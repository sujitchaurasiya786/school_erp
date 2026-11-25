"""
Calendar and Attendance Services.
Provides centralized operational calendar resolution for Attendance and Academics.
"""
import calendar
from datetime import date, datetime, timedelta
import math
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.attendance.models import (
    Holiday, HolidayType, SaturdayRule, SpecialDayOverride,
    AttendanceSession, AttendanceSessionStatus,
    StudentDailyAttendance, AttendanceCorrectionRequest, StaffDailyAttendance, AttendanceStatus,
    StaffApprovalStatus, CheckInMethod,
    WorkingDayConfig, WEEKDAY_CHOICES
)
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from common.exceptions import BusinessConflictError, ResourceNotFoundError


class CalendarService:
    """
    Centralized calendar resolution engine for School ERP.
    Single source of truth for working days, holidays, weekend policies,
    special working-day overrides, and timetable scheduling days.
    """

    @classmethod
    def _coerce_date(cls, date_val):
        if isinstance(date_val, datetime):
            return date_val.date()
        if isinstance(date_val, date):
            return date_val
        if isinstance(date_val, str):
            try:
                return datetime.strptime(date_val.strip(), "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError({'date': _(f"Invalid date format '{date_val}'. Expected YYYY-MM-DD.")})
        raise ValidationError({'date': _("Date must be a date object or YYYY-MM-DD string.")})

    @classmethod
    def _resolve_academic_year(cls, academic_year):
        from apps.school_config.models import AcademicYear
        if isinstance(academic_year, AcademicYear):
            return academic_year
        try:
            return AcademicYear.objects.get(pk=academic_year)
        except AcademicYear.DoesNotExist:
            raise ResourceNotFoundError(f"Academic Year with ID {academic_year} not found.")

    @classmethod
    def get_day_status(cls, academic_year, target_date):
        """
        Resolves the operational status of a specific date in the academic year.

        Precedence:
          1. SpecialDayOverride (can override holiday or weekend)
          2. Holiday (non-working)
          3. WorkingDayConfig (weekday status + Saturday policy)
          4. Legacy fallback (Mon–Sat working, Sun off)

        Returns dict:
          - is_working: bool
          - reason: str
          - type: 'REGULAR' | 'HOLIDAY' | 'WEEKEND_OFF' | 'SPECIAL_WORKING' | 'EMERGENCY_CLOSURE' | 'EVENT'
          - actual_date: date
          - timetable_weekday: int (1-7) or None
        """
        ay = cls._resolve_academic_year(academic_year)
        t_date = cls._coerce_date(target_date)

        # Boundary check
        if ay.start_date and ay.end_date:
            if not (ay.start_date <= t_date <= ay.end_date):
                return {
                    'is_working': False,
                    'reason': f"Date {t_date} is outside Academic Year '{ay.name}' ({ay.start_date} to {ay.end_date}).",
                    'type': 'OUTSIDE_ACADEMIC_YEAR',
                    'actual_date': t_date,
                    'timetable_weekday': None
                }

        # 1. SpecialDayOverride
        override = SpecialDayOverride.objects.filter(academic_year=ay, date=t_date, is_active=True).first()
        if override:
            if override.is_working:
                return {
                    'is_working': True,
                    'reason': override.reason,
                    'type': 'SPECIAL_WORKING',
                    'actual_date': t_date,
                    'timetable_weekday': override.timetable_weekday
                }
            else:
                return {
                    'is_working': False,
                    'reason': override.reason,
                    'type': override.override_type,
                    'actual_date': t_date,
                    'timetable_weekday': None
                }

        # 2. Holiday
        holiday = Holiday.objects.filter(academic_year=ay, date=t_date, is_active=True).first()
        if holiday:
            return {
                'is_working': False,
                'reason': holiday.title,
                'type': 'HOLIDAY',
                'actual_date': t_date,
                'timetable_weekday': None
            }

        # 3. WorkingDayConfig
        config = WorkingDayConfig.objects.filter(academic_year=ay, is_active=True).first()
        iso_weekday = t_date.isoweekday()  # 1=Mon, ..., 6=Sat, 7=Sun
        weekday_names = {1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday', 7: 'Sunday'}
        day_name = weekday_names.get(iso_weekday, 'Weekday')

        if not config:
            # 4. Legacy fallback (Mon–Sat working, Sunday off)
            if iso_weekday <= 6:
                return {
                    'is_working': True,
                    'reason': f"Regular {day_name} (Legacy Schedule)",
                    'type': 'REGULAR',
                    'actual_date': t_date,
                    'timetable_weekday': iso_weekday
                }
            else:
                return {
                    'is_working': False,
                    'reason': "Sunday Weekly Off (Legacy Schedule)",
                    'type': 'WEEKEND_OFF',
                    'actual_date': t_date,
                    'timetable_weekday': None
                }

        # Resolve according to WorkingDayConfig
        if iso_weekday == 1:
            is_work = config.monday
        elif iso_weekday == 2:
            is_work = config.tuesday
        elif iso_weekday == 3:
            is_work = config.wednesday
        elif iso_weekday == 4:
            is_work = config.thursday
        elif iso_weekday == 5:
            is_work = config.friday
        elif iso_weekday == 7:
            is_work = config.sunday
        elif iso_weekday == 6:
            # Saturday policy
            if config.saturday_rule == SaturdayRule.ALL_WORKING:
                is_work = True
                sat_reason = "Saturday Working (All Saturdays Working Policy)"
            elif config.saturday_rule == SaturdayRule.ALL_OFF:
                is_work = False
                sat_reason = "Saturday Weekly Off (All Saturdays Off Policy)"
            elif config.saturday_rule == SaturdayRule.SECOND_FOURTH_OFF:
                saturday_number = (t_date.day - 1) // 7 + 1
                if saturday_number in (2, 4):
                    is_work = False
                    sat_reason = f"{saturday_number}{'nd' if saturday_number == 2 else 'th'} Saturday Off Policy"
                else:
                    suffix = 'st' if saturday_number == 1 else ('rd' if saturday_number == 3 else 'th')
                    is_work = True
                    sat_reason = f"{saturday_number}{suffix} Saturday Working Policy"
            else:
                is_work = True
                sat_reason = "Saturday Working"

            if is_work:
                return {
                    'is_working': True,
                    'reason': sat_reason,
                    'type': 'REGULAR',
                    'actual_date': t_date,
                    'timetable_weekday': 6
                }
            else:
                return {
                    'is_working': False,
                    'reason': sat_reason,
                    'type': 'WEEKEND_OFF',
                    'actual_date': t_date,
                    'timetable_weekday': None
                }

        if is_work:
            return {
                'is_working': True,
                'reason': f"Regular {day_name}",
                'type': 'REGULAR',
                'actual_date': t_date,
                'timetable_weekday': iso_weekday
            }
        else:
            return {
                'is_working': False,
                'reason': f"Weekly Off ({day_name})",
                'type': 'WEEKEND_OFF',
                'actual_date': t_date,
                'timetable_weekday': None
            }

    @classmethod
    def is_working_day(cls, academic_year, target_date):
        status = cls.get_day_status(academic_year, target_date)
        return bool(status.get('is_working', False))

    @classmethod
    def get_timetable_weekday(cls, academic_year, target_date):
        status = cls.get_day_status(academic_year, target_date)
        return status.get('timetable_weekday')

    @classmethod
    def validate_attendance_date(cls, academic_year, target_date):
        """
        Validates whether attendance can be marked on target_date.
        Raises ValidationError if date is a non-working day or holiday without override.
        """
        ay = cls._resolve_academic_year(academic_year)
        t_date = cls._coerce_date(target_date)
        status = cls.get_day_status(ay, t_date)
        if not status.get('is_working', False):
            raise ValidationError({
                'date': _(
                    f"Cannot mark attendance on {t_date} ({status.get('reason')}). "
                    f"Attendance can only be recorded on active working days or authorized special working days."
                )
            })
        return True

    @classmethod
    def get_active_timetable_days(cls, academic_year):
        """
        Returns list of weekday numbers (1=Monday ... 7=Sunday) that are active for the academic year.
        Used by timetable matrix and slot conflict validation.
        """
        ay = cls._resolve_academic_year(academic_year)
        config = WorkingDayConfig.objects.filter(academic_year=ay, is_active=True).first()
        if not config:
            return [1, 2, 3, 4, 5, 6]

        active = []
        if config.monday:
            active.append(1)
        if config.tuesday:
            active.append(2)
        if config.wednesday:
            active.append(3)
        if config.thursday:
            active.append(4)
        if config.friday:
            active.append(5)
        if config.saturday_rule != SaturdayRule.ALL_OFF:
            active.append(6)
        if config.sunday:
            active.append(7)
        return active

    @classmethod
    def get_working_days_in_range(cls, academic_year, start_date, end_date):
        """
        Returns list of operational working date objects in [start_date, end_date].
        Batch-optimized: fetches overrides, holidays, and config in 3 queries total.
        """
        ay = cls._resolve_academic_year(academic_year)
        s_date = cls._coerce_date(start_date)
        e_date = cls._coerce_date(end_date)
        if s_date > e_date:
            raise ValidationError({'end_date': _("End date must be greater than or equal to start date.")})

        # Batch load
        overrides = {
            o.date: o for o in SpecialDayOverride.objects.filter(
                academic_year=ay, date__range=[s_date, e_date], is_active=True
            )
        }
        holiday_dates = set(
            Holiday.objects.filter(
                academic_year=ay, date__range=[s_date, e_date], is_active=True
            ).values_list('date', flat=True)
        )
        config = WorkingDayConfig.objects.filter(academic_year=ay, is_active=True).first()

        working_days = []
        cur = s_date
        one_day = timedelta(days=1)

        while cur <= e_date:
            # Boundary check
            if ay.start_date and ay.end_date and not (ay.start_date <= cur <= ay.end_date):
                cur += one_day
                continue

            # 1. Override
            if cur in overrides:
                if overrides[cur].is_working:
                    working_days.append(cur)
                cur += one_day
                continue

            # 2. Holiday
            if cur in holiday_dates:
                cur += one_day
                continue

            # 3. Config
            iso_w = cur.isoweekday()
            if not config:
                if iso_w <= 6:
                    working_days.append(cur)
            else:
                is_w = False
                if iso_w == 1:
                    is_w = config.monday
                elif iso_w == 2:
                    is_w = config.tuesday
                elif iso_w == 3:
                    is_w = config.wednesday
                elif iso_w == 4:
                    is_w = config.thursday
                elif iso_w == 5:
                    is_w = config.friday
                elif iso_w == 7:
                    is_w = config.sunday
                elif iso_w == 6:
                    if config.saturday_rule == SaturdayRule.ALL_WORKING:
                        is_w = True
                    elif config.saturday_rule == SaturdayRule.ALL_OFF:
                        is_w = False
                    elif config.saturday_rule == SaturdayRule.SECOND_FOURTH_OFF:
                        sat_idx = (cur.day - 1) // 7 + 1
                        is_w = sat_idx not in (2, 4)

                if is_w:
                    working_days.append(cur)

            cur += one_day

        return working_days

    @classmethod
    @transaction.atomic
    def create_vacation_break(
        cls,
        academic_year,
        title,
        start_date,
        end_date,
        holiday_type=HolidayType.VACATION,
        description="",
        is_national_holiday=False,
        actor=None,
        request=None
    ):
        """
        Creates individual Holiday records for each date in [start_date, end_date].
        Atomic: if any date is a duplicate or invalid, the entire operation is rolled back.
        """
        ay = cls._resolve_academic_year(academic_year)
        s_date = cls._coerce_date(start_date)
        e_date = cls._coerce_date(end_date)

        if s_date > e_date:
            raise ValidationError({'end_date': _("Vacation end date must be on or after start date.")})

        if ay.start_date and ay.end_date:
            if not (ay.start_date <= s_date <= ay.end_date and ay.start_date <= e_date <= ay.end_date):
                raise ValidationError({
                    'date': _(f"Vacation break dates ({s_date} to {e_date}) fall outside Academic Year '{ay.name}' ({ay.start_date} to {ay.end_date}).")
                })

        created_holidays = []
        cur = s_date
        one_day = timedelta(days=1)

        while cur <= e_date:
            if Holiday.objects.filter(academic_year=ay, date=cur).exists():
                raise BusinessConflictError(
                    f"A holiday already exists on {cur} for Academic Year '{ay.name}'. Cannot create vacation break."
                )
            h = Holiday(
                academic_year=ay,
                title=f"{title} ({cur.strftime('%b %d')})" if s_date != e_date else title,
                date=cur,
                holiday_type=holiday_type,
                is_national_holiday=is_national_holiday,
                description=description
            )
            h.clean()
            h.save()
            created_holidays.append(h)
            cur += one_day

        AuditService.log(
            action=AuditAction.CREATE,
            module="attendance",
            entity_type="HolidayBreak",
            entity_id=str(ay.pk),
            actor=actor,
            reason=f"Created vacation break '{title}' from {s_date} to {e_date} ({len(created_holidays)} days)",
            changes={"title": title, "start_date": str(s_date), "end_date": str(e_date), "count": len(created_holidays)},
            request=request
        )
        return created_holidays


class StudentDailyAttendanceService:
    """
    Business service encapsulating student attendance recording, roster lookups,
    scoping validations, and correction workflows.
    """

    @classmethod
    def validate_user_attendance_scope(cls, user, class_name, section_name):
        """
        Validates if user is authorized to mark or view attendance for the specified class & section.
        System Administrator (is_superuser=True) always has complete access.
        Normal teachers must be assigned as class_teacher to the section or hold a matching scoped RBAC role.
        """
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'is_superuser', False):
            return True

        from apps.rbac.models import UserRoleAssignment, ScopeType
        from apps.academics.models import SchoolClass, Section

        # 1. Check Global permission
        global_assigns = UserRoleAssignment.objects.filter(
            user=user,
            scope_type=ScopeType.GLOBAL,
            is_active=True,
            role__permissions__codename='attendance.record.mark'
        )
        if global_assigns.exists():
            return True

        # Resolve class & section models
        c_obj = SchoolClass.objects.filter(name__iexact=str(class_name or '').strip()).first()
        sec_obj = Section.objects.filter(
            school_class__name__iexact=str(class_name or '').strip(),
            name__iexact=str(section_name or '').strip()
        ).first()

        # 2. Check CLASS scope by ID or name
        class_ids = [str(class_name or '').strip()]
        if c_obj:
            class_ids.append(str(c_obj.pk))
        if UserRoleAssignment.objects.filter(
            user=user,
            scope_type=ScopeType.CLASS,
            scope_id__in=class_ids,
            is_active=True,
            role__permissions__codename='attendance.record.mark'
        ).exists():
            return True

        # 3. Check SECTION scope by ID or name
        sec_ids = [str(section_name or '').strip()]
        if sec_obj:
            sec_ids.append(str(sec_obj.pk))
            sec_ids.append(f"{class_name}-{section_name}")
        if UserRoleAssignment.objects.filter(
            user=user,
            scope_type=ScopeType.SECTION,
            scope_id__in=sec_ids,
            is_active=True,
            role__permissions__codename='attendance.record.mark'
        ).exists():
            return True

        # 4. Check section's assigned class teacher
        if sec_obj and sec_obj.class_teacher_id == user.pk:
            return True

        return False

    @classmethod
    def get_rule_config(cls, academic_year):
        ay = CalendarService._resolve_academic_year(academic_year)
        cfg = WorkingDayConfig.objects.filter(academic_year=ay, is_active=True).first()
        return {
            'shortage_threshold': float(cfg.shortage_threshold) if cfg else 75.0,
            'allow_late_status': bool(cfg.allow_late_status) if cfg else False,
        }

    @classmethod
    def get_section_roster_with_attendance(
        cls,
        academic_year,
        class_name,
        section_name,
        target_date
    ):
        """
        Retrieves enrolled students for the given section along with their attendance,
        running attendance percentage, session lock status, and pending correction request status.
        """
        ay = CalendarService._resolve_academic_year(academic_year)
        t_date = CalendarService._coerce_date(target_date)
        cal_status = CalendarService.get_day_status(ay, t_date)
        rules = cls.get_rule_config(ay)

        clean_class = str(class_name or '').strip()
        clean_section = str(section_name or '').strip()

        from apps.students.models import ClassEnrollment
        enrollments = list(
            ClassEnrollment.objects.filter(
                academic_year=ay,
                class_name__iexact=clean_class,
                section_name__iexact=clean_section,
                is_active=True
            )
            .select_related('student', 'student__primary_guardian')
            .order_by('roll_number', 'student__first_name', 'student__last_name')
        )

        # Fetch session for this section & date
        session = AttendanceSession.objects.filter(
            academic_year=ay,
            class_name__iexact=clean_class,
            section_name__iexact=clean_section,
            date=t_date
        ).select_related('marked_by', 'submitted_by').first()

        # Map existing attendance records for target_date
        att_qs = list(StudentDailyAttendance.objects.filter(
            academic_year=ay,
            class_name__iexact=clean_class,
            section_name__iexact=clean_section,
            date=t_date
        ).select_related('marked_by'))

        att_by_student = {att.student_id: att for att in att_qs}
        att_ids = [att.id for att in att_qs]

        # Running attendance % per student up to t_date in this academic year
        student_ids = [ce.student_id for ce in enrollments]
        hist_qs = StudentDailyAttendance.objects.filter(
            academic_year=ay,
            student_id__in=student_ids,
            date__lte=t_date
        ).values('student_id', 'status')
        hist_stats = {}
        for h in hist_qs:
            st = hist_stats.setdefault(h['student_id'], {'total': 0, 'eff': 0.0})
            st['total'] += 1
            if h['status'] in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
                st['eff'] += 1.0
            elif h['status'] == AttendanceStatus.HALF_DAY:
                st['eff'] += 0.5

        # Map pending correction requests
        corrections = {
            cr.attendance_id: cr
            for cr in AttendanceCorrectionRequest.objects.filter(
                attendance_id__in=att_ids,
                is_approved__isnull=True
            ).select_related('requested_by')
        }

        counts = {
            'total': len(enrollments),
            'present': 0,
            'absent': 0,
            'half_day': 0,
            'leave': 0,
            'late': 0,
            'marked': len(att_by_student),
            'unmarked': max(0, len(enrollments) - len(att_by_student))
        }

        roster = []
        for ce in enrollments:
            std = ce.student
            existing = att_by_student.get(std.id)
            current_status = existing.status if existing else AttendanceStatus.PRESENT
            if existing:
                if existing.status == AttendanceStatus.PRESENT:
                    counts['present'] += 1
                elif existing.status == AttendanceStatus.ABSENT:
                    counts['absent'] += 1
                elif existing.status == AttendanceStatus.HALF_DAY:
                    counts['half_day'] += 1
                elif existing.status == AttendanceStatus.LEAVE:
                    counts['leave'] += 1
                elif existing.status == AttendanceStatus.LATE:
                    counts['late'] += 1

            st_hist = hist_stats.get(std.id, {'total': 0, 'eff': 0.0})
            att_pct = round((st_hist['eff'] / st_hist['total'] * 100), 1) if st_hist['total'] > 0 else 100.0

            roster.append({
                'student': std,
                'enrollment': ce,
                'roll_number': ce.roll_number,
                'attendance': existing,
                'current_status': current_status,
                'remarks': existing.remarks if existing else '',
                'attendance_percentage': att_pct,
                'pending_correction': corrections.get(existing.id) if existing else None
            })

        marked_by_user = session.marked_by if (session and session.marked_by) else next((att.marked_by for att in att_qs if att.marked_by), None)
        session_status = session.status if session else ('SUBMITTED' if len(att_by_student) > 0 and not session else 'NONE')

        return {
            'academic_year': ay,
            'class_name': class_name,
            'section_name': section_name,
            'date': t_date,
            'calendar_status': cal_status,
            'is_working': cal_status.get('is_working', False),
            'roster': roster,
            'counts': counts,
            'is_already_marked': len(att_by_student) > 0,
            'session': session,
            'session_status': session_status,
            'is_locked': bool(session and session.is_locked),
            'allow_late_status': rules['allow_late_status'],
            'shortage_threshold': rules['shortage_threshold'],
            'marked_by': marked_by_user,
            'total_students': len(enrollments),
        }

    @classmethod
    @transaction.atomic
    def mark_attendance(
        cls,
        student_id,
        academic_year_id,
        attendance_date,
        class_name,
        section_name,
        status=AttendanceStatus.PRESENT,
        remarks="",
        actor=None,
        request=None,
        _session=None
    ):
        from apps.students.models import Student, ClassEnrollment
        try:
            student = Student.objects.get(pk=student_id)
        except Student.DoesNotExist:
            raise ResourceNotFoundError(f"Student with ID {student_id} not found.")

        t_date = CalendarService._coerce_date(attendance_date)
        CalendarService.validate_attendance_date(academic_year_id, t_date)

        clean_class = str(class_name or '').strip()
        clean_section = str(section_name or '').strip()

        # Validate that student is enrolled in this section/class and bind exact ClassEnrollment
        enrollment = ClassEnrollment.objects.filter(
            student=student,
            academic_year_id=academic_year_id,
            class_name__iexact=clean_class,
            section_name__iexact=clean_section,
            is_active=True
        ).first()
        if not enrollment:
            raise ValidationError({
                'student': _(f"Student '{student.admission_number}' ({student.full_name}) is not enrolled in Class '{clean_class}' Section '{clean_section}' for this academic year.")
            })

        if status not in AttendanceStatus.values:
            raise ValidationError({'status': _(f"Invalid attendance status '{status}'.")})

        if status == AttendanceStatus.LATE:
            rules = cls.get_rule_config(academic_year_id)
            if not rules['allow_late_status']:
                raise ValidationError({'status': _("Late attendance status is disabled by school attendance policy.")})

        session = _session
        if session is None:
            session = AttendanceSession.objects.filter(
                academic_year_id=academic_year_id,
                class_name__iexact=clean_class,
                section_name__iexact=clean_section,
                date=t_date
            ).first()

        if session and session.is_locked:
            raise BusinessConflictError(
                "Attendance for this class, section, and date is already submitted and locked. "
                "Use the Correction Request workflow to modify a submitted record."
            )

        if session is None:
            session = AttendanceSession.objects.create(
                academic_year_id=academic_year_id,
                class_name=clean_class,
                section_name=clean_section,
                date=t_date,
                status=AttendanceSessionStatus.DRAFT,
                marked_by=actor
            )

        record, created = StudentDailyAttendance.objects.update_or_create(
            student=student,
            date=t_date,
            defaults={
                'session': session,
                'enrollment': enrollment,
                'academic_year_id': academic_year_id,
                'class_name': clean_class,
                'section_name': clean_section,
                'status': status,
                'remarks': str(remarks or '').strip(),
                'marked_by': actor
            }
        )

        AuditService.log(
            action=AuditAction.CREATE if created else AuditAction.UPDATE,
            module="attendance",
            entity_type="StudentDailyAttendance",
            entity_id=str(record.pk),
            actor=actor,
            reason=f"{'Marked' if created else 'Updated'} attendance for {student.admission_number} on {t_date}: {status}",
            changes={
                "student": student.admission_number,
                "enrollment_id": enrollment.pk,
                "session_id": session.pk,
                "date": str(t_date),
                "status": status,
                "class_name": clean_class,
                "section_name": clean_section,
                "remarks": remarks
            },
            request=request
        )
        return record

    @classmethod
    @transaction.atomic
    def mark_bulk_attendance(
        cls,
        records,
        actor=None,
        request=None,
        submit=False
    ):
        """
        Marks attendance for multiple students atomically with calendar validation,
        enrollment verification, and session locking checks.
        """
        if not records:
            raise ValidationError({'records': _("Attendance records list cannot be empty.")})

        first_item = records[0]
        ay_id = first_item.get('academic_year_id')
        att_date = CalendarService._coerce_date(first_item.get('date'))
        clean_class = str(first_item.get('class_name', '')).strip()
        clean_section = str(first_item.get('section_name', '')).strip()

        CalendarService.validate_attendance_date(ay_id, att_date)

        session, _ = AttendanceSession.objects.select_for_update().get_or_create(
            academic_year_id=ay_id,
            class_name=clean_class,
            section_name=clean_section,
            date=att_date,
            defaults={
                'status': AttendanceSessionStatus.DRAFT,
                'marked_by': actor
            }
        )

        if session.is_locked:
            raise BusinessConflictError(
                "Attendance for this class, section, and date is already submitted and locked. "
                "Use the Correction Request workflow to update records."
            )

        processed_ids = []
        for item in records:
            student_id = item.get('student_id')
            item_ay = item.get('academic_year_id') or ay_id
            item_date = item.get('date') or att_date
            class_name = item.get('class_name', clean_class)
            section_name = item.get('section_name', clean_section)
            status_val = item.get('status', AttendanceStatus.PRESENT)
            remarks = item.get('remarks', '')

            rec = cls.mark_attendance(
                student_id=student_id,
                academic_year_id=item_ay,
                attendance_date=item_date,
                class_name=class_name,
                section_name=section_name,
                status=status_val,
                remarks=remarks,
                actor=actor,
                request=request,
                _session=session
            )
            processed_ids.append(rec.pk)

        if submit:
            session.status = AttendanceSessionStatus.SUBMITTED
            session.submitted_by = actor
            session.submitted_at = timezone.now()
            session.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])

        AuditService.log(
            action=AuditAction.APPROVE if submit else AuditAction.UPDATE,
            module="attendance",
            entity_type="AttendanceSession",
            entity_id=str(session.pk),
            actor=actor,
            reason=f"{'Submitted and locked' if submit else 'Saved draft'} attendance for {clean_class}-{clean_section} on {att_date} ({len(processed_ids)} students)",
            changes={"session_id": session.pk, "status": session.status, "count": len(processed_ids), "date": str(att_date)},
            request=request
        )
        return processed_ids

    @classmethod
    @transaction.atomic
    def submit_session(
        cls,
        academic_year_id,
        class_name,
        section_name,
        attendance_date,
        records=None,
        actor=None,
        request=None
    ):
        """
        Finalizes and locks an AttendanceSession (Draft -> Submitted).
        Optionally saves `records` before locking. Prevents duplicate submission.
        """
        t_date = CalendarService._coerce_date(attendance_date)
        CalendarService.validate_attendance_date(academic_year_id, t_date)
        clean_class = str(class_name or '').strip()
        clean_section = str(section_name or '').strip()

        existing_session = AttendanceSession.objects.select_for_update().filter(
            academic_year_id=academic_year_id,
            class_name__iexact=clean_class,
            section_name__iexact=clean_section,
            date=t_date
        ).first()

        if existing_session and existing_session.is_locked:
            raise BusinessConflictError("Attendance for this class, section, and date has already been submitted and locked.")

        if records:
            cls.mark_bulk_attendance(records=records, actor=actor, request=request, submit=True)
            session = AttendanceSession.objects.get(
                academic_year_id=academic_year_id,
                class_name__iexact=clean_class,
                section_name__iexact=clean_section,
                date=t_date
            )
            return session

        if not existing_session:
            raise ValidationError({'session': _("Cannot submit an empty session. Please mark student attendance first.")})

        from apps.students.models import ClassEnrollment
        enrolled_count = ClassEnrollment.objects.filter(
            academic_year_id=academic_year_id,
            class_name__iexact=clean_class,
            section_name__iexact=clean_section,
            is_active=True
        ).count()
        marked_count = existing_session.records.count()
        if enrolled_count > 0 and marked_count < enrolled_count:
            raise ValidationError({
                'records': _(f"All enrolled students must be marked before submitting ({marked_count}/{enrolled_count} marked).")
            })

        existing_session.status = AttendanceSessionStatus.SUBMITTED
        existing_session.submitted_by = actor
        existing_session.submitted_at = timezone.now()
        existing_session.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])

        AuditService.log(
            action=AuditAction.APPROVE,
            module="attendance",
            entity_type="AttendanceSession",
            entity_id=str(existing_session.pk),
            actor=actor,
            reason=f"Submitted and locked attendance session {clean_class}-{clean_section} on {t_date}",
            changes={"session_id": existing_session.pk, "status": existing_session.status},
            request=request
        )
        return existing_session

    @classmethod
    @transaction.atomic
    def submit_correction_request(
        cls,
        attendance_id,
        new_status,
        reason,
        user,
        request=None
    ):
        """
        Submits an AttendanceCorrectionRequest for a previously saved attendance record.
        """
        try:
            attendance = StudentDailyAttendance.objects.select_related('student', 'academic_year').get(pk=attendance_id)
        except StudentDailyAttendance.DoesNotExist:
            raise ResourceNotFoundError(f"Attendance record with ID {attendance_id} not found.")

        if new_status not in AttendanceStatus.values:
            raise ValidationError({'new_status': _(f"Invalid attendance status '{new_status}'.")})

        clean_reason = str(reason or '').strip()
        if not clean_reason:
            raise ValidationError({'reason': _("A valid reason is required for requesting an attendance correction.")})

        # Check if an unresolved request already exists
        if AttendanceCorrectionRequest.objects.filter(attendance=attendance, is_approved__isnull=True).exists():
            raise BusinessConflictError("A pending correction request already exists for this attendance record.")

        corr_req = AttendanceCorrectionRequest.objects.create(
            attendance=attendance,
            requested_by=user,
            new_status=new_status,
            reason=clean_reason
        )

        AuditService.log(
            action=AuditAction.CREATE,
            module="attendance",
            entity_type="AttendanceCorrectionRequest",
            entity_id=str(corr_req.pk),
            actor=user,
            reason=f"Submitted attendance correction request for {attendance.student.admission_number} ({attendance.status} -> {new_status})",
            changes={
                "attendance_id": str(attendance.pk),
                "student": attendance.student.admission_number,
                "current_status": attendance.status,
                "requested_status": new_status,
                "reason": clean_reason
            },
            request=request
        )
        return corr_req

    @classmethod
    @transaction.atomic
    def review_correction_request(
        cls,
        request_id,
        is_approved,
        review_notes,
        user,
        request=None
    ):
        """
        Reviews (approves or rejects) an AttendanceCorrectionRequest.
        If approved, updates the target StudentDailyAttendance record.
        """
        try:
            corr_req = AttendanceCorrectionRequest.objects.select_related('attendance', 'attendance__student').get(pk=request_id)
        except AttendanceCorrectionRequest.DoesNotExist:
            raise ResourceNotFoundError(f"Attendance correction request with ID {request_id} not found.")

        if corr_req.is_approved is not None:
            raise BusinessConflictError("This attendance correction request has already been reviewed.")

        corr_req.is_approved = bool(is_approved)
        corr_req.reviewed_by = user
        corr_req.review_notes = str(review_notes or '').strip()
        corr_req.save()

        att = corr_req.attendance
        old_status = att.status

        if corr_req.is_approved:
            att.status = corr_req.new_status
            note = f" [Corrected to {corr_req.new_status} by {user.get_full_name() or user.username}]"
            if note not in att.remarks:
                att.remarks = f"{att.remarks}{note}".strip()
            att.save()

        AuditService.log(
            action=AuditAction.APPROVE if corr_req.is_approved else AuditAction.REJECT,
            module="attendance",
            entity_type="AttendanceCorrectionRequest",
            entity_id=str(corr_req.pk),
            actor=user,
            reason=f"{'Approved' if corr_req.is_approved else 'Rejected'} attendance correction request for {att.student.admission_number} ({old_status} -> {corr_req.new_status})",
            changes={
                "request_id": str(corr_req.pk),
                "is_approved": corr_req.is_approved,
                "old_status": old_status,
                "new_status": att.status,
                "review_notes": corr_req.review_notes
            },
            request=request
        )
        return corr_req


