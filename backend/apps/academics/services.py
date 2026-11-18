from datetime import date
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.academics.models import (
    AcademicStream, ClassSubject, SchoolClass, Section, Stream, Subject, SubjectType, TimetableSlot
)
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

        from apps.academics.models import (
    AcademicStream, ClassSubject, SchoolClass, Section, Stream, Subject, SubjectType, TimetableSlot
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
            from apps.identity.models import User
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
                from apps.identity.models import User
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
