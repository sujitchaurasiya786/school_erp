from datetime import date
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.academics.models import (
    AcademicStream, ClassSubject, SchoolClass, Section, Stream, Subject, SubjectType,
    TimetablePeriod, TimetableSlot
)
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.school_config.models import AcademicYear
from common.exceptions import BusinessConflictError, ResourceNotFoundError
from common.validators import validate_academic_year_format
from apps.identity.models import User, UserType


class AcademicsService:
    """
    Timetable validation and academic scheduling service.
    """

    @classmethod
    def validate_slot_conflicts(cls, academic_year, day_of_week, period, class_subject, room=None, exclude_slot_id=None):
        # 0. Break period check
        if period and getattr(period, 'is_break', False):
            raise ValidationError({'period': _(f"Cannot schedule academic classes during break period '{period.name}'.")})

        day_name = dict(TimetableSlot.DAYS_OF_WEEK).get(day_of_week, f"Day {day_of_week}")

        # 1. Section conflict check (Section cannot have two subjects at the same period)
        if class_subject and hasattr(class_subject, 'section'):
            section_conflict = TimetableSlot.objects.filter(
                academic_year=academic_year,
                day_of_week=day_of_week,
                period=period,
                class_subject__section=class_subject.section
            )
            if exclude_slot_id:
                section_conflict = section_conflict.exclude(id=exclude_slot_id)
            if section_conflict.exists():
                existing = section_conflict.first()
                raise BusinessConflictError(
                    f"Section '{class_subject.section}' already has '{existing.class_subject.subject.name}' "
                    f"scheduled on {day_name} during {period.name} {period.period_number}."
                )

        # 2. Teacher conflict check
        teacher = getattr(class_subject, 'teacher', None)
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
                existing = teacher_conflict.first()
                teacher_name = teacher.get_full_name() or teacher.username
                raise BusinessConflictError(
                    f"Teacher '{teacher_name}' is already assigned to teach '{existing.class_subject.subject.name}' "
                    f"in '{existing.class_subject.section}' on {day_name} during {period.name} {period.period_number}."
                )

        # 3. Room conflict check
        if room:
            cleaned_room = str(room).strip()
            if cleaned_room:
                room_conflict = TimetableSlot.objects.filter(
                    academic_year=academic_year,
                    day_of_week=day_of_week,
                    period=period,
                    room__iexact=cleaned_room
                )
                if exclude_slot_id:
                    room_conflict = room_conflict.exclude(id=exclude_slot_id)
                if room_conflict.exists():
                    existing = room_conflict.first()
                    raise BusinessConflictError(
                        f"Room '{cleaned_room}' is already booked for '{existing.class_subject.section}' "
                        f"({existing.class_subject.subject.name}) on {day_name} during {period.name} {period.period_number}."
                    )


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

        from apps.academics.models import (
    AcademicStream, ClassSubject, SchoolClass, Section, Stream, Subject, SubjectType,
    TimetablePeriod, TimetableSlot
)
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