class StaffDailyAttendanceService:
    """
    Service layer for recording and inspecting daily staff attendance.
    Supports individual and bulk marking with operational calendar resolution.
    """

    @classmethod
    def get_staff_roster_with_attendance(cls, target_date, department_id=None):
        from apps.identity.models import User, UserType
        from apps.school_config.models import AcademicYear

        t_date = CalendarService._coerce_date(target_date)
        current_ay = AcademicYear.objects.filter(is_current=True).first()
        cal_status = CalendarService.get_day_status(current_ay, t_date) if current_ay else {'is_working': True, 'reason': 'Operational'}

        staff_qs = User.objects.filter(
            is_active=True,
            user_type__in=[UserType.TEACHER, UserType.STAFF, UserType.PRINCIPAL, UserType.ADMIN, UserType.ACCOUNTANT]
        ).select_related('staff_profile', 'staff_profile__department', 'staff_profile__designation').order_by('first_name', 'last_name', 'username')

        if department_id:
            staff_qs = staff_qs.filter(staff_profile__department_id=department_id)

        staff_list = list(staff_qs)
        existing_records = {
            rec.user_id: rec
            for rec in StaffDailyAttendance.objects.filter(date=t_date).select_related('marked_by')
        }

        counts = {
            'total': len(staff_list),
            'present': 0,
            'absent': 0,
            'half_day': 0,
            'leave': 0,
            'marked': len(existing_records),
            'unmarked': max(0, len(staff_list) - len(existing_records))
        }

        roster = []
        for u in staff_list:
            existing = existing_records.get(u.id)
            status_val = existing.status if existing else AttendanceStatus.PRESENT
            if existing:
                if existing.status == AttendanceStatus.PRESENT:
                    counts['present'] += 1
                elif existing.status == AttendanceStatus.ABSENT:
                    counts['absent'] += 1
                elif existing.status == AttendanceStatus.HALF_DAY:
                    counts['half_day'] += 1
                elif existing.status == AttendanceStatus.LEAVE:
                    counts['leave'] += 1

            roster.append({
                'user': u,
                'staff_profile': getattr(u, 'staff_profile', None),
                'attendance': existing,
                'status': status_val,
                'remarks': existing.remarks if existing else ''
            })

        marked_by_user = next((r.marked_by for r in existing_records.values() if r.marked_by), None)

        return {
            'date': t_date,
            'calendar_status': cal_status,
            'is_working': cal_status.get('is_working', False),
            'roster': roster,
            'counts': counts,
            'is_already_marked': len(existing_records) > 0,
            'marked_by': marked_by_user,
            'total_staff': len(staff_list)
        }

    @classmethod
    @transaction.atomic
    def mark_staff_attendance(
        cls,
        user_id,
        attendance_date,
        status=AttendanceStatus.PRESENT,
        remarks="",
        actor=None,
        request=None
    ):
        from apps.identity.models import User
        from apps.school_config.models import AcademicYear
        try:
            target_user = User.objects.select_related('staff_profile').get(pk=user_id)
        except User.DoesNotExist:
            raise ResourceNotFoundError(f"Staff user with ID {user_id} not found.")

        t_date = CalendarService._coerce_date(attendance_date)
        current_ay = AcademicYear.objects.filter(is_current=True).first()
        if current_ay:
            CalendarService.validate_attendance_date(current_ay, t_date)

        if status not in AttendanceStatus.values:
            raise ValidationError({'status': _(f"Invalid attendance status '{status}'.")})

        profile = getattr(target_user, 'staff_profile', None)

        record, created = StaffDailyAttendance.objects.update_or_create(
            user=target_user,
            date=t_date,
            defaults={
                'staff_profile': profile,
                'status': status,
                'remarks': str(remarks or '').strip(),
                'marked_by': actor
            }
        )

        AuditService.log(
            action=AuditAction.CREATE if created else AuditAction.UPDATE,
            module="attendance",
            entity_type="StaffDailyAttendance",
            entity_id=str(record.pk),
            actor=actor,
            reason=f"{'Marked' if created else 'Updated'} staff attendance for {target_user.username} on {t_date}: {status}",
            changes={
                "user": target_user.username,
                "date": str(t_date),
                "status": status,
                "remarks": remarks
            },
            request=request
        )
        return record

    @classmethod
    @transaction.atomic
    def mark_bulk_staff_attendance(
        cls,
        records,
        attendance_date,
        actor=None,
        request=None
    ):
        if not records:
            raise ValidationError({'records': _("Staff attendance records list cannot be empty.")})

        from apps.school_config.models import AcademicYear
        t_date = CalendarService._coerce_date(attendance_date)
        current_ay = AcademicYear.objects.filter(is_current=True).first()
        if current_ay:
            CalendarService.validate_attendance_date(current_ay, t_date)

        saved_ids = []
        for item in records:
            user_id = item.get('user_id')
            status_val = item.get('status', AttendanceStatus.PRESENT)
            remarks = item.get('remarks', '')

            rec = cls.mark_staff_attendance(
                user_id=user_id,
                attendance_date=t_date,
                status=status_val,
                remarks=remarks,
                actor=actor,
                request=request
            )
            saved_ids.append(rec.pk)

        AuditService.log(
            action=AuditAction.UPDATE,
            module="attendance",
            entity_type="StaffDailyAttendanceBulk",
            entity_id=f"staff-{t_date}",
            actor=actor,
            reason=f"Bulk staff attendance recorded for {len(saved_ids)} staff members on {t_date}",
            changes={"count": len(saved_ids), "date": str(t_date), "ids": saved_ids},
            request=request
        )
        return saved_ids

    @staticmethod
    def calculate_haversine_distance(lat1, lon1, lat2, lon2):
        """
        Calculates great-circle distance in meters between two lat/lon coordinates
        using Haversine formula.
        """
        try:
            R = 6371000.0  # Earth's radius in meters
            phi1 = math.radians(float(lat1))
            phi2 = math.radians(float(lat2))
            delta_phi = math.radians(float(lat2) - float(lat1))
            delta_lambda = math.radians(float(lon2) - float(lon1))

            a = math.sin(delta_phi / 2.0) ** 2 + \
                math.cos(phi1) * math.cos(phi2) * \
                math.sin(delta_lambda / 2.0) ** 2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return round(R * c, 1)
        except (ValueError, TypeError):
            return None

    @classmethod
    def get_self_attendance_status(cls, user, target_date=None):
        """
        Returns today's self-attendance state, timing boundaries, campus coords, and allowed radius.
        """
        from apps.school_config.models import AcademicYear, SchoolProfile
        today = CalendarService._coerce_date(target_date) if target_date else timezone.localdate()
        school_profile = SchoolProfile.get_singleton()
        current_ay = AcademicYear.objects.filter(is_current=True).first()
        cal_status = CalendarService.get_day_status(current_ay, today) if current_ay else {'is_working': True, 'reason': 'Operational'}

        record = StaffDailyAttendance.objects.filter(user=user, date=today).first()
        now_time = timezone.localtime().time()

        can_check_in = False
        can_check_out = False
        if not record:
            can_check_in = True
        elif not record.check_in_time:
            can_check_in = True
        elif record.check_in_time and not record.check_out_time:
            can_check_out = True

        return {
            'date': today,
            'user': user,
            'school_profile': school_profile,
            'calendar_status': cal_status,
            'is_working': cal_status.get('is_working', True),
            'record': record,
            'now_time': now_time,
            'can_check_in': can_check_in,
            'can_check_out': can_check_out,
            'is_checked_in': bool(record and record.check_in_time),
            'is_checked_out': bool(record and record.check_out_time),
        }

    @classmethod
    @transaction.atomic
    def record_self_attendance(
        cls,
        user,
        action_type,
        latitude=None,
        longitude=None,
        selfie_base64="",
        exception_reason="",
        actor=None,
        request=None
    ):
        """
        Handles teacher/staff self attendance punch (check-in / check-out) with camera snapshot,
        fixed geo-location radius verification, and timing checks.
        If outside campus radius, late, or early, requires reason and marks PENDING_APPROVAL.
        """
        from apps.school_config.models import AcademicYear, SchoolProfile
        today = timezone.localdate()
        now_time = timezone.localtime().time()
        school_profile = SchoolProfile.get_singleton()

        current_ay = AcademicYear.objects.filter(is_current=True).first()
        if current_ay:
            status = CalendarService.get_day_status(current_ay, today)
            if not status.get('is_working', True):
                if not str(exception_reason or '').strip():
                    raise ValidationError({
                        'exception_reason': _(f"Today is marked as non-working ({status.get('reason', 'Holiday')}). Please provide an explanation.")
                    })

        action_type = str(action_type or '').strip().upper()
        if action_type not in ('CHECK_IN', 'CHECK_OUT'):
            raise ValidationError({'action_type': _("Action must be either CHECK_IN or CHECK_OUT.")})

        # Geo-location radius calculation
        dist = None
        is_within_radius = True
        if latitude is not None and longitude is not None and school_profile.latitude and school_profile.longitude:
            dist = cls.calculate_haversine_distance(
                school_profile.latitude, school_profile.longitude,
                latitude, longitude
            )
            if dist is not None:
                is_within_radius = (dist <= school_profile.geo_radius_meters)
            else:
                is_within_radius = False
        else:
            is_within_radius = False

        # Timing checks
        is_late = False
        is_early = False
        if action_type == 'CHECK_IN':
            if school_profile.school_start_time and now_time > school_profile.school_start_time:
                is_late = True
        elif action_type == 'CHECK_OUT':
            if school_profile.school_end_time and now_time < school_profile.school_end_time:
                is_early = True

        # Check if exception approval is required
        needs_approval = (not is_within_radius) or is_late or is_early
        clean_reason = str(exception_reason or '').strip()
        if needs_approval and not clean_reason:
            flags = []
            if not is_within_radius:
                flags.append(f"outside campus radius ({dist or 'unknown'}m from campus, allowed {school_profile.geo_radius_meters}m)")
            if is_late:
                flags.append(f"late check-in (after {school_profile.school_start_time.strftime('%H:%M')})")
            if is_early:
                flags.append(f"early check-out (before {school_profile.school_end_time.strftime('%H:%M')})")
            raise ValidationError({
                'exception_reason': _(f"Reason required: You are {' and '.join(flags)}.")
            })

        app_status = StaffApprovalStatus.PENDING_APPROVAL if needs_approval else StaffApprovalStatus.AUTO_APPROVED

        # Load or create record
        profile = getattr(user, 'staff_profile', None)
        record, created = StaffDailyAttendance.objects.get_or_create(
            user=user,
            date=today,
            defaults={
                'staff_profile': profile,
                'status': AttendanceStatus.PRESENT,
                'marked_by': actor or user,
                'check_in_method': CheckInMethod.SELF_GEO,
                'approval_status': app_status,
            }
        )

        if action_type == 'CHECK_IN':
            if record.check_in_time and not created:
                raise ValidationError({'check_in_time': _("You have already checked in for today.")})
            record.check_in_time = now_time
            if selfie_base64:
                record.selfie_image = selfie_base64
            if latitude is not None:
                record.latitude = latitude
            if longitude is not None:
                record.longitude = longitude
            record.distance_meters = dist
            record.is_within_radius = is_within_radius
            record.is_late_check_in = is_late
            record.check_in_method = CheckInMethod.SELF_GEO
            record.status = AttendanceStatus.PRESENT
            if clean_reason:
                record.exception_reason = clean_reason
            record.approval_status = app_status
        elif action_type == 'CHECK_OUT':
            if not record.check_in_time:
                record.check_in_time = now_time
            record.check_out_time = now_time
            record.is_early_check_out = is_early
            if clean_reason:
                if record.exception_reason:
                    record.exception_reason = f"{record.exception_reason} | Checkout: {clean_reason}"
                else:
                    record.exception_reason = clean_reason
            if needs_approval and record.approval_status != StaffApprovalStatus.PENDING_APPROVAL:
                record.approval_status = StaffApprovalStatus.PENDING_APPROVAL

        record.save()

        AuditService.log(
            action=AuditAction.CREATE if created else AuditAction.UPDATE,
            module="attendance",
            entity_type="StaffDailyAttendance",
            entity_id=str(record.pk),
            actor=actor or user,
            reason=f"Self attendance {action_type} for {user.username} (Radius: {'Within' if is_within_radius else 'Outside'}, Status: {app_status})",
            changes={
                "action": action_type,
                "time": now_time.strftime("%H:%M:%S"),
                "distance_meters": dist,
                "within_radius": is_within_radius,
                "is_late": is_late,
                "is_early": is_early,
                "approval_status": record.approval_status,
                "reason": clean_reason
            },
            request=request
        )
        return record

    @classmethod
    @transaction.atomic
    def review_self_attendance(
        cls,
        attendance_id,
        action,
        review_notes="",
        reviewer=None,
        request=None
    ):
        """
        Admin/Principal reviews a staff self-attendance pending approval.
        action: 'APPROVE' or 'REJECT'
        """
        try:
            record = StaffDailyAttendance.objects.select_related('user').get(pk=attendance_id)
        except StaffDailyAttendance.DoesNotExist:
            raise ResourceNotFoundError(f"Staff attendance record with ID {attendance_id} not found.")

        act = str(action or '').strip().upper()
        if act not in ('APPROVE', 'REJECT'):
            raise ValidationError({'action': _("Review action must be either APPROVE or REJECT.")})

        if act == 'APPROVE':
            record.approval_status = StaffApprovalStatus.APPROVED
            record.status = AttendanceStatus.PRESENT
        else:
            record.approval_status = StaffApprovalStatus.REJECTED
            record.status = AttendanceStatus.ABSENT

        record.reviewed_by = reviewer
        record.review_notes = str(review_notes or '').strip()
        record.reviewed_at = timezone.now()
        record.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module="attendance",
            entity_type="StaffDailyAttendance",
            entity_id=str(record.pk),
            actor=reviewer,
            reason=f"{act} self-attendance for {record.user.username} on {record.date}",
            changes={
                "approval_status": record.approval_status,
                "status": record.status,
                "review_notes": record.review_notes
            },
            request=request
        )
        return record

    @classmethod
    def get_pending_self_attendances(cls):
        """Returns staff attendance records awaiting Admin/Principal review."""
        return StaffDailyAttendance.objects.filter(
            approval_status=StaffApprovalStatus.PENDING_APPROVAL
        ).select_related('user', 'staff_profile', 'staff_profile__department', 'staff_profile__designation').order_by('-date', '-created_at')


