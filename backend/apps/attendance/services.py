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
    StudentDailyAttendance, AttendanceStatus, WorkingDayConfig, WEEKDAY_CHOICES
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
    Business service encapsulating student attendance recording and validations.
    """

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
        from apps.students.models import Student
        try:
            student = Student.objects.get(pk=student_id)
        except Student.DoesNotExist:
            raise ResourceNotFoundError(f"Student with ID {student_id} not found.")

        t_date = CalendarService._coerce_date(attendance_date)
        CalendarService.validate_attendance_date(academic_year_id, t_date)

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
        Marks attendance for multiple students atomically with calendar validation.
        """
        if not records:
            raise ValidationError({'records': _("Attendance records list cannot be empty.")})

        processed_ids = []
        for item in records:
            student_id = item.get('student_id')
            ay_id = item.get('academic_year_id')
            att_date = item.get('date')
            class_name = item.get('class_name', '')
            section_name = item.get('section_name', '')
            status_val = item.get('status', AttendanceStatus.PRESENT)
            remarks = item.get('remarks', '')

            rec = cls.mark_attendance(
                student_id=student_id,
                academic_year_id=ay_id,
                attendance_date=att_date,
                class_name=class_name,
                section_name=section_name,
                status=status_val,
                remarks=remarks,
                actor=actor,
                request=request
            )
            processed_ids.append(rec.pk)

        return processed_ids
