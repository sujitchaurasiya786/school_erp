"""
Attendance Web Views — Dashboard views for Working Days, Attendance Calendar, and Holidays.
Provides intuitive web interfaces for school administrators and staff.
"""
from datetime import date, datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.attendance.models import (
    Holiday, HolidayType, SaturdayRule, OverrideType,
    WorkingDayConfig, SpecialDayOverride, WEEKDAY_CHOICES
)
from apps.attendance.services import CalendarService
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.rbac.services import RBACService
from apps.school_config.models import AcademicYear
from common.exceptions import BusinessConflictError


def _check_attendance_permission(request, codename):
    """
    Enforces dynamic RBAC permission for attendance dashboard views.
    Django superuser (is_superuser=True) has permanent system administrator access.
    """
    if not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True
    return RBACService.has_permission(request.user, codename)


@login_required(login_url='login')
def attendance_calendar_view(request):
    """
    Dashboard view for Weekly Working Days configuration, Special Day Overrides,
    and operational calendar day resolution.
    """
    if not _check_attendance_permission(request, 'attendance.calendar.view'):
        messages.error(request, "You do not have permission to view the attendance calendar.")
        return redirect('home-dashboard')

    can_manage = _check_attendance_permission(request, 'attendance.calendar.manage')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    selected_year_id = request.GET.get('year')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    # Handle mutations (POST)
    if request.method == 'POST':
        if not can_manage:
            messages.error(request, "You do not have permission to modify calendar settings.")
            return redirect(f"{request.path}?year={selected_year.pk if selected_year else ''}")

        action = request.POST.get('action')

        if action == 'save_working_days':
            if not selected_year:
                messages.error(request, "Please select an academic year.")
                return redirect(request.path)

            config, _ = WorkingDayConfig.objects.get_or_create(academic_year=selected_year)
            config.monday = request.POST.get('monday') == 'on'
            config.tuesday = request.POST.get('tuesday') == 'on'
            config.wednesday = request.POST.get('wednesday') == 'on'
            config.thursday = request.POST.get('thursday') == 'on'
            config.friday = request.POST.get('friday') == 'on'
            config.sunday = request.POST.get('sunday') == 'on'
            config.saturday_rule = request.POST.get('saturday_rule', SaturdayRule.ALL_WORKING)
            config.notes = request.POST.get('notes', '').strip()

            try:
                config.clean()
                config.save()
                AuditService.log(
                    action=AuditAction.UPDATE,
                    module="attendance",
                    entity_type="WorkingDayConfig",
                    entity_id=str(config.pk),
                    actor=request.user,
                    reason=f"Updated working days configuration for {selected_year.name}",
                    changes={
                        "saturday_rule": config.saturday_rule,
                        "sunday": config.sunday,
                    },
                    request=request
                )
                messages.success(request, f"Working days configuration updated for {selected_year.name}.")
            except ValidationError as e:
                messages.error(request, str(e.message_dict if hasattr(e, 'message_dict') else e))

            return redirect(f"{request.path}?year={selected_year.pk}")

        elif action == 'create_override':
            if not selected_year:
                messages.error(request, "Please select an academic year.")
                return redirect(request.path)

            override_date_str = request.POST.get('date', '').strip()
            is_working = request.POST.get('is_working') == 'on'
            override_type = request.POST.get('override_type', OverrideType.SPECIAL_WORKING)
            reason = request.POST.get('reason', '').strip()
            tt_weekday = request.POST.get('timetable_weekday')
            tt_weekday_val = int(tt_weekday) if tt_weekday and is_working else None

            try:
                override = SpecialDayOverride(
                    academic_year=selected_year,
                    date=datetime.strptime(override_date_str, "%Y-%m-%d").date(),
                    is_working=is_working,
                    override_type=override_type,
                    timetable_weekday=tt_weekday_val,
                    reason=reason,
                    created_by=request.user
                )
                override.clean()
                override.save()
                AuditService.log(
                    action=AuditAction.CREATE,
                    module="attendance",
                    entity_type="SpecialDayOverride",
                    entity_id=str(override.pk),
                    actor=request.user,
                    reason=f"Created special day override on {override.date}: {override.reason}",
                    request=request
                )
                messages.success(request, f"Special day override recorded for {override.date}.")
            except Exception as e:
                messages.error(request, f"Error saving override: {e}")

            return redirect(f"{request.path}?year={selected_year.pk}")

        elif action == 'delete_override':
            override_id = request.POST.get('override_id')
            override = get_object_or_404(SpecialDayOverride, pk=override_id, academic_year=selected_year)
            o_date = str(override.date)
            override.delete()
            AuditService.log(
                action=AuditAction.DELETE,
                module="attendance",
                entity_type="SpecialDayOverride",
                entity_id=override_id,
                actor=request.user,
                reason=f"Deleted special day override for {o_date}",
                request=request
            )
            messages.success(request, f"Deleted override for {o_date}.")
            return redirect(f"{request.path}?year={selected_year.pk}")

    # Load data for view
    working_day_config = None
    overrides = []
    if selected_year:
        working_day_config, _ = WorkingDayConfig.objects.get_or_create(
            academic_year=selected_year,
            defaults={'saturday_rule': SaturdayRule.ALL_WORKING}
        )
        overrides = SpecialDayOverride.objects.filter(
            academic_year=selected_year
        ).select_related('created_by').order_by('date')

    context = {
        'academic_years': academic_years,
        'selected_year': selected_year,
        'current_year': current_year,
        'working_day_config': working_day_config,
        'overrides': overrides,
        'can_manage': can_manage,
        'saturday_rules': SaturdayRule.choices,
        'override_types': OverrideType.choices,
        'weekday_choices': WEEKDAY_CHOICES,
    }
    return render(request, 'dashboard/attendance/calendar.html', context)


