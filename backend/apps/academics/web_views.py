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

from apps.academics.models import SchoolClass, Section, Stream
from apps.academics.services import AcademicYearService, SchoolClassService, SectionService
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