class AttendanceReportService:
    """
    Analytics & Reporting Service for Student & Staff Attendance.
    Generates class-wise summaries, shortage reports (<75%), and exports to CSV.
    """

    @classmethod
    def get_student_attendance_summary(
        cls,
        academic_year,
        class_name,
        section_name,
        start_date,
        end_date,
        threshold=None
    ):
        ay = CalendarService._resolve_academic_year(academic_year)
        s_date = CalendarService._coerce_date(start_date)
        e_date = CalendarService._coerce_date(end_date)

        if threshold is None:
            threshold = StudentDailyAttendanceService.get_rule_config(ay)['shortage_threshold']

        working_dates = CalendarService.get_working_days_in_range(ay, s_date, e_date)
        total_working_days = len(working_dates)

        from apps.students.models import ClassEnrollment
        enrollments = list(
            ClassEnrollment.objects.filter(
                academic_year=ay,
                class_name__iexact=str(class_name or '').strip(),
                section_name__iexact=str(section_name or '').strip(),
                is_active=True
            )
            .select_related('student', 'student__primary_guardian')
            .order_by('roll_number', 'student__first_name')
        )

        records = (
            StudentDailyAttendance.objects.filter(
                academic_year=ay,
                class_name__iexact=str(class_name or '').strip(),
                section_name__iexact=str(section_name or '').strip(),
                date__range=[s_date, e_date]
            )
        )

        att_map = {}
        for r in records:
            att_map.setdefault(r.student_id, []).append(r)

        rows = []
        total_pct = 0.0
        shortage_count = 0

        for ce in enrollments:
            std = ce.student
            student_records = att_map.get(std.id, [])

            present_count = sum(1 for r in student_records if r.status == AttendanceStatus.PRESENT)
            late_count = sum(1 for r in student_records if r.status == AttendanceStatus.LATE)
            absent_count = sum(1 for r in student_records if r.status == AttendanceStatus.ABSENT)
            half_day_count = sum(1 for r in student_records if r.status == AttendanceStatus.HALF_DAY)
            leave_count = sum(1 for r in student_records if r.status == AttendanceStatus.LEAVE)

            effective_present = present_count + late_count + (0.5 * half_day_count)
            pct = round((effective_present / total_working_days * 100), 1) if total_working_days > 0 else 0.0

            is_shortage = pct < float(threshold)
            if is_shortage:
                shortage_count += 1
            total_pct += pct

            rows.append({
                'student': std,
                'roll_number': ce.roll_number,
                'admission_number': std.admission_number,
                'name': std.full_name,
                'present_days': present_count,
                'late_days': late_count,
                'half_days': half_day_count,
                'absent_days': absent_count,
                'leave_days': leave_count,
                'effective_attendance': effective_present,
                'total_working_days': total_working_days,
                'percentage': pct,
                'is_shortage': is_shortage,
            })

        avg_pct = round(total_pct / len(enrollments), 1) if enrollments else 0.0

        return {
            'academic_year': ay,
            'class_name': class_name,
            'section_name': section_name,
            'start_date': s_date,
            'end_date': e_date,
            'total_working_days': total_working_days,
            'total_students': len(enrollments),
            'shortage_count': shortage_count,
            'threshold': float(threshold),
            'average_percentage': avg_pct,
            'rows': rows,
        }

    @classmethod
    def get_daily_class_report(cls, academic_year, target_date, class_name=None, section_name=None):
        """
        Returns daily attendance breakdown and absent student list for a given date.
        """
        ay = CalendarService._resolve_academic_year(academic_year)
        t_date = CalendarService._coerce_date(target_date)
        qs = StudentDailyAttendance.objects.filter(academic_year=ay, date=t_date).select_related('student', 'enrollment', 'session')
        if class_name:
            qs = qs.filter(class_name__iexact=str(class_name).strip())
        if section_name:
            qs = qs.filter(section_name__iexact=str(section_name).strip())

        records = list(qs.order_by('class_name', 'section_name', 'enrollment__roll_number', 'student__first_name'))
        absent_students = [
            {
                'student_id': r.student_id,
                'admission_number': r.student.admission_number,
                'name': r.student.full_name,
                'roll_number': r.enrollment.roll_number if r.enrollment else None,
                'class_name': r.class_name,
                'section_name': r.section_name,
                'status': r.status,
                'remarks': r.remarks,
            }
            for r in records if r.status == AttendanceStatus.ABSENT
        ]
        summary = {
            'total_marked': len(records),
            'present': sum(1 for r in records if r.status == AttendanceStatus.PRESENT),
            'late': sum(1 for r in records if r.status == AttendanceStatus.LATE),
            'absent': len(absent_students),
            'half_day': sum(1 for r in records if r.status == AttendanceStatus.HALF_DAY),
            'leave': sum(1 for r in records if r.status == AttendanceStatus.LEAVE),
        }
        return {
            'academic_year': ay.name,
            'date': str(t_date),
            'summary': summary,
            'absent_students': absent_students,
            'records': [
                {
                    'student_id': r.student_id,
                    'admission_number': r.student.admission_number,
                    'name': r.student.full_name,
                    'roll_number': r.enrollment.roll_number if r.enrollment else None,
                    'class_name': r.class_name,
                    'section_name': r.section_name,
                    'status': r.status,
                    'remarks': r.remarks,
                    'session_status': r.session.status if r.session else 'SUBMITTED',
                }
                for r in records
            ],
        }

    @classmethod
    def get_monthly_attendance_report(cls, academic_year, class_name, section_name, year, month):
        """
        Returns a monthly attendance summary and day-by-day status matrix for a class/section.
        """
        ay = CalendarService._resolve_academic_year(academic_year)
        num_days = calendar.monthrange(int(year), int(month))[1]
        s_date = date(int(year), int(month), 1)
        e_date = date(int(year), int(month), num_days)
        summary = cls.get_student_attendance_summary(ay, class_name, section_name, s_date, e_date)

        records = StudentDailyAttendance.objects.filter(
            academic_year=ay,
            class_name__iexact=str(class_name or '').strip(),
            section_name__iexact=str(section_name or '').strip(),
            date__range=[s_date, e_date]
        )
        day_map = {}
        for r in records:
            day_map.setdefault(r.student_id, {})[str(r.date)] = r.status

        serialized_rows = []
        for row in summary['rows']:
            std = row['student']
            serialized_rows.append({
                'student_id': std.id,
                'admission_number': row['admission_number'],
                'roll_number': row['roll_number'],
                'name': row['name'],
                'present_days': row['present_days'],
                'late_days': row['late_days'],
                'half_days': row['half_days'],
                'absent_days': row['absent_days'],
                'leave_days': row['leave_days'],
                'effective_attendance': row['effective_attendance'],
                'total_working_days': row['total_working_days'],
                'percentage': row['percentage'],
                'is_shortage': row['is_shortage'],
                'daily_statuses': day_map.get(std.id, {}),
            })

        return {
            'academic_year': ay.name,
            'class_name': class_name,
            'section_name': section_name,
            'year': int(year),
            'month': int(month),
            'total_working_days': summary['total_working_days'],
            'threshold': summary['threshold'],
            'average_percentage': summary['average_percentage'],
            'shortage_count': summary['shortage_count'],
            'rows': serialized_rows,
        }

    @classmethod
    def get_student_attendance_history(cls, student, academic_year=None, start_date=None, end_date=None):
        """
        Returns a student's attendance history preserving historical class/section/enrollment
        across promotions.
        """
        qs = StudentDailyAttendance.objects.filter(student=student).select_related('academic_year', 'enrollment', 'session')
        if academic_year:
            ay = CalendarService._resolve_academic_year(academic_year)
            qs = qs.filter(academic_year=ay)
        if start_date:
            qs = qs.filter(date__gte=CalendarService._coerce_date(start_date))
        if end_date:
            qs = qs.filter(date__lte=CalendarService._coerce_date(end_date))

        records = list(qs.order_by('-date'))
        total = len(records)
        present = sum(1 for r in records if r.status == AttendanceStatus.PRESENT)
        late = sum(1 for r in records if r.status == AttendanceStatus.LATE)
        half_day = sum(1 for r in records if r.status == AttendanceStatus.HALF_DAY)
        absent = sum(1 for r in records if r.status == AttendanceStatus.ABSENT)
        leave = sum(1 for r in records if r.status == AttendanceStatus.LEAVE)
        effective = present + late + (0.5 * half_day)
        pct = round((effective / total * 100), 1) if total > 0 else 0.0

        return {
            'student_id': student.id,
            'admission_number': student.admission_number,
            'student_name': student.full_name,
            'summary': {
                'total_marked_days': total,
                'present': present,
                'late': late,
                'half_day': half_day,
                'absent': absent,
                'leave': leave,
                'percentage': pct,
            },
            'records': [
                {
                    'id': r.id,
                    'date': str(r.date),
                    'academic_year': r.academic_year.name,
                    'class_name': r.enrollment.class_name if r.enrollment else r.class_name,
                    'section_name': r.enrollment.section_name if r.enrollment else r.section_name,
                    'roll_number': r.enrollment.roll_number if r.enrollment else None,
                    'status': r.status,
                    'remarks': r.remarks,
                }
                for r in records
            ],
        }

    @classmethod
    def get_class_wise_summary(cls, academic_year, start_date, end_date):
        """
        Returns aggregated attendance percentage per active class & section for an academic year.
        """
        ay = CalendarService._resolve_academic_year(academic_year)
        s_date = CalendarService._coerce_date(start_date)
        e_date = CalendarService._coerce_date(end_date)

        from apps.academics.models import Section
        sections = Section.objects.filter(is_active=True).select_related('school_class').order_by('school_class__order', 'name')
        results = []
        for sec in sections:
            summary = cls.get_student_attendance_summary(ay, sec.school_class.name, sec.name, s_date, e_date)
            if summary['total_students'] > 0:
                results.append({
                    'class_name': sec.school_class.name,
                    'section_name': sec.name,
                    'total_students': summary['total_students'],
                    'average_percentage': summary['average_percentage'],
                    'shortage_count': summary['shortage_count'],
                })
        return {
            'academic_year': ay.name,
            'start_date': str(s_date),
            'end_date': str(e_date),
            'sections': results,
        }

    @classmethod
    def get_staff_attendance_summary(
        cls,
        start_date,
        end_date,
        department_id=None
    ):
        from apps.identity.models import User, UserType
        from apps.school_config.models import AcademicYear

        s_date = CalendarService._coerce_date(start_date)
        e_date = CalendarService._coerce_date(end_date)
        current_ay = AcademicYear.objects.filter(is_current=True).first()

        working_dates = CalendarService.get_working_days_in_range(current_ay, s_date, e_date) if current_ay else []
        total_working_days = len(working_dates) or max(1, (e_date - s_date).days + 1)

        staff_qs = User.objects.filter(
            is_active=True,
            user_type__in=[UserType.TEACHER, UserType.STAFF, UserType.PRINCIPAL, UserType.ADMIN, UserType.ACCOUNTANT]
        ).select_related('staff_profile', 'staff_profile__department', 'staff_profile__designation').order_by('first_name')

        if department_id:
            staff_qs = staff_qs.filter(staff_profile__department_id=department_id)

        staff_list = list(staff_qs)
        records = StaffDailyAttendance.objects.filter(date__range=[s_date, e_date])

        att_map = {}
        for r in records:
            att_map.setdefault(r.user_id, []).append(r)

        rows = []
        total_pct = 0.0
        for u in staff_list:
            u_records = att_map.get(u.id, [])
            present_count = sum(1 for r in u_records if r.status == AttendanceStatus.PRESENT)
            absent_count = sum(1 for r in u_records if r.status == AttendanceStatus.ABSENT)
            half_day_count = sum(1 for r in u_records if r.status == AttendanceStatus.HALF_DAY)
            leave_count = sum(1 for r in u_records if r.status == AttendanceStatus.LEAVE)

            effective_present = present_count + (0.5 * half_day_count)
            pct = round((effective_present / total_working_days * 100), 1) if total_working_days > 0 else 0.0
            total_pct += pct

            dept_name = u.staff_profile.department.name if hasattr(u, 'staff_profile') and u.staff_profile and u.staff_profile.department else '-'
            desig_title = u.staff_profile.designation.title if hasattr(u, 'staff_profile') and u.staff_profile and u.staff_profile.designation else '-'

            rows.append({
                'user': u,
                'name': u.get_full_name() or u.username,
                'department': dept_name,
                'designation': desig_title,
                'present_days': present_count,
                'half_days': half_day_count,
                'absent_days': absent_count,
                'leave_days': leave_count,
                'total_working_days': total_working_days,
                'percentage': pct,
            })

        avg_pct = round(total_pct / len(staff_list), 1) if staff_list else 0.0

        return {
            'start_date': s_date,
            'end_date': e_date,
            'total_working_days': total_working_days,
            'total_staff': len(staff_list),
            'average_percentage': avg_pct,
            'rows': rows,
        }

    @classmethod
    def export_csv(cls, filename, headers, rows):
        import csv
        import io
        from django.http import HttpResponse

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        for r in rows:
            writer.writerow(r)

        response = HttpResponse(output.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