class SchoolClassService:
    """
    Business service for managing CBSE Classes (Nursery to Class 12).
    """

    @classmethod
    @transaction.atomic
    def create_class(
        cls,
        name,
        code,
        order=1,
        is_active=True,
        actor=None,
        reason="",
        request=None
    ):
        """
        Creates a new SchoolClass record with validation and audit logging.
        """
        school_class = SchoolClass(
            name=name,
            code=code,
            order=order,
            is_active=is_active,
        )
        school_class.clean()
        school_class.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="SchoolClass",
            entity_id=str(school_class.pk),
            actor=actor,
            reason=reason or f"Created class {school_class.name}",
            changes={
                "name": school_class.name,
                "code": school_class.code,
                "order": school_class.order,
                "is_active": school_class.is_active,
            },
            request=request,
        )
        return school_class

    @classmethod
    @transaction.atomic
    def update_class(
        cls,
        class_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        """
        Updates an existing SchoolClass record with validation and audit logging.
        """
        try:
            school_class = SchoolClass.objects.select_for_update().get(pk=class_id)
        except SchoolClass.DoesNotExist:
            raise ResourceNotFoundError(f"Class with ID {class_id} not found.")

        old_state = {
            "name": school_class.name,
            "code": school_class.code,
            "order": school_class.order,
            "is_active": school_class.is_active,
        }

        if 'name' in kwargs:
            school_class.name = str(kwargs['name']).strip()
        if 'code' in kwargs:
            school_class.code = str(kwargs['code']).strip().upper()
        if 'order' in kwargs:
            school_class.order = int(kwargs['order'])
        if 'is_active' in kwargs:
            school_class.is_active = bool(kwargs['is_active'])

        school_class.clean()
        school_class.save()

        new_state = {
            "name": school_class.name,
            "code": school_class.code,
            "order": school_class.order,
            "is_active": school_class.is_active,
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
                entity_type="SchoolClass",
                entity_id=str(school_class.pk),
                actor=actor,
                reason=reason or f"Updated class {school_class.name}",
                changes=changes,
                request=request,
            )

        return school_class

    @classmethod
    def get_class(cls, class_id):
        """
        Retrieves a SchoolClass by ID.
        """
        try:
            return SchoolClass.objects.get(pk=class_id)
        except SchoolClass.DoesNotExist:
            raise ResourceNotFoundError(f"Class with ID {class_id} not found.")

    @classmethod
    def list_classes(cls, active_only=False):
        """
        Returns an ordered queryset of SchoolClass records.
        """
        qs = SchoolClass.objects.all().order_by('order', 'name')
        if active_only:
            qs = qs.filter(is_active=True)
        return qs

    @classmethod
    @transaction.atomic
    def delete_class(
        cls,
        class_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Deletes a SchoolClass only if no sections exist.
        Otherwise raises BusinessConflictError to prevent cascade deletion.
        """
        try:
            school_class = SchoolClass.objects.select_for_update().get(pk=class_id)
        except SchoolClass.DoesNotExist:
            raise ResourceNotFoundError(f"Class with ID {class_id} not found.")

        section_count = school_class.sections.count()
        if section_count > 0:
            raise BusinessConflictError(
                f"Cannot delete class '{school_class.name}' because {section_count} section(s) "
                "are associated with it. Delete or reassign sections first, or deactivate the class."
            )

        class_id_str = str(school_class.pk)
        class_name = school_class.name
        school_class.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="SchoolClass",
            entity_id=class_id_str,
            actor=actor,
            reason=reason or f"Deleted class {class_name}",
            changes={"name": class_name},
            request=request,
        )


class SectionService:
    """
    Manages operations on Section records (Class Sections).
    Enforces business rules, capacity limits, uppercase name normalization,
    dependency protections against cascade deletions, and audit trails.
    """

    @classmethod
    @transaction.atomic
    def create_section(
        cls,
        school_class_id,
        name,
        capacity=40,
        stream=Stream.NONE,
        room_number="",
        class_teacher_id=None,
        is_active=True,
        actor=None,
        reason="",
        request=None
    ):
        """
        Creates a new Section with validation and audit logging.
        """
        try:
            school_class = SchoolClass.objects.get(pk=school_class_id)
        except SchoolClass.DoesNotExist:
            raise ResourceNotFoundError(f"Class with ID {school_class_id} not found.")

        cleaned_name = str(name or "").strip().upper()
        if not cleaned_name:
            raise ValidationError({'name': _("Section name is required.")})

        parsed_capacity = int(capacity) if capacity is not None else 40
        if parsed_capacity < 1:
            raise ValidationError({'capacity': _("Capacity must be a positive integer (>= 1).")})

        section = Section(
            school_class=school_class,
            name=cleaned_name,
            capacity=parsed_capacity,
            stream=stream or Stream.NONE,
            room_number=str(room_number or "").strip(),
            class_teacher_id=class_teacher_id,
            is_active=bool(is_active)
        )
        section.clean()
        section.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="Section",
            entity_id=str(section.pk),
            actor=actor,
            reason=reason or f"Created section {section.name} for class {school_class.name}",
            changes={
                "school_class_id": school_class.pk,
                "class_name": school_class.name,
                "name": section.name,
                "capacity": section.capacity,
                "stream": section.stream,
                "room_number": section.room_number,
                "is_active": section.is_active,
            },
            request=request,
        )

        return section

    @classmethod
    @transaction.atomic
    def update_section(
        cls,
        section_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        """
        Updates an existing Section with validation, diff-tracking, and audit logging.
        """
        try:
            section = Section.objects.select_for_update().get(pk=section_id)
        except Section.DoesNotExist:
            raise ResourceNotFoundError(f"Section with ID {section_id} not found.")

        old_state = {
            "name": section.name,
            "capacity": section.capacity,
            "stream": section.stream,
            "room_number": section.room_number,
            "class_teacher_id": section.class_teacher_id,
            "is_active": section.is_active,
            "school_class_id": section.school_class_id,
        }

        if 'school_class_id' in kwargs:
            new_class_id = kwargs['school_class_id']
            try:
                section.school_class = SchoolClass.objects.get(pk=new_class_id)
            except SchoolClass.DoesNotExist:
                raise ResourceNotFoundError(f"Class with ID {new_class_id} not found.")

        if 'name' in kwargs:
            section.name = str(kwargs['name']).strip().upper()
        if 'capacity' in kwargs:
            section.capacity = int(kwargs['capacity'])
        if 'stream' in kwargs:
            section.stream = kwargs['stream'] or Stream.NONE
        if 'room_number' in kwargs:
            section.room_number = str(kwargs['room_number'] or "").strip()
        if 'class_teacher_id' in kwargs:
            section.class_teacher_id = kwargs['class_teacher_id']
        if 'is_active' in kwargs:
            section.is_active = bool(kwargs['is_active'])

        section.clean()
        section.save()

        new_state = {
            "name": section.name,
            "capacity": section.capacity,
            "stream": section.stream,
            "room_number": section.room_number,
            "class_teacher_id": section.class_teacher_id,
            "is_active": section.is_active,
            "school_class_id": section.school_class_id,
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
                entity_type="Section",
                entity_id=str(section.pk),
                actor=actor,
                reason=reason or f"Updated section {section.name}",
                changes=changes,
                request=request,
            )

        return section

    @classmethod
    def get_section(cls, section_id):
        """
        Retrieves a Section by ID.
        """
        try:
            return Section.objects.select_related('school_class', 'class_teacher').get(pk=section_id)
        except Section.DoesNotExist:
            raise ResourceNotFoundError(f"Section with ID {section_id} not found.")

    @classmethod
    def list_sections(cls, school_class_id=None, stream=None, active_only=False):
        """
        Returns an ordered queryset of Section records.
        """
        qs = Section.objects.select_related('school_class', 'class_teacher').all().order_by(
            'school_class__order', 'name'
        )
        if school_class_id:
            qs = qs.filter(school_class_id=school_class_id)
        if stream:
            qs = qs.filter(stream=stream)
        if active_only:
            qs = qs.filter(is_active=True)
        return qs

    @classmethod
    @transaction.atomic
    def delete_section(
        cls,
        section_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Deletes a Section only if no subject mappings or dependencies exist.
        Otherwise raises BusinessConflictError to prevent cascade deletion.
        """
        try:
            section = Section.objects.select_for_update().get(pk=section_id)
        except Section.DoesNotExist:
            raise ResourceNotFoundError(f"Section with ID {section_id} not found.")

        subject_count = section.class_subjects.count()
        if subject_count > 0:
            raise BusinessConflictError(
                f"Cannot delete section '{section}' because {subject_count} subject mapping(s) "
                "are associated with it. Delete or reassign subject mappings first, or deactivate the section."
            )

        section_id_str = str(section.pk)
        section_str = str(section)
        section.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="Section",
            entity_id=section_id_str,
            actor=actor,
            reason=reason or f"Deleted section {section_str}",
            changes={"section": section_str},
            request=request,
        )


class StreamService:
    """
    Manages operations on AcademicStream records for senior-secondary classes (Classes 11 and 12).
    Enforces class eligibility, uppercase code normalization, dependency protection, and audit logging.
    """

    @classmethod
    @transaction.atomic
    def create_stream(
        cls,
        school_class_id,
        name,
        code,
        description="",
        is_active=True,
        actor=None,
        reason="",
        request=None
    ):
        """
        Creates an AcademicStream for Class 11 or 12 with validation and audit logging.
        """
        try:
            school_class = SchoolClass.objects.get(pk=school_class_id)
        except SchoolClass.DoesNotExist:
            raise ResourceNotFoundError(f"Class with ID {school_class_id} not found.")

        if not school_class.is_senior_secondary:
            raise ValidationError(
                f"Streams can only be configured for senior-secondary classes (Classes 11 and 12). '{school_class.name}' is not eligible."
            )

        cleaned_name = str(name or "").strip()
        cleaned_code = str(code or "").strip().upper()

        if not cleaned_name:
            raise ValidationError({'name': _("Stream name is required.")})
        if not cleaned_code:
            raise ValidationError({'code': _("Stream code is required.")})

        stream = AcademicStream(
            school_class=school_class,
            name=cleaned_name,
            code=cleaned_code,
            description=str(description or "").strip(),
            is_active=bool(is_active)
        )
        stream.clean()
        stream.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="AcademicStream",
            entity_id=str(stream.pk),
            actor=actor,
            reason=reason or f"Created stream {stream.name} ({stream.code}) for {school_class.name}",
            changes={
                "school_class_id": school_class.pk,
                "class_name": school_class.name,
                "name": stream.name,
                "code": stream.code,
                "description": stream.description,
                "is_active": stream.is_active,
            },
            request=request,
        )
        return stream

    @classmethod
    @transaction.atomic
    def update_stream(
        cls,
        stream_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        """
        Updates an existing AcademicStream with validation, diff-tracking, and audit logging.
        """
        try:
            stream = AcademicStream.objects.select_for_update().get(pk=stream_id)
        except AcademicStream.DoesNotExist:
            raise ResourceNotFoundError(f"Stream with ID {stream_id} not found.")

        old_state = {
            "name": stream.name,
            "code": stream.code,
            "description": stream.description,
            "is_active": stream.is_active,
            "school_class_id": stream.school_class_id,
        }

        if 'school_class_id' in kwargs:
            new_class_id = kwargs['school_class_id']
            try:
                new_class = SchoolClass.objects.get(pk=new_class_id)
            except SchoolClass.DoesNotExist:
                raise ResourceNotFoundError(f"Class with ID {new_class_id} not found.")
            if not new_class.is_senior_secondary:
                raise ValidationError(
                    f"Streams can only be assigned to senior-secondary classes (Classes 11 and 12). '{new_class.name}' is not eligible."
                )
            stream.school_class = new_class

        if 'name' in kwargs:
            stream.name = str(kwargs['name']).strip()
        if 'code' in kwargs:
            stream.code = str(kwargs['code']).strip().upper()
        if 'description' in kwargs:
            stream.description = str(kwargs['description'] or "").strip()
        if 'is_active' in kwargs:
            stream.is_active = bool(kwargs['is_active'])

        stream.clean()
        stream.save()

        new_state = {
            "name": stream.name,
            "code": stream.code,
            "description": stream.description,
            "is_active": stream.is_active,
            "school_class_id": stream.school_class_id,
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
                entity_type="AcademicStream",
                entity_id=str(stream.pk),
                actor=actor,
                reason=reason or f"Updated stream {stream.name}",
                changes=changes,
                request=request,
            )
        return stream

    @classmethod
    def get_stream(cls, stream_id):
        """
        Retrieves an AcademicStream by ID.
        """
        try:
            return AcademicStream.objects.select_related('school_class').get(pk=stream_id)
        except AcademicStream.DoesNotExist:
            raise ResourceNotFoundError(f"Stream with ID {stream_id} not found.")

    @classmethod
    def list_streams(cls, school_class_id=None, active_only=False):
        """
        Returns an ordered queryset of AcademicStream records.
        """
        qs = AcademicStream.objects.select_related('school_class').all().order_by(
            'school_class__order', 'name'
        )
        if school_class_id:
            qs = qs.filter(school_class_id=school_class_id)
        if active_only:
            qs = qs.filter(is_active=True)
        return qs

    @classmethod
    @transaction.atomic
    def delete_stream(
        cls,
        stream_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Deletes an AcademicStream only if no sections reference its stream name/code.
        Otherwise raises BusinessConflictError to protect historical data integrity.
        """
        try:
            stream = AcademicStream.objects.select_for_update().get(pk=stream_id)
        except AcademicStream.DoesNotExist:
            raise ResourceNotFoundError(f"Stream with ID {stream_id} not found.")

        # Check if any section of this class has stream set to this code or name
        section_count = Section.objects.filter(
            school_class=stream.school_class,
            stream__in=[stream.code, stream.name.upper()]
        ).count()

        if section_count > 0:
            raise BusinessConflictError(
                f"Cannot delete stream '{stream.name}' because {section_count} section(s) in "
                f"'{stream.school_class.name}' are assigned to it. Reassign or delete sections first, or deactivate the stream."
            )

        stream_id_str = str(stream.pk)
        stream_str = str(stream)
        stream.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="AcademicStream",
            entity_id=stream_id_str,
            actor=actor,
            reason=reason or f"Deleted stream {stream_str}",
            changes={"stream": stream_str},
            request=request,
        )


class SubjectService:
    """
    Manages operations on central Subject master records.
    Enforces uppercase code normalization, component validation (theory/practical),
    dependency protections against cascade deletions, and audit trails.
    """

    @classmethod
    @transaction.atomic
    def create_subject(
        cls,
        name,
        code,
        subject_type=SubjectType.COMPULSORY,
        is_theory=True,
        is_practical=False,
        description="",
        is_active=True,
        actor=None,
        reason="",
        request=None
    ):
        """
        Creates a new Subject with validation and audit logging.
        """
        cleaned_name = str(name or "").strip()
        cleaned_code = str(code or "").strip().upper()

        if not cleaned_name:
            raise ValidationError({'name': _("Subject name is required.")})
        if not cleaned_code:
            raise ValidationError({'code': _("Subject code is required.")})

        subject = Subject(
            name=cleaned_name,
            code=cleaned_code,
            subject_type=subject_type or SubjectType.COMPULSORY,
            is_theory=bool(is_theory),
            is_practical=bool(is_practical),
            description=str(description or "").strip(),
            is_active=bool(is_active)
        )
        subject.clean()
        subject.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="Subject",
            entity_id=str(subject.pk),
            actor=actor,
            reason=reason or f"Created subject {subject.name} ({subject.code})",
            changes={
                "name": subject.name,
                "code": subject.code,
                "subject_type": subject.subject_type,
                "is_theory": subject.is_theory,
                "is_practical": subject.is_practical,
                "description": subject.description,
                "is_active": subject.is_active,
            },
            request=request,
        )
        return subject

    @classmethod
    @transaction.atomic
    def update_subject(
        cls,
        subject_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        """
        Updates an existing Subject with validation, diff-tracking, and audit logging.
        """
        try:
            subject = Subject.objects.select_for_update().get(pk=subject_id)
        except Subject.DoesNotExist:
            raise ResourceNotFoundError(f"Subject with ID {subject_id} not found.")

        old_state = {
            "name": subject.name,
            "code": subject.code,
            "subject_type": subject.subject_type,
            "is_theory": subject.is_theory,
            "is_practical": subject.is_practical,
            "description": subject.description,
            "is_active": subject.is_active,
        }

        if 'name' in kwargs:
            subject.name = str(kwargs['name']).strip()
        if 'code' in kwargs:
            subject.code = str(kwargs['code']).strip().upper()
        if 'subject_type' in kwargs:
            subject.subject_type = kwargs['subject_type'] or SubjectType.COMPULSORY
        if 'is_theory' in kwargs:
            subject.is_theory = bool(kwargs['is_theory'])
        if 'is_practical' in kwargs:
            subject.is_practical = bool(kwargs['is_practical'])
        if 'description' in kwargs:
            subject.description = str(kwargs['description'] or "").strip()
        if 'is_active' in kwargs:
            subject.is_active = bool(kwargs['is_active'])

        subject.clean()
        subject.save()

        new_state = {
            "name": subject.name,
            "code": subject.code,
            "subject_type": subject.subject_type,
            "is_theory": subject.is_theory,
            "is_practical": subject.is_practical,
            "description": subject.description,
            "is_active": subject.is_active,
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
                entity_type="Subject",
                entity_id=str(subject.pk),
                actor=actor,
                reason=reason or f"Updated subject {subject.name}",
                changes=changes,
                request=request,
            )
        return subject

    @classmethod
    def get_subject(cls, subject_id):
        """
        Retrieves a Subject by ID.
        """
        try:
            return Subject.objects.get(pk=subject_id)
        except Subject.DoesNotExist:
            raise ResourceNotFoundError(f"Subject with ID {subject_id} not found.")

    @classmethod
    def list_subjects(cls, subject_type=None, active_only=False, search=None):
        """
        Returns an ordered queryset of Subject records with optional filtering.
        """
        qs = Subject.objects.all().order_by('name', 'code')
        if subject_type:
            qs = qs.filter(subject_type=subject_type)
        if active_only:
            qs = qs.filter(is_active=True)
        if search:
            query = str(search).strip()
            qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
        return qs

    @classmethod
    @transaction.atomic
    def delete_subject(
        cls,
        subject_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Deletes a Subject only if not mapped to any class-sections.
        Otherwise raises BusinessConflictError to preserve curriculum history.
        """
        try:
            subject = Subject.objects.select_for_update().get(pk=subject_id)
        except Subject.DoesNotExist:
            raise ResourceNotFoundError(f"Subject with ID {subject_id} not found.")

        mapping_count = subject.subject_mappings.count()
        if mapping_count > 0:
            raise BusinessConflictError(
                f"Cannot delete subject '{subject.name}' because {mapping_count} class-section mapping(s) "
                "are associated with it. Delete or reassign mappings first, or deactivate the subject."
            )

        subject_id_str = str(subject.pk)
        subject_str = str(subject)
        subject.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="Subject",
            entity_id=subject_id_str,
            actor=actor,
            reason=reason or f"Deleted subject {subject_str}",
            changes={"subject": subject_str},
            request=request,
        )


class ClassSubjectService:
    """
    Class-Subject Mapping Service for School ERP.

    Encapsulates all curriculum mapping business rules:
      - Maps central Subject catalog records to Classes, Streams, and Sections.
      - Enforces that only active subjects can be newly mapped.
      - Manages curriculum classifications (Compulsory, Optional, Elective).
      - Enforces senior secondary stream consistency (Classes 11-12).
      - Provides atomic bulk mapping across sections of a class or stream.
      - Provides copying of mappings from one section to other sections.
      - Protects mapped subjects with dependent timetable slots from deletion.
      - Logs all sensitive mapping events through AuditService.
    """

    @classmethod
    @transaction.atomic
    def create_mapping(
        cls,
        section_id,
        subject_id,
        subject_type=None,
        academic_year_id=None,
        teacher_id=None,
        is_active=True,
        actor=None,
        reason="",
        request=None
    ):
        """
        Creates a single ClassSubject mapping for a section with validation and audit logging.
        """
        try:
            section = Section.objects.select_for_update().select_related('school_class').get(pk=section_id)
        except Section.DoesNotExist:
            raise ResourceNotFoundError(f"Section with ID {section_id} not found.")

        try:
            subject = Subject.objects.get(pk=subject_id)
        except Subject.DoesNotExist:
            raise ResourceNotFoundError(f"Subject with ID {subject_id} not found.")

        if not subject.is_active:
            raise ValidationError({'subject': _(f"Cannot map inactive subject '{subject.name}'.")})

        resolved_type = subject_type or subject.subject_type or SubjectType.COMPULSORY

        academic_year = None
        if academic_year_id:
            try:
                academic_year = AcademicYear.objects.get(pk=academic_year_id)
            except AcademicYear.DoesNotExist:
                raise ResourceNotFoundError(f"Academic Year with ID {academic_year_id} not found.")

        teacher = None
        if teacher_id:
            from apps.identity.models import User, UserType
            try:
                teacher = User.objects.get(pk=teacher_id)
            except User.DoesNotExist:
                raise ResourceNotFoundError(f"User with ID {teacher_id} not found.")

        if ClassSubject.objects.filter(section=section, subject=subject).exists():
            raise ValidationError({
                'subject': _(f"Subject '{subject.name}' is already mapped to Section '{section}'.")
            })

        is_elective_val = (resolved_type == SubjectType.ELECTIVE)

        mapping = ClassSubject(
            section=section,
            subject=subject,
            subject_type=resolved_type,
            is_elective=is_elective_val,
            academic_year=academic_year,
            teacher=teacher,
            is_active=is_active
        )
        mapping.clean()
        mapping.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="ClassSubject",
            entity_id=str(mapping.pk),
            actor=actor,
            reason=reason or f"Mapped subject {subject.name} to section {section}",
            changes={
                "section": str(section),
                "subject": subject.name,
                "code": subject.code,
                "subject_type": resolved_type,
                "is_elective": is_elective_val,
                "teacher": teacher.get_full_name() if teacher else None,
                "academic_year": academic_year.name if academic_year else None,
            },
            request=request,
        )
        return mapping

    @classmethod
    @transaction.atomic
    def bulk_map_subjects_to_class(
        cls,
        school_class_id,
        subject_ids,
        stream=None,
        section_id=None,
        subject_type=None,
        default_subject_type=None,
        subject_type_map=None,
        academic_year_id=None,
        actor=None,
        reason="",
        request=None
    ):
        """
        Bulk maps multiple subjects to all sections of a class/stream (or a specific section).
        Skips already mapped subjects gracefully without failing the entire operation.
        """
        resolved_default_type = subject_type or default_subject_type
        try:
            school_class = SchoolClass.objects.get(pk=school_class_id)
        except SchoolClass.DoesNotExist:
            raise ResourceNotFoundError(f"Class with ID {school_class_id} not found.")

        if not subject_ids:
            raise ValidationError({'subjects': _("At least one subject must be selected for mapping.")})

        # Validate stream compatibility with class
        if school_class.is_senior_secondary:
            if stream and stream == Stream.NONE:
                raise ValidationError({'stream': _(f"Senior secondary classes require an academic stream. 'General' is invalid for '{school_class.name}'.")})
        else:
            if stream and stream != Stream.NONE:
                raise ValidationError({'stream': _(f"Streams are applicable only to Classes 11 and 12. '{school_class.name}' cannot have a stream.")})

        academic_year = None
        if academic_year_id:
            try:
                academic_year = AcademicYear.objects.get(pk=academic_year_id)
            except AcademicYear.DoesNotExist:
                raise ResourceNotFoundError(f"Academic Year with ID {academic_year_id} not found.")

        if section_id:
            sections_qs = Section.objects.select_for_update().filter(pk=section_id, school_class=school_class, is_active=True)
            if not sections_qs.exists():
                raise ResourceNotFoundError(f"Active section {section_id} not found in {school_class.name}.")
        else:
            sections_qs = Section.objects.select_for_update().filter(school_class=school_class, is_active=True)
            if stream and stream != Stream.NONE:
                sections_qs = sections_qs.filter(stream=stream)

        sections = list(sections_qs)
        if not sections:
            raise ValidationError({'sections': _(f"No active sections found for class '{school_class.name}' matching the criteria.")})

        subjects = list(Subject.objects.filter(pk__in=subject_ids, is_active=True))
        if not subjects:
            raise ValidationError({'subjects': _("None of the selected subjects are active or valid.")})

        created_mappings = []
        skipped_count = 0

        for section in sections:
            existing_subject_ids = set(
                ClassSubject.objects.filter(section=section).values_list('subject_id', flat=True)
            )
            for sub in subjects:
                if sub.id in existing_subject_ids:
                    skipped_count += 1
                    continue

                st = None
                if subject_type_map and str(sub.id) in subject_type_map:
                    st = subject_type_map[str(sub.id)]
                elif resolved_default_type:
                    st = resolved_default_type
                else:
                    st = sub.subject_type or SubjectType.COMPULSORY

                is_elec = (st == SubjectType.ELECTIVE)

                m = ClassSubject(
                    section=section,
                    subject=sub,
                    subject_type=st,
                    is_elective=is_elec,
                    academic_year=academic_year,
                    is_active=True
                )
                m.clean()
                m.save()
                created_mappings.append(m)
                existing_subject_ids.add(sub.id)

        created_count = len(created_mappings)

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="ClassSubject",
            entity_id=str(school_class.pk),
            actor=actor,
            reason=reason or f"Bulk mapped {created_count} subject(s) to class {school_class.name} (skipped {skipped_count})",
            changes={
                "school_class": school_class.name,
                "stream": stream,
                "sections_count": len(sections),
                "created_count": created_count,
                "skipped_count": skipped_count,
            },
            request=request,
        )

        return {
            'created_count': created_count,
            'skipped_count': skipped_count,
            'created_mappings': created_mappings,
        }

    @classmethod
    @transaction.atomic
    def update_mapping(
        cls,
        mapping_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        """
        Updates an existing ClassSubject mapping (subject_type, teacher, academic_year, is_active).
        """
        try:
            mapping = ClassSubject.objects.select_for_update().select_related('section', 'subject').get(pk=mapping_id)
        except ClassSubject.DoesNotExist:
            raise ResourceNotFoundError(f"ClassSubject mapping with ID {mapping_id} not found.")

        changes = {}

        if 'subject_type' in kwargs:
            st = kwargs['subject_type']
            if st != mapping.subject_type:
                changes['subject_type'] = {'old': mapping.subject_type, 'new': st}
                mapping.subject_type = st
                mapping.is_elective = (st == SubjectType.ELECTIVE)

        if 'is_elective' in kwargs and 'subject_type' not in kwargs:
            ie = bool(kwargs['is_elective'])
            if ie != mapping.is_elective:
                changes['is_elective'] = {'old': mapping.is_elective, 'new': ie}
                mapping.is_elective = ie
                if ie:
                    mapping.subject_type = SubjectType.ELECTIVE

        if 'teacher_id' in kwargs:
            t_id = kwargs['teacher_id']
            old_t = str(mapping.teacher) if mapping.teacher else None
            if t_id:
                from apps.identity.models import User, UserType
                try:
                    new_t = User.objects.get(pk=t_id)
                    mapping.teacher = new_t
                    changes['teacher'] = {'old': old_t, 'new': str(new_t)}
                except User.DoesNotExist:
                    raise ResourceNotFoundError(f"User with ID {t_id} not found.")
            else:
                mapping.teacher = None
                changes['teacher'] = {'old': old_t, 'new': None}

        if 'academic_year_id' in kwargs:
            ay_id = kwargs['academic_year_id']
            old_ay = str(mapping.academic_year) if mapping.academic_year else None
            if ay_id:
                try:
                    new_ay = AcademicYear.objects.get(pk=ay_id)
                    mapping.academic_year = new_ay
                    changes['academic_year'] = {'old': old_ay, 'new': str(new_ay)}
                except AcademicYear.DoesNotExist:
                    raise ResourceNotFoundError(f"Academic Year with ID {ay_id} not found.")
            else:
                mapping.academic_year = None
                changes['academic_year'] = {'old': old_ay, 'new': None}

        if 'is_active' in kwargs:
            ia = bool(kwargs['is_active'])
            if ia != mapping.is_active:
                changes['is_active'] = {'old': mapping.is_active, 'new': ia}
                mapping.is_active = ia

        mapping.clean()
        mapping.save()

        if changes:
            AuditService.log(
                action=AuditAction.UPDATE,
                module="academics",
                entity_type="ClassSubject",
                entity_id=str(mapping.pk),
                actor=actor,
                reason=reason or f"Updated ClassSubject mapping {mapping}",
                changes=changes,
                request=request,
            )

        return mapping

    @classmethod
    @transaction.atomic
    def delete_mapping(
        cls,
        mapping_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Deletes a ClassSubject mapping only if no dependent timetable slots exist.
        Otherwise raises BusinessConflictError to preserve scheduling integrity.
        """
        try:
            mapping = ClassSubject.objects.select_for_update().select_related('section', 'subject').get(pk=mapping_id)
        except ClassSubject.DoesNotExist:
            raise ResourceNotFoundError(f"ClassSubject mapping with ID {mapping_id} not found.")

        # Check dependent timetable slots
        slots_count = mapping.timetableslot_set.count()
        if slots_count > 0:
            raise BusinessConflictError(
                f"Cannot unmap '{mapping.subject.name}' from '{mapping.section}': "
                f"{slots_count} timetable slot(s) depend on this mapping. Remove the timetable slots first."
            )

        mapping_str = str(mapping)
        mapping_pk = str(mapping.pk)
        mapping.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="ClassSubject",
            entity_id=mapping_pk,
            actor=actor,
            reason=reason or f"Deleted ClassSubject mapping {mapping_str}",
            changes={"mapping": mapping_str},
            request=request,
        )
        return True

    @classmethod
    @transaction.atomic
    def copy_mappings_across_sections(
        cls,
        source_section_id,
        target_section_ids,
        actor=None,
        reason="",
        request=None
    ):
        """
        Copies all active subject mappings from source_section to target_sections, skipping duplicates.
        """
        try:
            source_sec = Section.objects.get(pk=source_section_id)
        except Section.DoesNotExist:
            raise ResourceNotFoundError(f"Source section with ID {source_section_id} not found.")

        source_mappings = list(ClassSubject.objects.filter(section=source_sec, is_active=True))
        if not source_mappings:
            raise ValidationError({'source_section': _(f"Source section '{source_sec}' has no active subject mappings to copy.")})

        target_sections = list(Section.objects.filter(pk__in=target_section_ids, is_active=True))
        if not target_sections:
            raise ValidationError({'target_sections': _("No valid target sections provided.")})

        copied_count = 0
        skipped_count = 0

        for tgt in target_sections:
            if tgt.id == source_sec.id:
                continue
            existing_sub_ids = set(ClassSubject.objects.filter(section=tgt).values_list('subject_id', flat=True))
            for sm in source_mappings:
                if sm.subject_id in existing_sub_ids:
                    skipped_count += 1
                    continue
                m = ClassSubject(
                    section=tgt,
                    subject=sm.subject,
                    subject_type=sm.subject_type,
                    is_elective=sm.is_elective,
                    academic_year=sm.academic_year,
                    is_active=True
                )
                m.clean()
                m.save()
                existing_sub_ids.add(sm.subject_id)
                copied_count += 1

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="ClassSubject",
            entity_id=str(source_sec.pk),
            actor=actor,
            reason=reason or f"Copied {copied_count} mappings from {source_sec} to {len(target_sections)} section(s)",
            changes={
                "source_section": str(source_sec),
                "copied_count": copied_count,
                "skipped_count": skipped_count,
            },
            request=request,
        )

        return {'copied_count': copied_count, 'skipped_count': skipped_count}

    @classmethod
    def get_mapping(cls, mapping_id):
        """
        Retrieves a single ClassSubject mapping or raises ResourceNotFoundError.
        """
        try:
            return ClassSubject.objects.select_related(
                'section', 'section__school_class', 'subject', 'teacher', 'academic_year'
            ).get(pk=mapping_id)
        except ClassSubject.DoesNotExist:
            raise ResourceNotFoundError(f"ClassSubject mapping with ID {mapping_id} not found.")

    @classmethod
    def list_mappings(
        cls,
        school_class_id=None,
        section_id=None,
        stream=None,
        subject_type=None,
        subject_id=None,
        academic_year_id=None,
        search=None,
        is_active=None
    ):
        """
        Returns a filtered QuerySet of ClassSubject mappings with select_related optimization.
        """
        qs = ClassSubject.objects.select_related(
            'section', 'section__school_class', 'subject', 'teacher', 'academic_year'
        ).all().order_by('section__school_class__order', 'section__name', 'subject__name')

        if school_class_id:
            qs = qs.filter(section__school_class_id=school_class_id)
        if section_id:
            qs = qs.filter(section_id=section_id)
        if stream:
            qs = qs.filter(section__stream=stream)
        if subject_type:
            qs = qs.filter(subject_type=subject_type)
        if subject_id:
            qs = qs.filter(subject_id=subject_id)
        if academic_year_id:
            qs = qs.filter(academic_year_id=academic_year_id)
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        if search:
            search = search.strip()
            qs = qs.filter(
                Q(subject__name__icontains=search) |
                Q(subject__code__icontains=search) |
                Q(section__name__icontains=search) |
                Q(section__school_class__name__icontains=search)
            )

        return qs


class TeacherAssignmentService:
    """
    Teacher & Faculty Assignment Service for School ERP (Academics Module Phase 3).

    Encapsulates all teacher allocation workflows:
      - Class-Teacher Assignment (§4.7):
        * Assigns an authorized teaching staff member as the primary Class Teacher for a section.
        * Validates teacher active status and eligibility (TEACHER persona, dynamic role, or staff flag).
        * Single-section primary check to prevent undetected multiple class teacher collisions.
        * Unassigns class teacher cleanly.
      - Subject-Teacher Assignment (§4.8):
        * Assigns qualified faculty to specific ClassSubject curriculum mappings.
        * Bulk assigns faculty across sections (e.g. Mathematics teacher for 10-A, 10-B, 10-C).
        * Validates active subject mapping prerequisites.
        * Unassigns subject teacher cleanly.
      - Faculty Workload Analysis:
        * Computes real-time workload for faculty: class teacher duties, subject teaching assignments, class breakdown.
      - Comprehensive Audit Logging for all assignments and reassignments.
    """

    @classmethod
    def get_eligible_faculty_queryset(cls):
        """
        Returns a distinct queryset of active users eligible for teaching assignments.
        Eligible faculty members include:
          - Active users with user_type in (TEACHER, STAFF, PRINCIPAL, ADMIN)
          - Active users assigned any role containing 'teacher' or 'faculty'
          - Active users with is_staff=True or is_superuser=True
        Explicitly excludes Students and Parents.
        Does NOT restrict by role scope (GLOBAL, CLASS, SECTION, OWN_ONLY), ensuring all
        institutional teachers are eligible to be assigned as Class Teachers or Subject Teachers.
        """
        eligible_types = [
            UserType.TEACHER,
            UserType.STAFF,
            UserType.PRINCIPAL,
            UserType.ADMIN,
        ]
        return User.objects.filter(
            is_active=True
        ).filter(
            Q(user_type__in=eligible_types)
            | Q(role_assignments__role__code__icontains='teacher', role_assignments__is_active=True)
            | Q(role_assignments__role__name__icontains='teacher', role_assignments__is_active=True)
            | Q(role_assignments__role__code__icontains='faculty', role_assignments__is_active=True)
            | Q(is_staff=True)
            | Q(is_superuser=True)
        ).exclude(
            user_type__in=[UserType.STUDENT, UserType.PARENT]
        ).distinct().order_by('first_name', 'last_name', 'username')

    @classmethod
    def is_eligible_faculty(cls, user):
        """
        Validates whether a specific user instance is eligible to be assigned as
        a Class Teacher or Subject Teacher.
        """
        if not user or not user.is_active:
            return False

        if getattr(user, 'user_type', None) in [UserType.STUDENT, UserType.PARENT]:
            return False

        if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
            return True

        if getattr(user, 'user_type', None) in [
            UserType.TEACHER,
            UserType.STAFF,
            UserType.PRINCIPAL,
            UserType.ADMIN,
        ]:
            return True

        # Check dynamic role assignments
        if hasattr(user, 'role_assignments'):
            return user.role_assignments.filter(
                Q(role__code__icontains='teacher') |
                Q(role__name__icontains='teacher') |
                Q(role__code__icontains='faculty'),
                is_active=True
            ).exists()

        return False

    @classmethod
    @transaction.atomic
    def assign_class_teacher(
        cls,
        section_id,
        teacher_id,
        actor=None,
        reason="",
        request=None,
        force=False
    ):
        """
        Assigns an active staff user as the Class Teacher of a Section.
        Validates active staff status and prevents accidental multiple section leadership.
        """
        try:
            section = Section.objects.select_for_update().select_related('school_class', 'class_teacher').get(pk=section_id)
        except Section.DoesNotExist:
            raise ResourceNotFoundError(f"Section with ID {section_id} not found.")

        try:
            teacher = User.objects.get(pk=teacher_id)
        except User.DoesNotExist:
            raise ResourceNotFoundError(f"User with ID {teacher_id} not found.")

        if not teacher.is_active:
            raise ValidationError({'teacher': _(f"Cannot assign inactive user '{teacher.get_full_name() or teacher.username}' as class teacher.")})

        if not cls.is_eligible_faculty(teacher):
            raise ValidationError({'teacher': _(f"User '{teacher.get_full_name() or teacher.username}' is not eligible for teaching assignments (must be teacher or staff member).")})

        # Check if already class teacher of another active section
        existing_sections = Section.objects.filter(class_teacher=teacher, is_active=True).exclude(pk=section.pk)
        if existing_sections.exists() and not force:
            section_names = ", ".join(str(s) for s in existing_sections)
            raise ValidationError({
                'teacher': _(
                    f"Teacher '{teacher.get_full_name() or teacher.username}' is already the class teacher of {section_names}. "
                    "In CBSE schools, a teacher is typically class teacher of at most one section."
                )
            })

        old_teacher = section.class_teacher
        old_teacher_name = (old_teacher.get_full_name() or old_teacher.username) if old_teacher else "None"
        new_teacher_name = teacher.get_full_name() or teacher.username

        section.class_teacher = teacher
        section.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module="academics",
            entity_type="Section",
            entity_id=str(section.pk),
            actor=actor,
            reason=reason or f"Assigned class teacher {new_teacher_name} to {section}",
            changes={
                "class_teacher": {
                    "old": old_teacher_name,
                    "new": new_teacher_name
                },
                "section": str(section)
            },
            request=request,
        )
        return section

    @classmethod
    @transaction.atomic
    def remove_class_teacher(
        cls,
        section_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Removes the designated Class Teacher from a Section.
        """
        try:
            section = Section.objects.select_for_update().select_related('school_class', 'class_teacher').get(pk=section_id)
        except Section.DoesNotExist:
            raise ResourceNotFoundError(f"Section with ID {section_id} not found.")

        if not section.class_teacher:
            return section

        old_teacher = section.class_teacher
        old_teacher_name = old_teacher.get_full_name() or old_teacher.username

        section.class_teacher = None
        section.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module="academics",
            entity_type="Section",
            entity_id=str(section.pk),
            actor=actor,
            reason=reason or f"Removed class teacher from {section}",
            changes={
                "class_teacher": {
                    "old": old_teacher_name,
                    "new": None
                },
                "section": str(section)
            },
            request=request,
        )
        return section

    @classmethod
    @transaction.atomic
    def assign_subject_teacher(
        cls,
        class_subject_id,
        teacher_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Assigns an active teaching staff member to a specific ClassSubject mapping.
        """
        try:
            class_sub = ClassSubject.objects.select_for_update().select_related(
                'section', 'section__school_class', 'subject', 'teacher'
            ).get(pk=class_subject_id)
        except ClassSubject.DoesNotExist:
            raise ResourceNotFoundError(f"ClassSubject mapping with ID {class_subject_id} not found.")

        try:
            teacher = User.objects.get(pk=teacher_id)
        except User.DoesNotExist:
            raise ResourceNotFoundError(f"User with ID {teacher_id} not found.")

        if not teacher.is_active:
            raise ValidationError({'teacher': _(f"Cannot assign inactive user '{teacher.get_full_name() or teacher.username}' as subject teacher.")})

        if not cls.is_eligible_faculty(teacher):
            raise ValidationError({'teacher': _(f"User '{teacher.get_full_name() or teacher.username}' is not eligible for teaching assignments (must be teacher or staff member).")})

        old_teacher = class_sub.teacher
        old_teacher_name = (old_teacher.get_full_name() or old_teacher.username) if old_teacher else "None"
        new_teacher_name = teacher.get_full_name() or teacher.username

        class_sub.teacher = teacher
        class_sub.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module="academics",
            entity_type="ClassSubject",
            entity_id=str(class_sub.pk),
            actor=actor,
            reason=reason or f"Assigned {new_teacher_name} as teacher for {class_sub.subject.name} in {class_sub.section}",
            changes={
                "teacher": {
                    "old": old_teacher_name,
                    "new": new_teacher_name
                },
                "mapping": str(class_sub)
            },
            request=request,
        )
        return class_sub

    @classmethod
    @transaction.atomic
    def bulk_assign_subject_teacher(
        cls,
        class_subject_ids,
        teacher_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Bulk assigns a faculty member to multiple ClassSubject mappings simultaneously.
        """
        if not class_subject_ids:
            raise ValidationError({'class_subjects': _("At least one subject mapping must be selected.")})

        try:
            teacher = User.objects.get(pk=teacher_id)
        except User.DoesNotExist:
            raise ResourceNotFoundError(f"User with ID {teacher_id} not found.")

        if not teacher.is_active:
            raise ValidationError({'teacher': _(f"Cannot assign inactive user '{teacher.get_full_name() or teacher.username}'.")})

        if not cls.is_eligible_faculty(teacher):
            raise ValidationError({'teacher': _(f"User '{teacher.get_full_name() or teacher.username}' is not eligible for teaching assignments.")})

        mappings = list(ClassSubject.objects.select_for_update().filter(pk__in=class_subject_ids, is_active=True))
        if not mappings:
            raise ValidationError({'class_subjects': _("None of the selected subject mappings are active or valid.")})

        new_teacher_name = teacher.get_full_name() or teacher.username

        for m in mappings:
            m.teacher = teacher
            m.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module="academics",
            entity_type="ClassSubject",
            entity_id=str(teacher.pk),
            actor=actor,
            reason=reason or f"Bulk assigned {new_teacher_name} to {len(mappings)} subject mapping(s)",
            changes={
                "teacher": new_teacher_name,
                "mappings_count": len(mappings)
            },
            request=request,
        )
        return len(mappings)

    @classmethod
    @transaction.atomic
    def remove_subject_teacher(
        cls,
        class_subject_id,
        actor=None,
        reason="",
        request=None
    ):
        """
        Clears the subject teacher from a ClassSubject mapping.
        """
        try:
            class_sub = ClassSubject.objects.select_for_update().select_related(
                'section', 'section__school_class', 'subject', 'teacher'
            ).get(pk=class_subject_id)
        except ClassSubject.DoesNotExist:
            raise ResourceNotFoundError(f"ClassSubject mapping with ID {class_subject_id} not found.")

        if not class_sub.teacher:
            return class_sub

        old_teacher = class_sub.teacher
        old_teacher_name = old_teacher.get_full_name() or old_teacher.username

        class_sub.teacher = None
        class_sub.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module="academics",
            entity_type="ClassSubject",
            entity_id=str(class_sub.pk),
            actor=actor,
            reason=reason or f"Unassigned teacher from {class_sub.subject.name} in {class_sub.section}",
            changes={
                "teacher": {
                    "old": old_teacher_name,
                    "new": None
                },
                "mapping": str(class_sub)
            },
            request=request,
        )
        return class_sub

    @classmethod
    def get_faculty_workload_summary(cls):
        """
        Generates a consolidated workload summary for all active teaching staff.
        Includes Class Teacher responsibilities and all assigned teaching subjects.
        """
        teachers = cls.get_eligible_faculty_queryset()
        workload = []

        for t in teachers:
            class_teacher_sections = list(t.class_teacher_sections.filter(is_active=True).select_related('school_class'))
            subject_mappings = list(
                t.teaching_subjects.filter(is_active=True).select_related('section', 'section__school_class', 'subject')
            )

            distinct_classes = sorted({m.section.school_class.name for m in subject_mappings})
            total_subjects = len(subject_mappings)

            # Intensity heuristic
            if total_subjects >= 6:
                intensity = 'Heavy'
                badge_class = 'danger'
            elif total_subjects >= 3:
                intensity = 'Normal'
                badge_class = 'primary'
            elif total_subjects > 0 or class_teacher_sections:
                intensity = 'Light'
                badge_class = 'info'
            else:
                intensity = 'Available'
                badge_class = 'secondary'

            workload.append({
                'teacher': t,
                'class_teacher_sections': class_teacher_sections,
                'subject_mappings': subject_mappings,
                'total_subjects': total_subjects,
                'distinct_classes': distinct_classes,
                'intensity': intensity,
                'badge_class': badge_class,
            })

        return workload

    @classmethod
    def list_sections_with_class_teachers(cls, school_class_id=None, stream=None, has_teacher=None, search=None):
        """
        Returns sections with class teacher details, ordered by class and section.
        """
        qs = Section.objects.select_related('school_class', 'class_teacher').filter(is_active=True).order_by(
            'school_class__order', 'name'
        )
        if school_class_id:
            qs = qs.filter(school_class_id=school_class_id)
        if stream:
            qs = qs.filter(stream=stream)
        if has_teacher is True:
            qs = qs.filter(class_teacher__isnull=False)
        elif has_teacher is False:
            qs = qs.filter(class_teacher__isnull=True)
        if search:
            search = search.strip()
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(school_class__name__icontains=search) |
                Q(class_teacher__first_name__icontains=search) |
                Q(class_teacher__last_name__icontains=search) |
                Q(class_teacher__username__icontains=search)
            )
        return qs

    @classmethod
    def list_subject_assignments(
        cls,
        school_class_id=None,
        section_id=None,
        teacher_id=None,
        subject_id=None,
        unassigned_only=False,
        search=None
    ):
        """
        Returns ClassSubject mappings with teacher details.
        """
        qs = ClassSubject.objects.select_related(
            'section', 'section__school_class', 'subject', 'teacher', 'academic_year'
        ).filter(is_active=True).order_by('section__school_class__order', 'section__name', 'subject__name')

        if school_class_id:
            qs = qs.filter(section__school_class_id=school_class_id)
        if section_id:
            qs = qs.filter(section_id=section_id)
        if teacher_id:
            qs = qs.filter(teacher_id=teacher_id)
        if subject_id:
            qs = qs.filter(subject_id=subject_id)
        if unassigned_only:
            qs = qs.filter(teacher__isnull=True)
        if search:
            search = search.strip()
            qs = qs.filter(
                Q(subject__name__icontains=search) |
                Q(subject__code__icontains=search) |
                Q(section__name__icontains=search) |
                Q(section__school_class__name__icontains=search) |
                Q(teacher__first_name__icontains=search) |
                Q(teacher__last_name__icontains=search) |
                Q(teacher__username__icontains=search)
            )
        return qs


