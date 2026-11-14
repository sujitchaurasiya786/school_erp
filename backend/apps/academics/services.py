from datetime import date
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.academics.models import TimetableSlot
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.school_config.models import AcademicYear
from common.exceptions import BusinessConflictError, ResourceNotFoundError
from common.validators import validate_academic_year_format


class AcademicsService:
    """
    Timetable validation and academic scheduling service.
    """

    @classmethod
    def validate_slot_conflicts(cls, academic_year, day_of_week, period, class_subject, room=None, exclude_slot_id=None):
        # 1. Teacher conflict check
        teacher = class_subject.teacher
        if teacher:
            teacher_conflict = TimetableSlot.objects.filter(
                academic_year=academic_year,
                day_of_week=day_of_week,
                period=period,
                class_subject__teacher=teacher
            )
            if exclude_slot_id:
                teacher_conflict = teacher_conflict.exclude(id=exclude_slot_id)
            if teacher_conflict.exists():
                raise BusinessConflictError(f"Teacher {teacher} is already assigned to another class during this period.")

        # 2. Room conflict check
        if room:
            room_conflict = TimetableSlot.objects.filter(
                academic_year=academic_year,
                day_of_week=day_of_week,
                period=period,
                room=room
            )
            if exclude_slot_id:
                room_conflict = room_conflict.exclude(id=exclude_slot_id)
            if room_conflict.exists():
                raise BusinessConflictError(f"Room {room} is already booked for another session in this period.")