@login_required(login_url='login')
def holidays_list_view(request):
    """
    Dashboard view for School Holidays and Vacation Breaks.
    """
    if not _check_attendance_permission(request, 'attendance.holiday.view'):
        messages.error(request, "You do not have permission to view holidays.")
        return redirect('home-dashboard')

    can_manage = _check_attendance_permission(request, 'attendance.holiday.manage')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    selected_year_id = request.GET.get('year')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    if request.method == 'POST':
        if not can_manage:
            messages.error(request, "You do not have permission to manage holidays.")
            return redirect(f"{request.path}?year={selected_year.pk if selected_year else ''}")

        action = request.POST.get('action')

        if action == 'create_holiday':
            title = request.POST.get('title', '').strip()
            date_str = request.POST.get('date', '').strip()
            holiday_type = request.POST.get('holiday_type', HolidayType.GAZETTED)
            is_national = request.POST.get('is_national_holiday') == 'on'
            description = request.POST.get('description', '').strip()

            try:
                h_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                holiday = Holiday(
                    academic_year=selected_year,
                    title=title,
                    date=h_date,
                    holiday_type=holiday_type,
                    is_national_holiday=is_national,
                    description=description
                )
                holiday.clean()
                holiday.save()
                AuditService.log(
                    action=AuditAction.CREATE,
                    module="attendance",
                    entity_type="Holiday",
                    entity_id=str(holiday.pk),
                    actor=request.user,
                    reason=f"Created holiday '{holiday.title}' on {holiday.date}",
                    request=request
                )
                messages.success(request, f"Created holiday '{holiday.title}' on {holiday.date}.")
            except Exception as e:
                messages.error(request, f"Error saving holiday: {e}")

            return redirect(f"{request.path}?year={selected_year.pk}")

        elif action == 'create_vacation_break':
            title = request.POST.get('title', '').strip()
            start_date_str = request.POST.get('start_date', '').strip()
            end_date_str = request.POST.get('end_date', '').strip()
            holiday_type = request.POST.get('holiday_type', HolidayType.VACATION)
            is_national = request.POST.get('is_national_holiday') == 'on'
            description = request.POST.get('description', '').strip()

            try:
                s_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                e_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                created = CalendarService.create_vacation_break(
                    academic_year=selected_year,
                    title=title,
                    start_date=s_date,
                    end_date=e_date,
                    holiday_type=holiday_type,
                    description=description,
                    is_national_holiday=is_national,
                    actor=request.user,
                    request=request
                )
                messages.success(request, f"Successfully created vacation break '{title}' ({len(created)} days recorded).")
            except Exception as e:
                messages.error(request, f"Error creating vacation break: {e}")

            return redirect(f"{request.path}?year={selected_year.pk}")

        elif action == 'delete_holiday':
            holiday_id = request.POST.get('holiday_id')
            holiday = get_object_or_404(Holiday, pk=holiday_id, academic_year=selected_year)
            h_title = holiday.title
            h_date = str(holiday.date)
            holiday.delete()
            AuditService.log(
                action=AuditAction.DELETE,
                module="attendance",
                entity_type="Holiday",
                entity_id=holiday_id,
                actor=request.user,
                reason=f"Deleted holiday '{h_title}' on {h_date}",
                request=request
            )
            messages.success(request, f"Deleted holiday '{h_title}'.")
            return redirect(f"{request.path}?year={selected_year.pk}")

    holidays = []
    if selected_year:
        holidays = Holiday.objects.filter(academic_year=selected_year).order_by('date')

    context = {
        'academic_years': academic_years,
        'selected_year': selected_year,
        'current_year': current_year,
        'holidays': holidays,
        'can_manage': can_manage,
        'holiday_types': HolidayType.choices,
    }
    return render(request, 'dashboard/attendance/holidays.html', context)