class TimetablePeriodService:
    """
    Manages Timetable Periods (the institutional bell schedule).
    Enforces time order, period sequencing, overlap protection, and audit logging.
    """

    @classmethod
    @transaction.atomic
    def create_period(
        cls,
        period_number,
        start_time,
        end_time,
        name="Period",
        is_break=False,
        actor=None,
        reason="",
        request=None
    ):
        try:
            p_num = int(period_number)
            if p_num <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValidationError({'period_number': _("Period number must be a positive integer (>= 1).")})

        if TimetablePeriod.objects.filter(period_number=p_num).exists():
            raise ValidationError({'period_number': _(f"Period number {p_num} already exists.")})

        if isinstance(start_time, str):
            start_time = cls._parse_time(start_time, 'start_time')
        if isinstance(end_time, str):
            end_time = cls._parse_time(end_time, 'end_time')

        if start_time >= end_time:
            raise ValidationError({'end_time': _("Period end time must be after start time.")})

        cls._validate_time_overlap(start_time, end_time)

        period = TimetablePeriod(
            period_number=p_num,
            name=str(name or "Period").strip(),
            start_time=start_time,
            end_time=end_time,
            is_break=bool(is_break)
        )
        period.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="TimetablePeriod",
            entity_id=str(period.pk),
            actor=actor,
            reason=reason or f"Created timetable period {period}",
            changes={
                "period_number": period.period_number,
                "name": period.name,
                "start_time": str(period.start_time),
                "end_time": str(period.end_time),
                "is_break": period.is_break,
            },
            request=request,
        )
        return period

    @classmethod
    @transaction.atomic
    def update_period(
        cls,
        period_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        try:
            period = TimetablePeriod.objects.select_for_update().get(pk=period_id)
        except TimetablePeriod.DoesNotExist:
            raise ResourceNotFoundError(f"Timetable period with ID {period_id} not found.")

        old_state = {
            "period_number": period.period_number,
            "name": period.name,
            "start_time": str(period.start_time),
            "end_time": str(period.end_time),
            "is_break": period.is_break,
        }

        if 'period_number' in kwargs:
            p_num = int(kwargs['period_number'])
            if p_num <= 0:
                raise ValidationError({'period_number': _("Period number must be a positive integer.")})
            if TimetablePeriod.objects.filter(period_number=p_num).exclude(pk=period.pk).exists():
                raise ValidationError({'period_number': _(f"Period number {p_num} already exists.")})
            period.period_number = p_num

        if 'name' in kwargs:
            period.name = str(kwargs['name']).strip()

        if 'start_time' in kwargs:
            st = kwargs['start_time']
            if isinstance(st, str):
                st = cls._parse_time(st, 'start_time')
            period.start_time = st

        if 'end_time' in kwargs:
            et = kwargs['end_time']
            if isinstance(et, str):
                et = cls._parse_time(et, 'end_time')
            period.end_time = et

        if 'is_break' in kwargs:
            period.is_break = bool(kwargs['is_break'])

        if period.start_time >= period.end_time:
            raise ValidationError({'end_time': _("Period end time must be after start time.")})

        cls._validate_time_overlap(period.start_time, period.end_time, exclude_period_id=period.pk)

        period.save()

        new_state = {
            "period_number": period.period_number,
            "name": period.name,
            "start_time": str(period.start_time),
            "end_time": str(period.end_time),
            "is_break": period.is_break,
        }
        changes = {k: {"old": old_state[k], "new": new_state[k]} for k in old_state if old_state[k] != new_state[k]}

        if changes:
            AuditService.log(
                action=AuditAction.UPDATE,
                module="academics",
                entity_type="TimetablePeriod",
                entity_id=str(period.pk),
                actor=actor,
                reason=reason or f"Updated timetable period {period}",
                changes=changes,
                request=request,
            )
        return period

    @classmethod
    @transaction.atomic
    def delete_period(cls, period_id, actor=None, reason="", request=None):
        try:
            period = TimetablePeriod.objects.select_for_update().get(pk=period_id)
        except TimetablePeriod.DoesNotExist:
            raise ResourceNotFoundError(f"Timetable period with ID {period_id} not found.")

        slot_count = TimetableSlot.objects.filter(period=period).count()
        if slot_count > 0:
            raise BusinessConflictError(
                f"Cannot delete period '{period.name} {period.period_number}' because {slot_count} timetable slot(s) "
                "are scheduled during this period. Delete or reassign the slots first."
            )

        period_id_str = str(period.pk)
        period_str = str(period)
        period.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="TimetablePeriod",
            entity_id=period_id_str,
            actor=actor,
            reason=reason or f"Deleted timetable period {period_str}",
            changes={"period": period_str},
            request=request,
        )

    @classmethod
    def get_period(cls, period_id):
        try:
            return TimetablePeriod.objects.get(pk=period_id)
        except TimetablePeriod.DoesNotExist:
            raise ResourceNotFoundError(f"Timetable period with ID {period_id} not found.")

    @classmethod
    def list_periods(cls):
        return TimetablePeriod.objects.all().order_by('period_number')

    @classmethod
    @transaction.atomic
    def bulk_create_cbse_standard_periods(cls, actor=None, request=None):
        if TimetablePeriod.objects.exists():
            raise BusinessConflictError("Timetable periods already exist. Clear existing periods before generating defaults.")

        default_schedule = [
            (1, "Morning Assembly", "08:00", "08:20", True),
            (2, "Period 1", "08:20", "09:05", False),
            (3, "Period 2", "09:05", "09:50", False),
            (4, "Period 3", "09:50", "10:35", False),
            (5, "Short Break", "10:35", "10:55", True),
            (6, "Period 4", "10:55", "11:40", False),
            (7, "Period 5", "11:40", "12:25", False),
            (8, "Lunch Break", "12:25", "13:05", True),
            (9, "Period 6", "13:05", "13:50", False),
            (10, "Period 7", "13:50", "14:30", False),
            (11, "Period 8", "14:30", "15:10", False),
        ]
        created = []
        for p_num, name, st, et, is_break in default_schedule:
            p = cls.create_period(
                period_number=p_num,
                name=name,
                start_time=st,
                end_time=et,
                is_break=is_break,
                actor=actor,
                reason="Generated CBSE standard default bell schedule",
                request=request
            )
            created.append(p)
        return created

    @classmethod
    def _parse_time(cls, time_str, field_name):
        from datetime import datetime
        time_str = str(time_str).strip()
        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                return datetime.strptime(time_str, fmt).time()
            except ValueError:
                pass
        raise ValidationError({field_name: _(f"Invalid time format '{time_str}'. Use HH:MM or HH:MM:SS.")})

    @classmethod
    def _validate_time_overlap(cls, start_time, end_time, exclude_period_id=None):
        qs = TimetablePeriod.objects.all()
        if exclude_period_id:
            qs = qs.exclude(pk=exclude_period_id)
        for p in qs:
            if start_time < p.end_time and end_time > p.start_time:
                raise ValidationError({
                    'start_time': _(
                        f"Period times ({start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}) "
                        f"overlap with existing period '{p.name} {p.period_number}' ({p.start_time.strftime('%H:%M')} - {p.end_time.strftime('%H:%M')})."
                    )
                })


