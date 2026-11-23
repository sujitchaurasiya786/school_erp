"""
Calendar and Attendance Services.
Provides centralized operational calendar resolution for Attendance and Academics.
"""
from datetime import date, datetime, timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.attendance.models import (
    Holiday, HolidayType, SaturdayRule, SpecialDayOverride,
    StudentDailyAttendance, AttendanceCorrectionRequest, StaffDailyAttendance, AttendanceStatus,
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
        if user.is_superuser:
            return True

        from apps.rbac.services import RBACService
        # Check global permission first
        if RBACService.has_permission(user, 'attendance.record.mark'):
            from apps.rbac.models import UserRoleAssignment, ScopeType
            global_assigns = UserRoleAssignment.objects.filter(
                user=user,
                scope_type=ScopeType.GLOBAL,
                is_active=True,
                role__permissions__codename='attendance.record.mark'
            )
            if global_assigns.exists():
                return True

            # Check CLASS scope
            if UserRoleAssignment.objects.filter(
                user=user,
                scope_type=ScopeType.CLASS,
                scope_value__iexact=str(class_name or '').strip(),
                is_active=True,
                role__permissions__codename='attendance.record.mark'
            ).exists():
                return True

            # Check SECTION scope
            if UserRoleAssignment.objects.filter(
                user=user,
                scope_type=ScopeType.SECTION,
                scope_value__iexact=str(section_name or '').strip(),
                is_active=True,
                role__permissions__codename='attendance.record.mark'
            ).exists():
                return True

        # Check section's assigned class teacher
        from apps.academics.models import Section
        if Section.objects.filter(
            school_class__name__iexact=str(class_name or '').strip(),
            name__iexact=str(section_name or '').strip(),
            class_teacher=user,
            is_active=True
        ).exists():
            return True

        return False

    @classmethod
    def get_section_roster_with_attendance(
        cls,
        academic_year,
        class_name,
        section_name,
        target_date
    ):
        """
        Retrieves enrolled students for the given section along with their attendance
        and pending correction request status for the specified target date.
        """
        ay = CalendarService._resolve_academic_year(academic_year)
        t_date = CalendarService._coerce_date(target_date)
        cal_status = CalendarService.get_day_status(ay, t_date)

        from apps.students.models import ClassEnrollment
        enrollments = (
            ClassEnrollment.objects.filter(
                academic_year=ay,
                class_name__iexact=str(class_name or '').strip(),
                section_name__iexact=str(section_name or '').strip(),
                is_active=True
            )
            .select_related('student', 'student__primary_guardian')
            .order_by('roll_number', 'student__first_name', 'student__last_name')
        )

        # Map existing attendance records
        att_qs = StudentDailyAttendance.objects.filter(
            academic_year=ay,
            class_name__iexact=str(class_name or '').strip(),
            section_name__iexact=str(section_name or '').strip(),
            date=t_date
        ).select_related('marked_by')

        att_by_student = {att.student_id: att for att in att_qs}
        att_ids = [att.id for att in att_qs]

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

            roster.append({
                'student': std,
                'enrollment': ce,
                'roll_number': ce.roll_number,
                'attendance': existing,
                'current_status': current_status,
                'remarks': existing.remarks if existing else '',
                'pending_correction': corrections.get(existing.id) if existing else None
            })

        marked_by_user = next((att.marked_by for att in att_qs if att.marked_by), None)

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
        request=None
    ):
        from apps.students.models import Student, ClassEnrollment
        try:
            student = Student.objects.get(pk=student_id)
        except Student.DoesNotExist:
            raise ResourceNotFoundError(f"Student with ID {student_id} not found.")

        t_date = CalendarService._coerce_date(attendance_date)
        CalendarService.validate_attendance_date(academic_year_id, t_date)

        # Validate that student is enrolled in this section/class
        is_enrolled = ClassEnrollment.objects.filter(
            student=student,
            academic_year_id=academic_year_id,
            class_name__iexact=str(class_name or '').strip(),
            section_name__iexact=str(section_name or '').strip(),
            is_active=True
        ).exists()
        if not is_enrolled:
            raise ValidationError({
                'student': _(f"Student '{student.admission_number}' ({student.full_name}) is not enrolled in Class '{class_name}' Section '{section_name}' for this academic year.")
            })

        if status not in AttendanceStatus.values:
            raise ValidationError({'status': _(f"Invalid attendance status '{status}'.")})

        record, created = StudentDailyAttendance.objects.update_or_create(
            student=student,
            date=t_date,
            defaults={
                'academic_year_id': academic_year_id,
                'class_name': str(class_name or '').strip(),
                'section_name': str(section_name or '').strip(),
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
                "date": str(t_date),
                "status": status,
                "class_name": class_name,
                "section_name": section_name,
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
        request=None
    ):
        """
        Marks attendance for multiple students atomically with calendar validation
        and enrollment verification.
        """
        if not records:
            raise ValidationError({'records': _("Attendance records list cannot be empty.")})

        # Pre-validate date & calendar on first record
        first_item = records[0]
        ay_id = first_item.get('academic_year_id')
        att_date = CalendarService._coerce_date(first_item.get('date'))
        CalendarService.validate_attendance_date(ay_id, att_date)

        processed_ids = []
        for item in records:
            student_id = item.get('student_id')
            item_ay = item.get('academic_year_id') or ay_id
            item_date = item.get('date') or att_date
            class_name = item.get('class_name', '')
            section_name = item.get('section_name', '')
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
                request=request
            )
            processed_ids.append(rec.pk)

        AuditService.log(
            action=AuditAction.UPDATE,
            module="attendance",
            entity_type="StudentDailyAttendanceBulk",
            entity_id=f"{first_item.get('class_name')}-{first_item.get('section_name')}-{att_date}",
            actor=actor,
            reason=f"Bulk attendance recorded for {len(processed_ids)} students on {att_date}",
            changes={"count": len(processed_ids), "date": str(att_date), "ids": processed_ids},
            request=request
        )
        return processed_ids

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
        threshold=75.0
    ):
        ay = CalendarService._resolve_academic_year(academic_year)
        s_date = CalendarService._coerce_date(start_date)
        e_date = CalendarService._coerce_date(end_date)

        working_dates = CalendarService.get_working_days_in_range(ay, s_date, e_date)
        total_working_days = len(working_dates)

        from apps.students.models import ClassEnrollment
        enrollments = (
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
            absent_count = sum(1 for r in student_records if r.status == AttendanceStatus.ABSENT)
            half_day_count = sum(1 for r in student_records if r.status == AttendanceStatus.HALF_DAY)
            leave_count = sum(1 for r in student_records if r.status == AttendanceStatus.LEAVE)

            effective_present = present_count + (0.5 * half_day_count)
            pct = round((effective_present / total_working_days * 100), 1) if total_working_days > 0 else 0.0

            is_shortage = pct < threshold
            if is_shortage:
                shortage_count += 1
            total_pct += pct

            rows.append({
                'student': std,
                'roll_number': ce.roll_number,
                'admission_number': std.admission_number,
                'name': std.full_name,
                'present_days': present_count,
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
            'threshold': threshold,
            'average_percentage': avg_pct,
            'rows': rows,
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