class AcademicYearService:
    """
    Academic Year Management Service for School ERP.

    Encapsulates all business rules for CBSE academic sessions:
      - Validates YYYY-YYYY naming format and consecutive year logic.
      - Enforces start_date < end_date and alignment with session naming.
      - Prevents overlapping academic year date ranges.
      - Manages single-active-current session toggles atomically.
      - Preserves previous year is_active status when switching current year.
      - Forbids inactive years from becoming current.
      - Protects academic years with dependent records from deletion.
      - Logs all sensitive lifecycle events through AuditService.
    """

    @classmethod
    @transaction.atomic
    def create_academic_year(
        cls,
        name,
        start_date_val,
        end_date_val,
        is_current=False,
        is_active=True,
        actor=None,
        reason="",
        request=None
    ):
        """
        Creates a new AcademicYear record with complete business validation and audit logging.
        """
        name = str(name).strip()
        validate_academic_year_format(name)

        if isinstance(start_date_val, str):
            start_date_val = date.fromisoformat(start_date_val.strip())
        if isinstance(end_date_val, str):
            end_date_val = date.fromisoformat(end_date_val.strip())

        if start_date_val >= end_date_val:
            raise ValidationError({
                'end_date': _("End date must be strictly after start date.")
            })

        # Validate start and end years match the name
        start_yr, end_yr = map(int, name.split('-'))
        if start_date_val.year != start_yr or end_date_val.year != end_yr:
            raise ValidationError({
                'name': _(
                    f"Academic year name '{name}' must align with start date year "
                    f"({start_date_val.year}) and end date year ({end_date_val.year})."
                )
            })

        # Overlap check
        overlapping = AcademicYear.objects.filter(
            start_date__lte=end_date_val,
            end_date__gte=start_date_val,
        )
        if overlapping.exists():
            overlap_names = ", ".join(overlapping.values_list('name', flat=True))
            raise ValidationError(
                _(f"Academic year date range ({start_date_val} to {end_date_val}) overlaps with existing academic year(s): {overlap_names}.")
            )

        # Rule 8: Inactive year cannot be selected as current
        if is_current and not is_active:
            raise ValidationError({
                'is_current': _("An inactive academic year cannot be selected as current.")
            })

        # Rule 5 & 6: Single current year, transaction-safe switch
        if is_current:
            from apps.school_config.models import SchoolProfile
            SchoolProfile.acquire_lock()
            # Atomically lock and update other current years
            # Rule 7: Do NOT touch is_active of previous current year
            current_years = AcademicYear.objects.select_for_update().filter(is_current=True)
            current_years.update(is_current=False)

        year = AcademicYear(
            name=name,
            start_date=start_date_val,
            end_date=end_date_val,
            is_current=is_current,
            is_active=is_active,
        )
        year.clean()
        year.save()

        # Audit Log
        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="AcademicYear",
            entity_id=str(year.pk),
            actor=actor,
            reason=reason or f"Created academic year {year.name}",
            changes={
                "name": year.name,
                "start_date": str(year.start_date),
                "end_date": str(year.end_date),
                "is_current": year.is_current,
                "is_active": year.is_active,
            },
            request=request,
        )

        return year

    @classmethod
    @transaction.atomic
    def update_academic_year(
        cls,
        academic_year_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        """
        Updates an existing academic year with conflict validation and audit logging.
        Acquires exclusive singleton lock on SchoolProfile FIRST to ensure consistent lock ordering
        across all academic year mutations and prevent deadlocks.
        """
        from apps.school_config.models import SchoolProfile
        SchoolProfile.acquire_lock()

        try:
            year = AcademicYear.objects.select_for_update().get(pk=academic_year_id)
        except AcademicYear.DoesNotExist:
            raise ResourceNotFoundError(f"Academic year with ID {academic_year_id} not found.")

        old_state = {
            "name": year.name,
            "start_date": str(year.start_date),
            "end_date": str(year.end_date),
            "is_current": year.is_current,
            "is_active": year.is_active,
        }

        # Apply and validate updates
        if 'name' in kwargs:
            year.name = str(kwargs['name']).strip()
            validate_academic_year_format(year.name)

        if 'start_date' in kwargs:
            val = kwargs['start_date']
            year.start_date = date.fromisoformat(val.strip()) if isinstance(val, str) else val

        if 'end_date' in kwargs:
            val = kwargs['end_date']
            year.end_date = date.fromisoformat(val.strip()) if isinstance(val, str) else val

        if year.start_date >= year.end_date:
            raise ValidationError({
                'end_date': _("End date must be strictly after start date.")
            })

        # Name / Date year alignment check
        try:
            start_yr, end_yr = map(int, str(year.name).strip().split('-'))
            if year.start_date.year != start_yr or year.end_date.year != end_yr:
                raise ValidationError({
                    'name': _(
                        f"Academic year name '{year.name}' must align with start date year "
                        f"({year.start_date.year}) and end date year ({year.end_date.year})."
                    )
                })
        except (ValueError, TypeError):
            pass

        # Overlap check (excluding self)
        overlapping = AcademicYear.objects.filter(
            start_date__lte=year.end_date,
            end_date__gte=year.start_date,
        ).exclude(pk=year.pk)

        if overlapping.exists():
            overlap_names = ", ".join(overlapping.values_list('name', flat=True))
            raise ValidationError(
                _(f"Academic year date range ({year.start_date} to {year.end_date}) overlaps with existing academic year(s): {overlap_names}.")
            )

        if 'is_active' in kwargs:
            target_active = bool(kwargs['is_active'])
            if not target_active and year.is_current and kwargs.get('is_current', True):
                raise ValidationError({
                    'is_active': _("Cannot deactivate the currently active academic year. Switch the active year first.")
                })
            year.is_active = target_active

        if 'is_current' in kwargs:
            target_current = bool(kwargs['is_current'])
            if target_current:
                if not year.is_active:
                    raise ValidationError({
                        'is_current': _("An inactive academic year cannot be selected as current.")
                    })
                # Lock and turn other current years to False (SchoolProfile lock is already held)
                AcademicYear.objects.select_for_update().filter(is_current=True).exclude(pk=year.pk).update(is_current=False)
            year.is_current = target_current

        year.clean()
        year.save()

        # Build changes diff
        new_state = {
            "name": year.name,
            "start_date": str(year.start_date),
            "end_date": str(year.end_date),
            "is_current": year.is_current,
            "is_active": year.is_active,
        }
        changes = {
            k: {"old": old_state[k], "new": new_state[k]}
            for k in old_state
            if old_state[k] != new_state[k]
        }

        if changes:
            AuditService.log(
                action=AuditAction.UPDATE,
                module="academics",
                entity_type="AcademicYear",
                entity_id=str(year.pk),
                actor=actor,
                reason=reason or f"Updated academic year {year.name}",
                changes=changes,
                request=request,
            )

        return year

    @classmethod
    @transaction.atomic
    def set_current_year(
        cls,
        academic_year_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Switches the currently active academic year across the ERP.
        Enforces single-active-session invariant atomically and preserves
        the active status of previous sessions.

        Acquires exclusive singleton lock on SchoolProfile FIRST to reliably serialize
        concurrent activations even when no academic year is currently active.
        """
        from apps.school_config.models import SchoolProfile
        SchoolProfile.acquire_lock()

        try:
            target_year = AcademicYear.objects.select_for_update().get(pk=academic_year_id)
        except AcademicYear.DoesNotExist:
            raise ResourceNotFoundError(f"Academic year with ID {academic_year_id} not found.")

        # Rule 8: Inactive year cannot be selected as current
        if not target_year.is_active:
            raise ValidationError("An inactive academic year cannot be selected as current.")

        # If already current, no change needed
        if target_year.is_current:
            return target_year

        # Rule 5, 6, 7: Lock other current year(s) and set is_current=False without deactivating is_active
        previous_current_qs = AcademicYear.objects.select_for_update().filter(
            is_current=True
        ).exclude(pk=target_year.pk)

        previous_current_names = list(previous_current_qs.values_list('name', flat=True))
        previous_current_qs.update(is_current=False)

        # Set target as current
        target_year.is_current = True
        target_year.save()

        # Audit logging
        AuditService.log(
            action=AuditAction.UPDATE,
            module="academics",
            entity_type="AcademicYear",
            entity_id=str(target_year.pk),
            actor=actor,
            reason=reason or f"Switched active academic year to {target_year.name}",
            changes={
                "is_current": {"old": False, "new": True},
                "previous_current_years": previous_current_names,
            },
            request=request,
        )

        return target_year

    @classmethod
    def get_current_year(cls):
        """
        Returns the currently active AcademicYear or None if none is active.
        """
        return AcademicYear.objects.filter(is_current=True).first()

    @classmethod
    def get_previous_year(cls, current_year=None):
        """
        Returns the preceding AcademicYear record chronologically before the current year.
        """
        if current_year is None:
            current_year = cls.get_current_year()

        if not current_year:
            return None

        return AcademicYear.objects.filter(
            start_date__lt=current_year.start_date
        ).order_by('-start_date').first()

    @classmethod
    def get_academic_year_history(cls, include_inactive=True):
        """
        Returns all historical and current academic year records ordered by start_date descending.
        """
        qs = AcademicYear.objects.all().order_by('-start_date')
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs

    @classmethod
    @transaction.atomic
    def delete_academic_year(
        cls,
        academic_year_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Deletes an academic year record only if no dependent operational or academic records exist.
        Otherwise raises BusinessConflictError to preserve audit and historical integrity.
        """
        from apps.school_config.models import SchoolProfile
        SchoolProfile.acquire_lock()

        try:
            year = AcademicYear.objects.select_for_update().get(pk=academic_year_id)
        except AcademicYear.DoesNotExist:
            raise ResourceNotFoundError(f"Academic year with ID {academic_year_id} not found.")

        if year.is_current:
            raise BusinessConflictError(
                f"Cannot delete academic year '{year.name}' as it is currently marked as active. "
                "Switch the active year before attempting deletion."
            )

        # Check for dependent records across ERP modules
        dependencies = []

        from apps.academics.models import TimetableSlot
        tt_count = TimetableSlot.objects.filter(academic_year=year).count()
        if tt_count:
            dependencies.append(f"{tt_count} timetable slot(s)")

        from apps.admissions.models import AdmissionApplication, SeatQuota
        app_count = AdmissionApplication.objects.filter(academic_year=year).count()
        if app_count:
            dependencies.append(f"{app_count} admission application(s)")
        sq_count = SeatQuota.objects.filter(academic_year=year).count()
        if sq_count:
            dependencies.append(f"{sq_count} seat quota(s)")

        from apps.students.models import ClassEnrollment
        enr_count = ClassEnrollment.objects.filter(academic_year=year).count()
        if enr_count:
            dependencies.append(f"{enr_count} student class enrollment(s)")

        from apps.attendance.models import Holiday, StudentDailyAttendance
        hol_count = Holiday.objects.filter(academic_year=year).count()
        if hol_count:
            dependencies.append(f"{hol_count} attendance holiday(s)")
        att_count = StudentDailyAttendance.objects.filter(academic_year=year).count()
        if att_count:
            dependencies.append(f"{att_count} student attendance record(s)")

        from apps.finance.models import FeeStructure, StudentInvoice
        fs_count = FeeStructure.objects.filter(academic_year=year).count()
        if fs_count:
            dependencies.append(f"{fs_count} fee structure(s)")
        inv_count = StudentInvoice.objects.filter(academic_year=year).count()
        if inv_count:
            dependencies.append(f"{inv_count} fee invoice(s)")

        from apps.examinations.models import ExamTerm
        term_count = ExamTerm.objects.filter(academic_year=year).count()
        if term_count:
            dependencies.append(f"{term_count} exam term(s)")

        from apps.hrms.models import PayrollRun
        payroll_count = PayrollRun.objects.filter(academic_year=year).count()
        if payroll_count:
            dependencies.append(f"{payroll_count} payroll run(s)")

        from apps.school_config.models import SchoolProfile
        if SchoolProfile.objects.filter(current_academic_year=year).exists():
            dependencies.append("School Profile reference")

        if dependencies:
            dep_summary = ", ".join(dependencies)
            raise BusinessConflictError(
                f"Cannot delete academic year '{year.name}' as dependent records exist: {dep_summary}. "
                "Historical academic records must be preserved. Deactivate the academic year instead."
            )

        year_id = year.pk
        year_name = year.name
        year.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="AcademicYear",
            entity_id=str(year_id),
            actor=actor,
            reason=reason or f"Deleted academic year {year_name}",
            changes={"name": year_name},
            request=request,
        )