class TimetableService:
    """
    Timetable & Scheduling Service for School ERP (Academics Module Phase 4).
    Enforces collision detection (Section, Teacher, Room, Break time),
    matrix construction, schedule querying, and audit logging.
    """

    @classmethod
    @transaction.atomic
    def create_slot(
        cls,
        day_of_week,
        period_id,
        class_subject_id,
        academic_year_id=None,
        room="",
        actor=None,
        reason="",
        request=None
    ):
        if academic_year_id:
            try:
                academic_year = AcademicYear.objects.get(pk=academic_year_id)
            except AcademicYear.DoesNotExist:
                raise ResourceNotFoundError(f"AcademicYear with ID {academic_year_id} not found.")
        else:
            academic_year = AcademicYearService.get_current_year()
            if not academic_year:
                raise ValidationError({'academic_year': _("No active academic year found. Please activate an academic year first.")})

        try:
            day_num = int(day_of_week)
            if day_num not in dict(TimetableSlot.DAYS_OF_WEEK):
                raise ValueError()
        except (ValueError, TypeError):
            valid_days = ", ".join(f"{k}={v}" for k, v in TimetableSlot.DAYS_OF_WEEK)
            raise ValidationError({'day_of_week': _(f"Invalid day_of_week. Must be one of: {valid_days}")})

        try:
            period = TimetablePeriod.objects.get(pk=period_id)
        except TimetablePeriod.DoesNotExist:
            raise ResourceNotFoundError(f"TimetablePeriod with ID {period_id} not found.")

        if period.is_break:
            raise ValidationError({'period': _(f"Cannot schedule academic classes during break period '{period.name}'.")})

        try:
            class_sub = ClassSubject.objects.select_related(
                'section', 'section__school_class', 'subject', 'teacher'
            ).get(pk=class_subject_id)
        except ClassSubject.DoesNotExist:
            raise ResourceNotFoundError(f"ClassSubject mapping with ID {class_subject_id} not found.")

        if not class_sub.is_active:
            raise ValidationError({'class_subject': _("Cannot schedule an inactive subject mapping.")})

        cleaned_room = str(room or "").strip()
        if not cleaned_room and class_sub.section.room_number:
            cleaned_room = class_sub.section.room_number

        AcademicsService.validate_slot_conflicts(
            academic_year=academic_year,
            day_of_week=day_num,
            period=period,
            class_subject=class_sub,
            room=cleaned_room
        )

        slot = TimetableSlot(
            academic_year=academic_year,
            day_of_week=day_num,
            period=period,
            class_subject=class_sub,
            room=cleaned_room
        )
        slot.save()

        day_name = dict(TimetableSlot.DAYS_OF_WEEK).get(day_num, str(day_num))
        teacher_name = (class_sub.teacher.get_full_name() or class_sub.teacher.username) if class_sub.teacher else "Unassigned"

        AuditService.log(
            action=AuditAction.CREATE,
            module="academics",
            entity_type="TimetableSlot",
            entity_id=str(slot.pk),
            actor=actor,
            reason=reason or f"Scheduled {class_sub.subject.name} for {class_sub.section} on {day_name} {period}",
            changes={
                "academic_year": academic_year.name,
                "day_of_week": day_name,
                "period": f"{period.name} {period.period_number}",
                "section": str(class_sub.section),
                "subject": class_sub.subject.name,
                "teacher": teacher_name,
                "room": cleaned_room,
            },
            request=request,
        )
        return slot

    @classmethod
    @transaction.atomic
    def update_slot(
        cls,
        slot_id,
        actor=None,
        reason="",
        request=None,
        **kwargs
    ):
        try:
            slot = TimetableSlot.objects.select_for_update().select_related(
                'academic_year', 'period', 'class_subject', 'class_subject__section', 'class_subject__subject', 'class_subject__teacher'
            ).get(pk=slot_id)
        except TimetableSlot.DoesNotExist:
            raise ResourceNotFoundError(f"Timetable slot with ID {slot_id} not found.")

        old_state = {
            "day_of_week": slot.day_of_week,
            "period_id": slot.period_id,
            "class_subject_id": slot.class_subject_id,
            "room": slot.room,
        }

        if 'day_of_week' in kwargs:
            day_num = int(kwargs['day_of_week'])
            if day_num not in dict(TimetableSlot.DAYS_OF_WEEK):
                raise ValidationError({'day_of_week': _("Invalid day_of_week.")})
            slot.day_of_week = day_num

        if 'period_id' in kwargs:
            period = TimetablePeriod.objects.get(pk=kwargs['period_id'])
            if period.is_break:
                raise ValidationError({'period': _(f"Cannot schedule classes during break period '{period.name}'.")})
            slot.period = period

        if 'class_subject_id' in kwargs:
            class_sub = ClassSubject.objects.select_related(
                'section', 'section__school_class', 'subject', 'teacher'
            ).get(pk=kwargs['class_subject_id'])
            if not class_sub.is_active:
                raise ValidationError({'class_subject': _("Cannot schedule an inactive subject mapping.")})
            slot.class_subject = class_sub

        if 'room' in kwargs:
            slot.room = str(kwargs['room'] or "").strip()

        AcademicsService.validate_slot_conflicts(
            academic_year=slot.academic_year,
            day_of_week=slot.day_of_week,
            period=slot.period,
            class_subject=slot.class_subject,
            room=slot.room,
            exclude_slot_id=slot.pk
        )

        slot.save()

        new_state = {
            "day_of_week": slot.day_of_week,
            "period_id": slot.period_id,
            "class_subject_id": slot.class_subject_id,
            "room": slot.room,
        }
        changes = {k: {"old": old_state[k], "new": new_state[k]} for k in old_state if old_state[k] != new_state[k]}

        if changes:
            AuditService.log(
                action=AuditAction.UPDATE,
                module="academics",
                entity_type="TimetableSlot",
                entity_id=str(slot.pk),
                actor=actor,
                reason=reason or f"Updated timetable slot {slot}",
                changes=changes,
                request=request,
            )
        return slot

    @classmethod
    @transaction.atomic
    def delete_slot(cls, slot_id, actor=None, reason="", request=None):
        try:
            slot = TimetableSlot.objects.select_for_update().select_related(
                'period', 'class_subject', 'class_subject__section', 'class_subject__subject'
            ).get(pk=slot_id)
        except TimetableSlot.DoesNotExist:
            raise ResourceNotFoundError(f"Timetable slot with ID {slot_id} not found.")

        slot_id_str = str(slot.pk)
        slot_desc = str(slot)
        slot.delete()

        AuditService.log(
            action=AuditAction.DELETE,
            module="academics",
            entity_type="TimetableSlot",
            entity_id=slot_id_str,
            actor=actor,
            reason=reason or f"Deleted timetable slot {slot_desc}",
            changes={"slot": slot_desc},
            request=request,
        )

    @classmethod
    def get_section_timetable_matrix(cls, section_id, academic_year_id=None):
        """
        Constructs the full weekly schedule matrix for a class section.
        Rows: Days of Week (Monday to Saturday)
        Columns: Timetable Periods
        Cells: TimetableSlot details or None (or break marker)
        """
        if not academic_year_id:
            ay = AcademicYearService.get_current_year()
        else:
            ay = AcademicYear.objects.filter(pk=academic_year_id).first()

        periods = list(TimetablePeriod.objects.all().order_by('period_number'))
        days = list(TimetableSlot.DAYS_OF_WEEK)

        slots = TimetableSlot.objects.filter(
            class_subject__section_id=section_id
        ).select_related(
            'period', 'class_subject', 'class_subject__subject', 'class_subject__teacher'
        )
        if ay:
            slots = slots.filter(academic_year=ay)

        slot_map = {(s.day_of_week, s.period_id): s for s in slots}

        grid = []
        total_scheduled = len(slots)
        subject_counts = {}

        for day_code, day_name in days:
            row_cells = []
            for p in periods:
                slot = slot_map.get((day_code, p.pk))
                if slot:
                    sub_name = slot.class_subject.subject.name
                    subject_counts[sub_name] = subject_counts.get(sub_name, 0) + 1

                row_cells.append({
                    'period': p,
                    'is_break': p.is_break,
                    'slot': slot,
                })

            grid.append({
                'day_code': day_code,
                'day_name': day_name,
                'periods': row_cells,
            })

        return {
            'days': days,
            'periods': periods,
            'grid': grid,
            'total_scheduled': total_scheduled,
            'subject_counts': subject_counts,
        }

    @classmethod
    def get_teacher_timetable_matrix(cls, teacher_id, academic_year_id=None):
        """
        Constructs a teacher's personal weekly schedule across all sections.
        """
        if not academic_year_id:
            ay = AcademicYearService.get_current_year()
        else:
            ay = AcademicYear.objects.filter(pk=academic_year_id).first()

        periods = list(TimetablePeriod.objects.all().order_by('period_number'))
        days = list(TimetableSlot.DAYS_OF_WEEK)

        slots = TimetableSlot.objects.filter(
            class_subject__teacher_id=teacher_id
        ).select_related(
            'period', 'class_subject', 'class_subject__section',
            'class_subject__section__school_class', 'class_subject__subject'
        )
        if ay:
            slots = slots.filter(academic_year=ay)

        slot_map = {(s.day_of_week, s.period_id): s for s in slots}

        grid = []
        total_scheduled = len(slots)
        sections_covered = set()

        for day_code, day_name in days:
            row_cells = []
            for p in periods:
                slot = slot_map.get((day_code, p.pk))
                if slot:
                    sections_covered.add(str(slot.class_subject.section))

                row_cells.append({
                    'period': p,
                    'is_break': p.is_break,
                    'slot': slot,
                })

            grid.append({
                'day_code': day_code,
                'day_name': day_name,
                'periods': row_cells,
            })

        return {
            'days': days,
            'periods': periods,
            'grid': grid,
            'total_scheduled': total_scheduled,
            'sections_covered': sorted(sections_covered),
        }
