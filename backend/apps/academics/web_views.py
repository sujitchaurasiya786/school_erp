"""
Academics Web Views — Dashboard views for Academic Years and Class Management.
Provides intuitive web interfaces for school administrators and principals.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.academics.models import (
    AcademicStream, ClassSubject, Room, RoomType, SchoolClass, Section, Stream, Subject, SubjectType,
    TimetablePeriod, TimetableSlot
)
from apps.academics.services import (
    AcademicYearService, ClassSubjectService, RoomService, SchoolClassService, SectionService, StreamService, SubjectService,
    TeacherAssignmentService, TimetablePeriodService, TimetableService
)
from apps.rbac.services import RBACService
from apps.school_config.models import AcademicYear, SchoolProfile
from common.exceptions import BusinessConflictError, ResourceNotFoundError


def _check_academics_permission(request, codename):
    """
    Enforces dynamic RBAC permission for academics dashboard views.
    Django superuser (is_superuser=True) has permanent system administrator access.
    """
    if not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True
    return RBACService.has_permission(request.user, codename)


# ==============================================================================
# 1. Academic Years Management
# ==============================================================================
@login_required(login_url='login')
def academic_years_list_view(request):
    """
    Displays the list of all academic years, metrics, and session creation modal.
    """
    if not _check_academics_permission(request, 'academics.year.view'):
        messages.error(request, "You do not have permission to view academic years.")
        return redirect('home-dashboard')

    if request.method == 'POST':
        if not _check_academics_permission(request, 'academics.year.manage'):
            messages.error(request, "You do not have permission to manage academic years.")
            return redirect('academics-years-list')

        action = request.POST.get('action', 'create')
        if action == 'create':
            name = request.POST.get('name', '').strip()
            start_date = request.POST.get('start_date', '').strip()
            end_date = request.POST.get('end_date', '').strip()
            is_current = request.POST.get('is_current') == 'on'
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                year = AcademicYearService.create_academic_year(
                    name=name,
                    start_date_val=start_date,
                    end_date_val=end_date,
                    is_current=is_current,
                    is_active=is_active,
                    actor=request.user,
                    reason=reason or f"Created academic year {name} from dashboard",
                    request=request,
                )
                messages.success(request, f"Academic year '{year.name}' created successfully.")
            except (ValidationError, ValueError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to create academic year: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-years-list')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    current_year = AcademicYear.objects.filter(is_current=True).first()
    total_years = academic_years.count()
    active_years_count = academic_years.filter(is_active=True).count()

    context = {
        'academic_years': academic_years,
        'current_year': current_year,
        'total_years': total_years,
        'active_years_count': active_years_count,
        'can_manage': _check_academics_permission(request, 'academics.year.manage'),
        'can_activate': _check_academics_permission(request, 'academics.year.activate'),
    }
    return render(request, 'dashboard/academics/academic_years_list.html', context)


@login_required(login_url='login')
@require_POST
def academic_year_activate_view(request, pk):
    """
    Switches and activates the selected academic year as the current session.
    """
    if not _check_academics_permission(request, 'academics.year.activate'):
        messages.error(request, "You do not have permission to activate academic years.")
        return redirect('academics-years-list')

    reason = request.POST.get('reason', 'Switched session from dashboard').strip()
    try:
        year = AcademicYearService.set_current_year(
            academic_year_id=pk,
            actor=request.user,
            reason=reason,
            request=request,
        )
        messages.success(request, f"Academic session '{year.name}' is now active across the school ERP.")
    except (ValidationError, ResourceNotFoundError) as err:
        msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
        messages.error(request, f"Activation failed: {msg}")
    except Exception as err:
        messages.error(request, f"An unexpected error occurred: {err}")

    return redirect('academics-years-list')


# ==============================================================================
# 2. Class Management
# ==============================================================================
@login_required(login_url='login')
def classes_list_view(request):
    """
    Displays the class directory, sequence order, section counts, and management modals.
    """
    if not _check_academics_permission(request, 'academics.class.view'):
        messages.error(request, "You do not have permission to view classes.")
        return redirect('home-dashboard')

    if request.method == 'POST':
        if not _check_academics_permission(request, 'academics.class.manage'):
            messages.error(request, "You do not have permission to manage classes.")
            return redirect('academics-classes-list')

        action = request.POST.get('action', 'create')

        if action == 'create':
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip()
            order = request.POST.get('order', '1').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                c = SchoolClassService.create_class(
                    name=name,
                    code=code,
                    order=int(order) if order.isdigit() else 1,
                    is_active=is_active,
                    actor=request.user,
                    reason=reason or f"Created class {name} from dashboard",
                    request=request,
                )
                messages.success(request, f"Class '{c.name}' ({c.code}) created successfully.")
            except ValidationError as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to create class: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-classes-list')

        elif action == 'update':
            class_id = request.POST.get('class_id')
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip()
            order = request.POST.get('order', '1').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                c = SchoolClassService.update_class(
                    class_id=class_id,
                    actor=request.user,
                    name=name,
                    code=code,
                    order=int(order) if order.isdigit() else 1,
                    is_active=is_active,
                    reason=reason or f"Updated class {name} from dashboard",
                    request=request,
                )
                messages.success(request, f"Class '{c.name}' updated successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to update class: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-classes-list')

        elif action == 'delete':
            class_id = request.POST.get('class_id')
            reason = request.POST.get('reason', '').strip()

            try:
                SchoolClassService.delete_class(
                    class_id=class_id,
                    actor=request.user,
                    reason=reason or "Deleted class from dashboard",
                    request=request,
                )
                messages.success(request, "Class deleted successfully.")
            except BusinessConflictError as err:
                messages.warning(request, str(err))
            except (ResourceNotFoundError, ValidationError) as err:
                messages.error(request, str(err))
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-classes-list')

    classes = SchoolClass.objects.annotate(
        section_count=Count('sections')
    ).order_by('order', 'name')

    total_classes = classes.count()
    active_classes_count = classes.filter(is_active=True).count()
    total_sections = Section.objects.count()

    context = {
        'classes': classes,
        'total_classes': total_classes,
        'active_classes_count': active_classes_count,
        'total_sections': total_sections,
        'can_manage': _check_academics_permission(request, 'academics.class.manage'),
    }
    return render(request, 'dashboard/academics/classes_list.html', context)


# ==============================================================================
# 3. Section Management
# ==============================================================================
@login_required(login_url='login')
def sections_list_view(request):
    """
    Displays the section directory, capacity metrics, and section management modals.
    Supports filtering by school class.
    """
    if not _check_academics_permission(request, 'academics.section.view'):
        messages.error(request, "You do not have permission to view sections.")
        return redirect('home-dashboard')

    if request.method == 'POST':
        if not _check_academics_permission(request, 'academics.section.manage'):
            messages.error(request, "You do not have permission to manage sections.")
            return redirect('academics-sections-list')

        action = request.POST.get('action', 'create')

        if action == 'create':
            school_class_id = request.POST.get('school_class_id')
            name = request.POST.get('name', '').strip()
            capacity = request.POST.get('capacity', '40').strip()
            stream = request.POST.get('stream', Stream.NONE).strip()
            room_number = request.POST.get('room_number', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                sec = SectionService.create_section(
                    school_class_id=school_class_id,
                    name=name,
                    capacity=int(capacity) if capacity.isdigit() else 40,
                    stream=stream,
                    room_number=room_number,
                    is_active=is_active,
                    actor=request.user,
                    reason=reason or f"Created section {name} from dashboard",
                    request=request,
                )
                messages.success(request, f"Section '{sec.name}' created successfully for {sec.school_class.name}.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to create section: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-sections-list')

        elif action == 'update':
            section_id = request.POST.get('section_id')
            school_class_id = request.POST.get('school_class_id')
            name = request.POST.get('name', '').strip()
            capacity = request.POST.get('capacity', '40').strip()
            stream = request.POST.get('stream', Stream.NONE).strip()
            room_number = request.POST.get('room_number', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            update_data = {
                'name': name,
                'capacity': int(capacity) if capacity.isdigit() else 40,
                'stream': stream,
                'room_number': room_number,
                'is_active': is_active,
            }
            if school_class_id:
                update_data['school_class_id'] = school_class_id

            try:
                sec = SectionService.update_section(
                    section_id=section_id,
                    actor=request.user,
                    reason=reason or f"Updated section {name} from dashboard",
                    request=request,
                    **update_data
                )
                messages.success(request, f"Section '{sec.name}' updated successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to update section: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-sections-list')

        elif action == 'delete':
            section_id = request.POST.get('section_id')
            reason = request.POST.get('reason', '').strip()

            try:
                SectionService.delete_section(
                    section_id=section_id,
                    actor=request.user,
                    reason=reason or "Deleted section from dashboard",
                    request=request,
                )
                messages.success(request, "Section deleted successfully.")
            except BusinessConflictError as err:
                messages.warning(request, str(err))
            except (ResourceNotFoundError, ValidationError) as err:
                messages.error(request, str(err))
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-sections-list')

    # Query params
    selected_class_id = request.GET.get('class_id', '').strip()

    sections_qs = Section.objects.select_related('school_class', 'class_teacher').all().order_by(
        'school_class__order', 'name'
    )
    if selected_class_id and selected_class_id.isdigit():
        sections_qs = sections_qs.filter(school_class_id=int(selected_class_id))

    school_classes = SchoolClass.objects.filter(is_active=True).order_by('order', 'name')
    total_sections = Section.objects.count()
    active_sections_count = Section.objects.filter(is_active=True).count()
    total_capacity = Section.objects.filter(is_active=True).aggregate(tot=Sum('capacity'))['tot'] or 0
    classes_covered = Section.objects.values('school_class').distinct().count()

    context = {
        'sections': sections_qs,
        'school_classes': school_classes,
        'selected_class_id': int(selected_class_id) if selected_class_id.isdigit() else None,
        'total_sections': total_sections,
        'active_sections_count': active_sections_count,
        'total_capacity': total_capacity,
        'classes_covered': classes_covered,
        'streams': Stream.choices,
        'can_manage': _check_academics_permission(request, 'academics.section.manage'),
    }
    return render(request, 'dashboard/academics/sections_list.html', context)


# ==============================================================================
# 4. Stream Management (Classes 11–12)
# ==============================================================================
@login_required(login_url='login')
def streams_list_view(request):
    """
    Displays the stream directory, senior class offerings, and stream management modals.
    Enforces stream configuration exclusively for Classes 11 and 12.
    """
    if not _check_academics_permission(request, 'academics.stream.view'):
        messages.error(request, "You do not have permission to view streams.")
        return redirect('home-dashboard')

    if request.method == 'POST':
        if not _check_academics_permission(request, 'academics.stream.manage'):
            messages.error(request, "You do not have permission to manage streams.")
            return redirect('academics-streams-list')

        action = request.POST.get('action', 'create')

        if action == 'create':
            school_class_id = request.POST.get('school_class_id')
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                st = StreamService.create_stream(
                    school_class_id=school_class_id,
                    name=name,
                    code=code,
                    description=description,
                    is_active=is_active,
                    actor=request.user,
                    reason=reason or f"Created stream {name} ({code}) from dashboard",
                    request=request,
                )
                messages.success(request, f"Stream '{st.name}' ({st.code}) configured successfully for {st.school_class.name}.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to create stream: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-streams-list')

        elif action == 'update':
            stream_id = request.POST.get('stream_id')
            school_class_id = request.POST.get('school_class_id')
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            update_data = {
                'name': name,
                'code': code,
                'description': description,
                'is_active': is_active,
            }
            if school_class_id:
                update_data['school_class_id'] = school_class_id

            try:
                st = StreamService.update_stream(
                    stream_id=stream_id,
                    actor=request.user,
                    reason=reason or f"Updated stream {name} from dashboard",
                    request=request,
                    **update_data
                )
                messages.success(request, f"Stream '{st.name}' updated successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to update stream: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-streams-list')

        elif action == 'delete':
            stream_id = request.POST.get('stream_id')
            reason = request.POST.get('reason', '').strip()

            try:
                StreamService.delete_stream(
                    stream_id=stream_id,
                    actor=request.user,
                    reason=reason or "Deleted stream from dashboard",
                    request=request,
                )
                messages.success(request, "Stream deleted successfully.")
            except BusinessConflictError as err:
                messages.warning(request, str(err))
            except (ResourceNotFoundError, ValidationError) as err:
                messages.error(request, str(err))
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-streams-list')

    selected_class_id = request.GET.get('class_id', '').strip()

    streams_qs = AcademicStream.objects.select_related('school_class').all().order_by(
        'school_class__order', 'name'
    )
    if selected_class_id and selected_class_id.isdigit():
        streams_qs = streams_qs.filter(school_class_id=int(selected_class_id))

    all_active_classes = SchoolClass.objects.filter(is_active=True).order_by('order', 'name')
    senior_classes = [c for c in all_active_classes if c.is_senior_secondary]

    total_streams = AcademicStream.objects.count()
    active_streams_count = AcademicStream.objects.filter(is_active=True).count()
    senior_classes_covered = AcademicStream.objects.values('school_class').distinct().count()

    context = {
        'streams': streams_qs,
        'senior_classes': senior_classes,
        'selected_class_id': int(selected_class_id) if selected_class_id.isdigit() else None,
        'total_streams': total_streams,
        'active_streams_count': active_streams_count,
        'senior_classes_covered': senior_classes_covered,
        'can_manage': _check_academics_permission(request, 'academics.stream.manage'),
    }
    return render(request, 'dashboard/academics/streams_list.html', context)


# ==============================================================================
# 5. Subject Master Management
# ==============================================================================
@login_required(login_url='login')
def subjects_list_view(request):
    """
    Displays the central subject master catalog, metrics, and subject CRUD modals.
    Enforces component requirements (Theory/Practical) and subject types.
    """
    if not _check_academics_permission(request, 'academics.subjects.view'):
        messages.error(request, "You do not have permission to view subjects.")
        return redirect('home-dashboard')

    if request.method == 'POST':
        if not _check_academics_permission(request, 'academics.subjects.manage'):
            messages.error(request, "You do not have permission to manage subjects.")
            return redirect('academics-subjects-list')

        action = request.POST.get('action', 'create')

        if action == 'create':
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip()
            subject_type = request.POST.get('subject_type', SubjectType.COMPULSORY).strip()
            is_theory = request.POST.get('is_theory') == 'on'
            is_practical = request.POST.get('is_practical') == 'on'
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                sub = SubjectService.create_subject(
                    name=name,
                    code=code,
                    subject_type=subject_type,
                    is_theory=is_theory,
                    is_practical=is_practical,
                    description=description,
                    is_active=is_active,
                    actor=request.user,
                    reason=reason or f"Created subject {name} ({code}) from dashboard",
                    request=request,
                )
                messages.success(request, f"Subject '{sub.name}' ({sub.code}) created successfully.")
            except ValidationError as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to create subject: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-subjects-list')

        elif action == 'update':
            subject_id = request.POST.get('subject_id')
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip()
            subject_type = request.POST.get('subject_type', SubjectType.COMPULSORY).strip()
            is_theory = request.POST.get('is_theory') == 'on'
            is_practical = request.POST.get('is_practical') == 'on'
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                sub = SubjectService.update_subject(
                    subject_id=subject_id,
                    actor=request.user,
                    name=name,
                    code=code,
                    subject_type=subject_type,
                    is_theory=is_theory,
                    is_practical=is_practical,
                    description=description,
                    is_active=is_active,
                    reason=reason or f"Updated subject {name} from dashboard",
                    request=request,
                )
                messages.success(request, f"Subject '{sub.name}' updated successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to update subject: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-subjects-list')

        elif action == 'delete':
            subject_id = request.POST.get('subject_id')
            reason = request.POST.get('reason', '').strip()

            try:
                SubjectService.delete_subject(
                    subject_id=subject_id,
                    actor=request.user,
                    reason=reason or "Deleted subject from dashboard",
                    request=request,
                )
                messages.success(request, "Subject deleted successfully.")
            except BusinessConflictError as err:
                messages.warning(request, str(err))
            except (ResourceNotFoundError, ValidationError) as err:
                messages.error(request, str(err))
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-subjects-list')

    # Query params for filtering & search
    selected_type = request.GET.get('type', '').strip()
    search_query = request.GET.get('q', '').strip()

    subjects_qs = Subject.objects.all().order_by('name', 'code')
    if selected_type and selected_type in dict(SubjectType.choices):
        subjects_qs = subjects_qs.filter(subject_type=selected_type)
    if search_query:
        from django.db.models import Q
        subjects_qs = subjects_qs.filter(Q(name__icontains=search_query) | Q(code__icontains=search_query))

    total_subjects = Subject.objects.count()
    active_subjects_count = Subject.objects.filter(is_active=True).count()
    theory_subjects_count = Subject.objects.filter(is_theory=True).count()
    practical_subjects_count = Subject.objects.filter(is_practical=True).count()

    context = {
        'subjects': subjects_qs,
        'subject_types': SubjectType.choices,
        'selected_type': selected_type,
        'search_query': search_query,
        'total_subjects': total_subjects,
        'active_subjects_count': active_subjects_count,
        'theory_subjects_count': theory_subjects_count,
        'practical_subjects_count': practical_subjects_count,
        'can_manage': _check_academics_permission(request, 'academics.subjects.manage'),
    }
    return render(request, 'dashboard/academics/subjects_list.html', context)


# ==============================================================================
# 6. Class–Subject Mapping Management
# ==============================================================================
@login_required(login_url='login')
def class_subjects_list_view(request):
    """
    Displays the list of subjects mapped to classes and sections, with metrics,
    filtering by class/section/stream/type, single mapping modal, and bulk mapping to class/stream modal.
    """
    can_view = _check_academics_permission(request, 'academics.class_subject.view') or \
               _check_academics_permission(request, 'academics.subjects.view')
    if not can_view:
        messages.error(request, "You do not have permission to view class-subject mappings.")
        return redirect('home-dashboard')

    can_manage = _check_academics_permission(request, 'academics.class_subject.manage') or \
                 _check_academics_permission(request, 'academics.subjects.manage')

    if request.method == 'POST':
        if not can_manage:
            messages.error(request, "You do not have permission to manage class-subject mappings.")
            return redirect('academics-class-subjects-list')

        action = request.POST.get('action', 'create')

        if action == 'create':
            section_id = request.POST.get('section_id')
            subject_id = request.POST.get('subject_id')
            subject_type = request.POST.get('subject_type') or SubjectType.COMPULSORY
            teacher_id = request.POST.get('teacher_id') or None
            academic_year_id = request.POST.get('academic_year_id') or None
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                mapping = ClassSubjectService.create_mapping(
                    section_id=section_id,
                    subject_id=subject_id,
                    subject_type=subject_type,
                    academic_year_id=academic_year_id,
                    teacher_id=teacher_id,
                    is_active=is_active,
                    actor=request.user,
                    reason=reason or f"Mapped subject from dashboard",
                    request=request,
                )
                messages.success(
                    request,
                    f"Subject '{mapping.subject.name}' mapped to '{mapping.section}' successfully."
                )
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to map subject: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-class-subjects-list')

        elif action == 'bulk_map':
            school_class_id = request.POST.get('school_class_id')
            stream = request.POST.get('stream', '').strip() or None
            section_id = request.POST.get('section_id', '').strip() or None
            subject_ids = request.POST.getlist('subject_ids')
            default_subject_type = request.POST.get('default_subject_type', SubjectType.COMPULSORY)
            academic_year_id = request.POST.get('academic_year_id', '').strip() or None
            reason = request.POST.get('reason', '').strip()

            try:
                res = ClassSubjectService.bulk_map_subjects_to_class(
                    school_class_id=school_class_id,
                    subject_ids=subject_ids,
                    stream=stream,
                    section_id=section_id,
                    subject_type=default_subject_type,
                    academic_year_id=academic_year_id,
                    actor=request.user,
                    reason=reason or f"Bulk mapped subjects to class from dashboard",
                    request=request,
                )
                messages.success(
                    request,
                    f"Successfully mapped {res['created_count']} subject(s) to sections "
                    f"(skipped {res['skipped_count']} already mapped)."
                )
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Bulk mapping failed: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-class-subjects-list')

        elif action == 'update':
            mapping_id = request.POST.get('mapping_id')
            subject_type = request.POST.get('subject_type')
            teacher_id = request.POST.get('teacher_id') or None
            academic_year_id = request.POST.get('academic_year_id') or None
            is_active = request.POST.get('is_active') == 'on'
            reason = request.POST.get('reason', '').strip()

            try:
                mapping = ClassSubjectService.update_mapping(
                    mapping_id=mapping_id,
                    actor=request.user,
                    subject_type=subject_type,
                    teacher_id=teacher_id,
                    academic_year_id=academic_year_id,
                    is_active=is_active,
                    reason=reason or "Updated ClassSubject mapping from dashboard",
                    request=request,
                )
                messages.success(request, f"Mapping for '{mapping.subject.name}' updated successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to update mapping: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-class-subjects-list')

        elif action == 'delete':
            mapping_id = request.POST.get('mapping_id')
            reason = request.POST.get('reason', '').strip()

            try:
                ClassSubjectService.delete_mapping(
                    mapping_id=mapping_id,
                    actor=request.user,
                    reason=reason or "Unmapped subject from dashboard",
                    request=request,
                )
                messages.success(request, "Subject unmapped successfully.")
            except BusinessConflictError as err:
                messages.warning(request, str(err))
            except (ResourceNotFoundError, ValidationError) as err:
                messages.error(request, str(err))
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-class-subjects-list')

        elif action == 'copy_section':
            source_section_id = request.POST.get('source_section_id')
            target_section_ids = request.POST.getlist('target_section_ids')
            reason = request.POST.get('reason', '').strip()

            try:
                res = ClassSubjectService.copy_mappings_across_sections(
                    source_section_id=source_section_id,
                    target_section_ids=target_section_ids,
                    actor=request.user,
                    reason=reason or "Copied section mappings from dashboard",
                    request=request,
                )
                messages.success(
                    request,
                    f"Successfully copied {res['copied_count']} subject mapping(s) "
                    f"(skipped {res['skipped_count']} already present)."
                )
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to copy mappings: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect('academics-class-subjects-list')

    # Query params for filtering & search
    selected_class_id = request.GET.get('class_id', '').strip()
    selected_section_id = request.GET.get('section_id', '').strip()
    selected_stream = request.GET.get('stream', '').strip()
    selected_type = request.GET.get('subject_type', '').strip()
    search_query = request.GET.get('q', '').strip()

    mappings_qs = ClassSubjectService.list_mappings(
        school_class_id=int(selected_class_id) if selected_class_id.isdigit() else None,
        section_id=int(selected_section_id) if selected_section_id.isdigit() else None,
        stream=selected_stream if selected_stream in dict(Stream.choices) else None,
        subject_type=selected_type if selected_type in dict(SubjectType.choices) else None,
        search=search_query or None,
    )

    all_classes = SchoolClass.objects.filter(is_active=True).order_by('order', 'name')
    all_sections = Section.objects.filter(is_active=True).select_related('school_class').order_by('school_class__order', 'name')
    all_subjects = Subject.objects.filter(is_active=True).order_by('name', 'code')

    all_teachers = TeacherAssignmentService.get_eligible_faculty_queryset()
    academic_years = AcademicYear.objects.all().order_by('-start_date')
    current_year = AcademicYearService.get_current_year()

    total_mappings = ClassSubject.objects.count()
    active_mappings_count = ClassSubject.objects.filter(is_active=True).count()
    compulsory_count = ClassSubject.objects.filter(subject_type=SubjectType.COMPULSORY).count()
    elective_count = ClassSubject.objects.filter(subject_type__in=[SubjectType.ELECTIVE, SubjectType.OPTIONAL]).count()
    classes_covered = ClassSubject.objects.values('section__school_class').distinct().count()

    context = {
        'mappings': mappings_qs,
        'all_classes': all_classes,
        'all_sections': all_sections,
        'all_subjects': all_subjects,
        'all_teachers': all_teachers,
        'academic_years': academic_years,
        'current_year': current_year,
        'streams': Stream.choices,
        'subject_types': SubjectType.choices,
        'selected_class_id': int(selected_class_id) if selected_class_id.isdigit() else None,
        'selected_section_id': int(selected_section_id) if selected_section_id.isdigit() else None,
        'selected_stream': selected_stream,
        'selected_type': selected_type,
        'search_query': search_query,
        'total_mappings': total_mappings,
        'active_mappings_count': active_mappings_count,
        'compulsory_count': compulsory_count,
        'elective_count': elective_count,
        'classes_covered': classes_covered,
        'can_manage': can_manage,
    }
    return render(request, 'dashboard/academics/class_subjects_list.html', context)


# ==============================================================================
# 7. Teacher Assignments Management (Phase 3)
# ==============================================================================
@login_required(login_url='login')
def teacher_assignments_view(request):
    """
    Consolidated Teacher Assignments Dashboard:
      - Tab 1: Class Teachers Directory & Assignment
      - Tab 2: Subject Teachers Directory & Single/Bulk Assignment
      - Tab 3: Faculty Workload Overview & Distribution
    """
    can_view = _check_academics_permission(request, 'academics.assignment.view') or \
               _check_academics_permission(request, 'academics.timetable.view') or \
               _check_academics_permission(request, 'academics.class.view')
    if not can_view:
        messages.error(request, "You do not have permission to view teacher assignments.")
        return redirect('home-dashboard')

    can_manage = _check_academics_permission(request, 'academics.assignment.manage') or \
                 _check_academics_permission(request, 'academics.timetable.manage')

    active_tab = request.GET.get('tab', 'class_teachers').strip()
    if active_tab not in ('class_teachers', 'subject_teachers', 'workload'):
        active_tab = 'class_teachers'

    if request.method == 'POST':
        if not can_manage:
            messages.error(request, "You do not have permission to manage teacher assignments.")
            return redirect('academics-teacher-assignments-list')

        action = request.POST.get('action')

        if action == 'assign_class_teacher':
            section_id = request.POST.get('section_id')
            teacher_id = request.POST.get('teacher_id')
            reason = request.POST.get('reason', '').strip()
            force = request.POST.get('force') == '1'

            try:
                sec = TeacherAssignmentService.assign_class_teacher(
                    section_id=section_id,
                    teacher_id=teacher_id,
                    actor=request.user,
                    reason=reason,
                    request=request,
                    force=force
                )
                messages.success(request, f"Class Teacher '{sec.class_teacher.get_full_name() or sec.class_teacher.username}' assigned to {sec} successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to assign class teacher: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect(f"{request.path}?tab=class_teachers")

        elif action == 'remove_class_teacher':
            section_id = request.POST.get('section_id')
            reason = request.POST.get('reason', '').strip()

            try:
                TeacherAssignmentService.remove_class_teacher(
                    section_id=section_id,
                    actor=request.user,
                    reason=reason,
                    request=request
                )
                messages.success(request, "Class Teacher unassigned successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to unassign class teacher: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect(f"{request.path}?tab=class_teachers")

        elif action == 'assign_subject_teacher':
            class_subject_id = request.POST.get('class_subject_id')
            teacher_id = request.POST.get('teacher_id')
            reason = request.POST.get('reason', '').strip()

            try:
                cs = TeacherAssignmentService.assign_subject_teacher(
                    class_subject_id=class_subject_id,
                    teacher_id=teacher_id,
                    actor=request.user,
                    reason=reason,
                    request=request
                )
                messages.success(request, f"Assigned '{cs.teacher.get_full_name() or cs.teacher.username}' to {cs.subject.name} in {cs.section}.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to assign subject teacher: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect(f"{request.path}?tab=subject_teachers")

        elif action == 'bulk_assign_subject_teacher':
            class_subject_ids = request.POST.getlist('class_subject_ids')
            teacher_id = request.POST.get('teacher_id')
            reason = request.POST.get('reason', '').strip()

            try:
                count = TeacherAssignmentService.bulk_assign_subject_teacher(
                    class_subject_ids=class_subject_ids,
                    teacher_id=teacher_id,
                    actor=request.user,
                    reason=reason,
                    request=request
                )
                messages.success(request, f"Successfully assigned teacher to {count} subject mapping(s).")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Bulk assignment failed: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect(f"{request.path}?tab=subject_teachers")

        elif action == 'remove_subject_teacher':
            class_subject_id = request.POST.get('class_subject_id')
            reason = request.POST.get('reason', '').strip()

            try:
                TeacherAssignmentService.remove_subject_teacher(
                    class_subject_id=class_subject_id,
                    actor=request.user,
                    reason=reason,
                    request=request
                )
                messages.success(request, "Subject Teacher unassigned successfully.")
            except (ValidationError, ResourceNotFoundError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to unassign subject teacher: {msg}")
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")

            return redirect(f"{request.path}?tab=subject_teachers")

    # Filters & Query Params
    selected_class_id = request.GET.get('class_id', '').strip()
    selected_stream = request.GET.get('stream', '').strip()
    selected_teacher_id = request.GET.get('teacher_id', '').strip()
    unassigned_only = request.GET.get('unassigned_only') == '1'
    search_query = request.GET.get('q', '').strip()

    # Data collections
    class_id_filter = int(selected_class_id) if selected_class_id.isdigit() else None
    teacher_id_filter = int(selected_teacher_id) if selected_teacher_id.isdigit() else None
    stream_filter = selected_stream if selected_stream in dict(Stream.choices) else None

    sections_list = TeacherAssignmentService.list_sections_with_class_teachers(
        school_class_id=class_id_filter,
        stream=stream_filter,
        has_teacher=False if unassigned_only else None,
        search=search_query or None
    )

    subject_assignments_list = TeacherAssignmentService.list_subject_assignments(
        school_class_id=class_id_filter,
        teacher_id=teacher_id_filter,
        unassigned_only=unassigned_only,
        search=search_query or None
    )

    faculty_workload_list = TeacherAssignmentService.get_faculty_workload_summary()

    all_classes = SchoolClass.objects.filter(is_active=True).order_by('order', 'name')
    all_teachers = TeacherAssignmentService.get_eligible_faculty_queryset()

    # Metrics
    total_sections = Section.objects.filter(is_active=True).count()
    assigned_class_teachers_count = Section.objects.filter(is_active=True, class_teacher__isnull=False).count()
    unassigned_class_teachers_count = total_sections - assigned_class_teachers_count

    total_subject_mappings = ClassSubject.objects.filter(is_active=True).count()
    assigned_subject_teachers_count = ClassSubject.objects.filter(is_active=True, teacher__isnull=False).count()
    unassigned_subject_teachers_count = total_subject_mappings - assigned_subject_teachers_count

    total_faculty_count = all_teachers.count()

    context = {
        'active_tab': active_tab,
        'sections_list': sections_list,
        'subject_assignments_list': subject_assignments_list,
        'faculty_workload_list': faculty_workload_list,
        'all_classes': all_classes,
        'all_teachers': all_teachers,
        'streams': Stream.choices,
        'selected_class_id': class_id_filter,
        'selected_stream': stream_filter,
        'selected_teacher_id': teacher_id_filter,
        'unassigned_only': unassigned_only,
        'search_query': search_query,
        'total_sections': total_sections,
        'assigned_class_teachers_count': assigned_class_teachers_count,
        'unassigned_class_teachers_count': unassigned_class_teachers_count,
        'total_subject_mappings': total_subject_mappings,
        'assigned_subject_teachers_count': assigned_subject_teachers_count,
        'unassigned_subject_teachers_count': unassigned_subject_teachers_count,
        'total_faculty_count': total_faculty_count,
        'can_manage': can_manage,
    }
    return render(request, 'dashboard/academics/teacher_assignments.html', context)


# ==============================================================================
# 8. Timetable & Scheduling Management (Phase 4)
# ==============================================================================
@login_required(login_url='login')
def timetable_view(request):
    """
    Consolidated Timetable & Scheduling Dashboard:
      - Tab 1: Section Timetable Matrix (Weekly Grid by Section, with conflict-checked slot assignment modals)
      - Tab 2: Teacher Timetable Matrix (Weekly Grid by Faculty member)
      - Tab 3: Bell Schedule / Period Management (Timetable Periods, times, break flags, CBSE defaults generation)
    """
    has_timetable_view = _check_academics_permission(request, 'academics.timetable.view')
    has_room_view = _check_academics_permission(request, 'academics.room.view')
    if not (has_timetable_view or has_room_view):
        messages.error(request, "You do not have permission to view timetables or rooms.")
        return redirect('home-dashboard')

    can_manage_timetable = _check_academics_permission(request, 'academics.timetable.manage')
    can_manage_room = _check_academics_permission(request, 'academics.room.manage') or can_manage_timetable
    can_manage = can_manage_timetable or can_manage_room

    active_tab = request.GET.get('tab', 'section_matrix').strip()
    if active_tab not in ('section_matrix', 'teacher_matrix', 'periods', 'rooms'):
        active_tab = 'section_matrix'

    if request.method == 'POST':
        action = request.POST.get('action')

        # 1. Period Actions
        if action == 'create_period':
            if not can_manage_timetable:
                messages.error(request, "You do not have permission to manage timetables.")
                return redirect('academics-timetable-list')
            period_number = request.POST.get('period_number')
            name = request.POST.get('name', 'Period')
            start_time = request.POST.get('start_time')
            end_time = request.POST.get('end_time')
            is_break = request.POST.get('is_break') == 'on'

            try:
                p = TimetablePeriodService.create_period(
                    period_number=period_number,
                    start_time=start_time,
                    end_time=end_time,
                    name=name,
                    is_break=is_break,
                    actor=request.user,
                    request=request,
                )
                messages.success(request, f"Period '{p.name} {p.period_number}' created successfully.")
            except (ValidationError, BusinessConflictError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to create period: {msg}")
            return redirect(f"{request.path}?tab=periods")

        elif action == 'update_period':
            if not can_manage_timetable:
                messages.error(request, "You do not have permission to manage timetables.")
                return redirect('academics-timetable-list')
            period_id = request.POST.get('period_id')
            period_number = request.POST.get('period_number')
            name = request.POST.get('name')
            start_time = request.POST.get('start_time')
            end_time = request.POST.get('end_time')
            is_break = request.POST.get('is_break') == 'on'

            try:
                p = TimetablePeriodService.update_period(
                    period_id=period_id,
                    period_number=period_number,
                    name=name,
                    start_time=start_time,
                    end_time=end_time,
                    is_break=is_break,
                    actor=request.user,
                    request=request,
                )
                messages.success(request, f"Period '{p.name} {p.period_number}' updated successfully.")
            except (ValidationError, ResourceNotFoundError, BusinessConflictError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to update period: {msg}")
            return redirect(f"{request.path}?tab=periods")

        elif action == 'delete_period':
            if not can_manage_timetable:
                messages.error(request, "You do not have permission to manage timetables.")
                return redirect('academics-timetable-list')
            period_id = request.POST.get('period_id')
            try:
                TimetablePeriodService.delete_period(period_id=period_id, actor=request.user, request=request)
                messages.success(request, "Period deleted successfully.")
            except (ResourceNotFoundError, BusinessConflictError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to delete period: {msg}")
            return redirect(f"{request.path}?tab=periods")

        elif action == 'generate_cbSE_periods' or action == 'generate_cbse_periods':
            if not can_manage_timetable:
                messages.error(request, "You do not have permission to manage timetables.")
                return redirect('academics-timetable-list')
            try:
                periods = TimetablePeriodService.bulk_create_cbse_standard_periods(actor=request.user, request=request)
                messages.success(request, f"Generated {len(periods)} CBSE standard periods (with Assembly, Breaks & 8 Teaching Periods).")
            except BusinessConflictError as err:
                messages.error(request, str(err))
            return redirect(f"{request.path}?tab=periods")

        # 2. Slot Actions
        elif action == 'create_slot':
            if not can_manage_timetable:
                messages.error(request, "You do not have permission to manage timetables.")
                return redirect('academics-timetable-list')
            day_of_week = request.POST.get('day_of_week')
            period_id = request.POST.get('period_id')
            class_subject_id = request.POST.get('class_subject_id')
            room_id = request.POST.get('room_id')
            room = request.POST.get('room', '')
            section_id = request.POST.get('section_id')

            try:
                slot = TimetableService.create_slot(
                    day_of_week=day_of_week,
                    period_id=period_id,
                    class_subject_id=class_subject_id,
                    room=room,
                    room_id=int(room_id) if room_id and room_id.isdigit() else None,
                    actor=request.user,
                    request=request,
                )
                day_name = dict(TimetableSlot.DAYS_OF_WEEK).get(int(day_of_week), '')
                messages.success(request, f"Scheduled {slot.class_subject.subject.name} for {slot.class_subject.section} on {day_name} ({slot.period.name} {slot.period.period_number}).")
            except (ValidationError, ResourceNotFoundError, BusinessConflictError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Collision / Scheduling Error: {msg}")

            redirect_url = f"{request.path}?tab=section_matrix"
            if section_id:
                redirect_url += f"&section_id={section_id}"
            return redirect(redirect_url)

        elif action == 'update_slot':
            if not can_manage_timetable:
                messages.error(request, "You do not have permission to manage timetables.")
                return redirect('academics-timetable-list')
            slot_id = request.POST.get('slot_id')
            class_subject_id = request.POST.get('class_subject_id')
            room_id = request.POST.get('room_id')
            room = request.POST.get('room', '')
            section_id = request.POST.get('section_id')

            update_data = {}
            if class_subject_id:
                update_data['class_subject_id'] = class_subject_id
            if room_id and room_id.isdigit():
                update_data['room_id'] = int(room_id)
            elif room is not None:
                update_data['room'] = room

            try:
                slot = TimetableService.update_slot(
                    slot_id=slot_id,
                    actor=request.user,
                    request=request,
                    **update_data
                )
                messages.success(request, "Timetable slot updated successfully.")
            except (ValidationError, ResourceNotFoundError, BusinessConflictError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Collision / Scheduling Error: {msg}")

            redirect_url = f"{request.path}?tab=section_matrix"
            if section_id:
                redirect_url += f"&section_id={section_id}"
            return redirect(redirect_url)

        elif action == 'delete_slot':
            if not can_manage_timetable:
                messages.error(request, "You do not have permission to manage timetables.")
                return redirect('academics-timetable-list')
            slot_id = request.POST.get('slot_id')
            section_id = request.POST.get('section_id')
            try:
                TimetableService.delete_slot(slot_id=slot_id, actor=request.user, request=request)
                messages.success(request, "Class period unassigned from timetable.")
            except (ResourceNotFoundError, ValidationError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to delete slot: {msg}")

            redirect_url = f"{request.path}?tab=section_matrix"
            if section_id:
                redirect_url += f"&section_id={section_id}"
            return redirect(redirect_url)

        # 3. Room Actions
        elif action == 'create_room':
            if not can_manage_room:
                messages.error(request, "You do not have permission to manage rooms.")
                return redirect(f"{request.path}?tab=rooms")

            room_number = request.POST.get('room_number', '').strip()
            name = request.POST.get('name', '').strip()
            room_type = request.POST.get('room_type', RoomType.CLASSROOM).strip()
            capacity = request.POST.get('capacity', 40)
            floor = request.POST.get('floor', '').strip()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            try:
                room_obj = RoomService.create_room(
                    room_number=room_number,
                    room_type=room_type,
                    capacity=capacity,
                    name=name,
                    floor=floor,
                    description=description,
                    is_active=is_active,
                    actor=request.user,
                    request=request
                )
                messages.success(request, f"Room '{room_obj.room_number}' created successfully.")
            except (ValidationError, BusinessConflictError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to create room: {msg}")
            return redirect(f"{request.path}?tab=rooms")

        elif action == 'update_room':
            if not can_manage_room:
                messages.error(request, "You do not have permission to manage rooms.")
                return redirect(f"{request.path}?tab=rooms")

            room_id = request.POST.get('room_id')
            room_number = request.POST.get('room_number', '').strip()
            name = request.POST.get('name', '').strip()
            room_type = request.POST.get('room_type')
            capacity = request.POST.get('capacity')
            floor = request.POST.get('floor', '').strip()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            try:
                room_obj = RoomService.update_room(
                    room_id=room_id,
                    room_number=room_number,
                    name=name,
                    room_type=room_type,
                    capacity=capacity,
                    floor=floor,
                    description=description,
                    is_active=is_active,
                    actor=request.user,
                    request=request
                )
                messages.success(request, f"Room '{room_obj.room_number}' updated successfully.")
            except (ValidationError, ResourceNotFoundError, BusinessConflictError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to update room: {msg}")
            return redirect(f"{request.path}?tab=rooms")

        elif action == 'toggle_room_status':
            if not can_manage_room:
                messages.error(request, "You do not have permission to manage rooms.")
                return redirect(f"{request.path}?tab=rooms")

            room_id = request.POST.get('room_id')
            target_status = request.POST.get('is_active') == 'true'
            try:
                room_obj = RoomService.toggle_room_status(
                    room_id=room_id,
                    is_active=target_status,
                    actor=request.user,
                    request=request
                )
                messages.success(request, f"Room '{room_obj.room_number}' {'activated' if target_status else 'deactivated'} successfully.")
            except (ResourceNotFoundError, ValidationError) as err:
                msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
                messages.error(request, f"Failed to change status: {msg}")
            return redirect(f"{request.path}?tab=rooms")

        elif action == 'delete_room':
            if not can_manage_room:
                messages.error(request, "You do not have permission to manage rooms.")
                return redirect(f"{request.path}?tab=rooms")

            room_id = request.POST.get('room_id')
            try:
                RoomService.delete_room(room_id=room_id, actor=request.user, request=request)
                messages.success(request, "Room deleted successfully.")
            except (BusinessConflictError, ResourceNotFoundError) as err:
                messages.warning(request, str(err))
            except Exception as err:
                messages.error(request, f"An unexpected error occurred: {err}")
            return redirect(f"{request.path}?tab=rooms")

    # Context Data
    all_periods = TimetablePeriodService.list_periods()
    all_classes = SchoolClass.objects.filter(is_active=True).order_by('order', 'name')
    all_sections = Section.objects.filter(is_active=True).select_related('school_class').order_by('school_class__order', 'name')
    all_teachers = TeacherAssignmentService.get_eligible_faculty_queryset()
    current_year = AcademicYearService.get_current_year()

    # Check if user has admin/management privilege to inspect all faculty timetables
    can_view_all_teachers = (
        request.user.is_superuser
        or can_manage
        or request.user.user_type in ['ADMIN', 'PRINCIPAL']
        or RBACService.has_permission(request.user, 'academics.timetable.manage')
    )

    # If teacher without admin privileges, default tab to teacher_matrix if no tab is provided
    if not can_view_all_teachers and request.user.user_type == 'TEACHER':
        if not request.GET.get('tab'):
            active_tab = 'teacher_matrix'

    # Section Matrix
    selected_section_id = request.GET.get('section_id', '').strip()
    selected_section = None
    if selected_section_id.isdigit():
        selected_section = Section.objects.filter(pk=int(selected_section_id), is_active=True).first()
    elif all_sections.exists():
        selected_section = all_sections.first()

    section_matrix = None
    available_subjects = []
    if selected_section:
        section_matrix = TimetableService.get_section_timetable_matrix(selected_section.pk)
        available_subjects = ClassSubject.objects.filter(
            section=selected_section, is_active=True
        ).select_related('subject', 'teacher').order_by('subject__name')

    # Teacher Matrix
    selected_teacher_id = request.GET.get('teacher_id', '').strip()
    selected_teacher = None
    teacher_matrix = None

    if not can_view_all_teachers and request.user.user_type == 'TEACHER':
        # Force the logged-in teacher to only view their own schedule
        selected_teacher = request.user
    else:
        if selected_teacher_id.isdigit():
            selected_teacher = all_teachers.filter(pk=int(selected_teacher_id)).first()
        elif all_teachers.exists() and active_tab == 'teacher_matrix':
            selected_teacher = all_teachers.first()

    if selected_teacher:
        teacher_matrix = TimetableService.get_teacher_timetable_matrix(selected_teacher.pk)

    total_periods_count = all_periods.count()
    teaching_periods_count = all_periods.filter(is_break=False).count()
    break_periods_count = total_periods_count - teaching_periods_count
    total_slots_count = TimetableSlot.objects.count()

    # Room Master data
    selected_room_type = request.GET.get('room_type', '').strip()
    room_search_query = request.GET.get('q', '').strip()
    all_rooms_qs = RoomService.list_rooms(
        room_type=selected_room_type if selected_room_type else None,
        search=room_search_query if room_search_query else None
    )
    active_rooms = RoomService.list_rooms(is_active=True)
    total_rooms_count = Room.objects.count()
    active_rooms_count = Room.objects.filter(is_active=True).count()

    context = {
        'active_tab': active_tab,
        'all_periods': all_periods,
        'all_classes': all_classes,
        'all_sections': all_sections,
        'all_teachers': all_teachers,
        'current_year': current_year,
        'selected_section': selected_section,
        'section_matrix': section_matrix,
        'available_subjects': available_subjects,
        'selected_teacher': selected_teacher,
        'teacher_matrix': teacher_matrix,
        'all_rooms': all_rooms_qs,
        'active_rooms': active_rooms,
        'room_types': RoomType.choices,
        'selected_room_type': selected_room_type,
        'room_search_query': room_search_query,
        'total_rooms_count': total_rooms_count,
        'active_rooms_count': active_rooms_count,
        'days_of_week': TimetableSlot.DAYS_OF_WEEK,
        'total_periods_count': total_periods_count,
        'teaching_periods_count': teaching_periods_count,
        'break_periods_count': break_periods_count,
        'total_slots_count': total_slots_count,
        'can_manage': can_manage,
        'can_manage_room': can_manage_room,
        'can_view_all_teachers': can_view_all_teachers,
    }
    return render(request, 'dashboard/academics/timetable.html', context)
