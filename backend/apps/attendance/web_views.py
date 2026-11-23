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

from apps.academics.models import SchoolClass, Section
from apps.attendance.models import (
    Holiday, HolidayType, SaturdayRule, OverrideType,
    WorkingDayConfig, SpecialDayOverride, WEEKDAY_CHOICES,
    StudentDailyAttendance, AttendanceCorrectionRequest, StaffDailyAttendance, AttendanceStatus
)
from apps.attendance.services import (
    CalendarService, StudentDailyAttendanceService,
    StaffDailyAttendanceService, AttendanceReportService
)
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.hrms.models import Department
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


@login_required(login_url='login')
def daily_attendance_view(request):
    """
    Daily Student Attendance Management Dashboard.
    Enables authorized teachers and administrators to view section rosters,
    check operational calendar day status, record bulk or individual daily attendance,
    and submit correction requests.
    """
    if not _check_attendance_permission(request, 'attendance.record.view'):
        messages.error(request, "You do not have permission to view student attendance.")
        return redirect('home-dashboard')

    can_mark = _check_attendance_permission(request, 'attendance.record.mark')
    can_correct = _check_attendance_permission(request, 'attendance.record.correct') or can_mark

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    selected_year_id = request.GET.get('year')
    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    classes = SchoolClass.objects.filter(is_active=True).order_by('order')

    selected_class_id = request.GET.get('class_id')
    selected_class = None
    sections = []
    if selected_class_id:
        selected_class = SchoolClass.objects.filter(pk=selected_class_id, is_active=True).first()
        if selected_class:
            sections = Section.objects.filter(school_class=selected_class, is_active=True).order_by('name')

    selected_section_name = request.GET.get('section_name', '').strip()
    selected_date_str = request.GET.get('date', '').strip()

    if not selected_date_str:
        selected_date = date.today()
        selected_date_str = selected_date.isoformat()
    else:
        try:
            selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = date.today()
            selected_date_str = selected_date.isoformat()

    # Scope authorization
    is_scoped = False
    if selected_class and selected_section_name:
        is_scoped = StudentDailyAttendanceService.validate_user_attendance_scope(
            user=request.user,
            class_name=selected_class.name,
            section_name=selected_section_name
        )

    # Handle POST mutations
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'save_attendance':
            if not can_mark:
                messages.error(request, "You do not have permission to mark attendance.")
                return redirect(request.get_full_path())

            if not is_scoped and not request.user.is_superuser:
                messages.error(request, f"You are not authorized to mark attendance for Class {selected_class.name} Section {selected_section_name}.")
                return redirect(request.get_full_path())

            # Calendar validation
            cal_status = CalendarService.get_day_status(selected_year, selected_date)
            if not cal_status.get('is_working', False):
                messages.error(request, f"Cannot record attendance: {selected_date} is a non-working day ({cal_status.get('reason')}).")
                return redirect(request.get_full_path())

            student_ids = request.POST.getlist('student_ids')
            if not student_ids:
                messages.warning(request, "No students selected for attendance marking.")
                return redirect(request.get_full_path())

            records_to_save = []
            for sid in student_ids:
                status_val = request.POST.get(f"status_{sid}", AttendanceStatus.PRESENT)
                remarks_val = request.POST.get(f"remarks_{sid}", "").strip()
                records_to_save.append({
                    'student_id': sid,
                    'academic_year_id': selected_year.pk,
                    'date': selected_date,
                    'class_name': selected_class.name,
                    'section_name': selected_section_name,
                    'status': status_val,
                    'remarks': remarks_val
                })

            try:
                saved_ids = StudentDailyAttendanceService.mark_bulk_attendance(
                    records=records_to_save,
                    actor=request.user,
                    request=request
                )
                messages.success(request, f"Attendance saved successfully for {len(saved_ids)} students.")
            except (ValidationError, BusinessConflictError) as err:
                messages.error(request, f"Failed to save attendance: {err}")
            except Exception as ex:
                messages.error(request, f"Unexpected error while saving attendance: {ex}")

            return redirect(request.get_full_path())

        elif action == 'submit_correction':
            if not can_correct:
                messages.error(request, "You do not have permission to submit attendance corrections.")
                return redirect(request.get_full_path())

            attendance_id = request.POST.get('attendance_id')
            new_status = request.POST.get('new_status')
            reason = request.POST.get('reason', '').strip()

            try:
                StudentDailyAttendanceService.submit_correction_request(
                    attendance_id=attendance_id,
                    new_status=new_status,
                    reason=reason,
                    user=request.user,
                    request=request
                )
                messages.success(request, "Attendance correction request submitted for administrative review.")
            except (ValidationError, BusinessConflictError) as err:
                messages.error(request, f"Could not submit correction request: {err}")
            except Exception as ex:
                messages.error(request, f"Error: {ex}")

            return redirect(request.get_full_path())

    # Build Roster Data if section & date are selected
    roster_data = None
    calendar_status = None
    if selected_year and selected_class and selected_section_name:
        calendar_status = CalendarService.get_day_status(selected_year, selected_date)
        if is_scoped or request.user.is_superuser:
            roster_data = StudentDailyAttendanceService.get_section_roster_with_attendance(
                academic_year=selected_year,
                class_name=selected_class.name,
                section_name=selected_section_name,
                target_date=selected_date
            )

    context = {
        'academic_years': academic_years,
        'selected_year': selected_year,
        'classes': classes,
        'selected_class': selected_class,
        'sections': sections,
        'selected_section_name': selected_section_name,
        'selected_date_str': selected_date_str,
        'selected_date': selected_date,
        'is_scoped': is_scoped,
        'can_mark': can_mark,
        'can_correct': can_correct,
        'calendar_status': calendar_status,
        'roster_data': roster_data,
        'attendance_statuses': AttendanceStatus.choices,
    }
    return render(request, 'dashboard/attendance/daily_attendance.html', context)


@login_required(login_url='login')
def attendance_corrections_view(request):
    """
    Attendance Correction Requests Queue.
    Enables authorized administrators to review, approve, or reject attendance correction requests.
    """
    if not _check_attendance_permission(request, 'attendance.record.view'):
        messages.error(request, "You do not have permission to view attendance corrections.")
        return redirect('home-dashboard')

    can_approve = _check_attendance_permission(request, 'attendance.correction.approve')

    status_filter = request.GET.get('status', 'pending')

    # Handle Review POST
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'review_correction':
            if not can_approve:
                messages.error(request, "You do not have permission to approve or reject attendance corrections.")
                return redirect(request.get_full_path())

            request_id = request.POST.get('request_id')
            decision = request.POST.get('decision')  # 'approve' or 'reject'
            review_notes = request.POST.get('review_notes', '').strip()

            is_approved = (decision == 'approve')
            try:
                corr = StudentDailyAttendanceService.review_correction_request(
                    request_id=request_id,
                    is_approved=is_approved,
                    review_notes=review_notes,
                    user=request.user,
                    request=request
                )
                action_word = "approved and student attendance updated" if is_approved else "rejected"
                messages.success(request, f"Correction request for {corr.attendance.student.admission_number} {action_word}.")
            except (ValidationError, BusinessConflictError) as err:
                messages.error(request, f"Review failed: {err}")
            except Exception as ex:
                messages.error(request, f"Error: {ex}")

            return redirect(request.get_full_path())

    qs = AttendanceCorrectionRequest.objects.select_related(
        'attendance', 'attendance__student', 'attendance__academic_year',
        'requested_by', 'reviewed_by'
    ).order_by('-created_at')

    pending_count = qs.filter(is_approved__isnull=True).count()
    approved_count = qs.filter(is_approved=True).count()
    rejected_count = qs.filter(is_approved=False).count()

    if status_filter == 'pending':
        corrections = qs.filter(is_approved__isnull=True)
    elif status_filter == 'approved':
        corrections = qs.filter(is_approved=True)
    elif status_filter == 'rejected':
        corrections = qs.filter(is_approved=False)
    else:
        corrections = qs

    context = {
        'corrections': corrections,
        'status_filter': status_filter,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
        'total_count': qs.count(),
        'can_approve': can_approve,
    }
    return render(request, 'dashboard/attendance/corrections_list.html', context)


@login_required(login_url='login')
def staff_attendance_view(request):
    """
    Staff Attendance Management Dashboard.
    Enables authorized administrators to record and inspect daily staff attendance.
    """
    if not (request.user.is_superuser or _check_attendance_permission(request, 'attendance.staff.view') or request.user.user_type in ['ADMIN', 'PRINCIPAL']):
        messages.error(request, "You do not have permission to view staff attendance.")
        return redirect('home-dashboard')

    can_mark = request.user.is_superuser or _check_attendance_permission(request, 'attendance.staff.mark') or request.user.user_type in ['ADMIN', 'PRINCIPAL']

    selected_date_str = request.GET.get('date', '').strip()
    if not selected_date_str:
        selected_date = date.today()
        selected_date_str = selected_date.isoformat()
    else:
        try:
            selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = date.today()
            selected_date_str = selected_date.isoformat()

    department_id = request.GET.get('department')
    departments = Department.objects.all().order_by('name')

    # Handle POST submission
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_staff_attendance':
            if not can_mark:
                messages.error(request, "You do not have permission to mark staff attendance.")
                return redirect(request.get_full_path())

            current_ay = AcademicYear.objects.filter(is_current=True).first()
            if current_ay:
                cal_status = CalendarService.get_day_status(current_ay, selected_date)
                if not cal_status.get('is_working', False):
                    messages.error(request, f"Cannot record staff attendance: {selected_date} is a non-working day ({cal_status.get('reason')}).")
                    return redirect(request.get_full_path())

            user_ids = request.POST.getlist('user_ids')
            if not user_ids:
                messages.warning(request, "No staff members selected for attendance marking.")
                return redirect(request.get_full_path())

            records_to_save = []
            for uid in user_ids:
                status_val = request.POST.get(f"status_{uid}", AttendanceStatus.PRESENT)
                remarks_val = request.POST.get(f"remarks_{uid}", "").strip()
                records_to_save.append({
                    'user_id': uid,
                    'status': status_val,
                    'remarks': remarks_val
                })

            try:
                saved_ids = StaffDailyAttendanceService.mark_bulk_staff_attendance(
                    records=records_to_save,
                    attendance_date=selected_date,
                    actor=request.user,
                    request=request
                )
                messages.success(request, f"Staff attendance saved successfully for {len(saved_ids)} staff members.")
            except (ValidationError, BusinessConflictError) as err:
                messages.error(request, f"Failed to save staff attendance: {err}")
            except Exception as ex:
                messages.error(request, f"Error: {ex}")

            return redirect(request.get_full_path())

        elif action in ('approve_self_attendance', 'reject_self_attendance'):
            if not can_mark:
                messages.error(request, "You do not have permission to review self-attendance.")
                return redirect(request.get_full_path())
            att_id = request.POST.get('attendance_id')
            notes = request.POST.get('review_notes', '').strip()
            review_action = 'APPROVE' if action == 'approve_self_attendance' else 'REJECT'
            try:
                StaffDailyAttendanceService.review_self_attendance(
                    attendance_id=att_id,
                    action=review_action,
                    review_notes=notes,
                    reviewer=request.user,
                    request=request
                )
                messages.success(request, f"Self-attendance request {'approved' if review_action == 'APPROVE' else 'rejected'} successfully.")
            except Exception as e:
                messages.error(request, f"Error reviewing attendance: {e}")
            return redirect(request.get_full_path())

    roster_data = StaffDailyAttendanceService.get_staff_roster_with_attendance(
        target_date=selected_date,
        department_id=department_id
    )
    pending_approvals = StaffDailyAttendanceService.get_pending_self_attendances()
    active_tab = request.GET.get('tab', 'roster')

    context = {
        'selected_date_str': selected_date_str,
        'selected_date': selected_date,
        'department_id': department_id,
        'departments': departments,
        'roster_data': roster_data,
        'can_mark': can_mark,
        'attendance_statuses': AttendanceStatus.choices,
        'pending_approvals': pending_approvals,
        'active_tab': active_tab,
    }
    return render(request, 'dashboard/attendance/staff_attendance.html', context)


@login_required(login_url='login')
def self_attendance_view(request):
    """
    Self Attendance check-in / check-out interface for teachers and staff.
    Uses browser camera snapshot, GPS geolocation verification against campus radius,
    and automatic late/early detection.
    """
    if request.user.user_type in ('STUDENT', 'PARENT'):
        messages.error(request, "Self attendance is only accessible to school staff and faculty.")
        return redirect('home-dashboard')

    if request.method == 'POST':
        action_type = request.POST.get('action_type', '').strip().upper()
        latitude = request.POST.get('latitude', '').strip()
        longitude = request.POST.get('longitude', '').strip()
        selfie_base64 = request.POST.get('selfie_image', '').strip()
        exception_reason = request.POST.get('exception_reason', '').strip()

        lat_val = None
        lon_val = None
        if latitude:
            try:
                lat_val = float(latitude)
            except ValueError:
                pass
        if longitude:
            try:
                lon_val = float(longitude)
            except ValueError:
                pass

        try:
            record = StaffDailyAttendanceService.record_self_attendance(
                user=request.user,
                action_type=action_type,
                latitude=lat_val,
                longitude=lon_val,
                selfie_base64=selfie_base64,
                exception_reason=exception_reason,
                actor=request.user,
                request=request
            )
            if record.approval_status == StaffApprovalStatus.PENDING_APPROVAL:
                messages.warning(
                    request,
                    f"{action_type.replace('_', ' ').title()} recorded. It is pending review from Admin/Principal due to location radius or schedule deviation."
                )
            else:
                messages.success(
                    request,
                    f"{action_type.replace('_', ' ').title()} recorded successfully. Verified on-campus."
                )
        except ValidationError as err:
            err_dict = err.message_dict if hasattr(err, 'message_dict') else {'error': err.messages}
            msg = " ".join([" ".join(v) for v in err_dict.values()])
            messages.error(request, msg)
        except Exception as ex:
            messages.error(request, f"Could not record attendance: {ex}")

        return redirect('attendance-self-mark')

    status_data = StaffDailyAttendanceService.get_self_attendance_status(request.user)
    context = {
        'status_data': status_data,
        'school_profile': status_data['school_profile'],
        'record': status_data['record'],
    }
    return render(request, 'dashboard/attendance/self_attendance.html', context)


@login_required(login_url='login')
def attendance_reports_view(request):
    """
    Attendance Analytics & Summary Reports.
    Enables staff to view student section attendance, calculate attendance percentage %,
    inspect shortage threshold alerts (<75%), and export to CSV.
    """
    if not (request.user.is_superuser or _check_attendance_permission(request, 'attendance.record.view')):
        messages.error(request, "You do not have permission to view attendance reports.")
        return redirect('home-dashboard')

    can_export = request.user.is_superuser or _check_attendance_permission(request, 'attendance.report.export')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    selected_year_id = request.GET.get('year')
    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    classes = SchoolClass.objects.filter(is_active=True).order_by('order')
    selected_class_id = request.GET.get('class_id')
    selected_class = None
    sections = []
    if selected_class_id:
        selected_class = SchoolClass.objects.filter(pk=selected_class_id, is_active=True).first()
        if selected_class:
            sections = Section.objects.filter(school_class=selected_class, is_active=True).order_by('name')

    selected_section_name = request.GET.get('section_name', '').strip()

    # Dates
    today = date.today()
    default_start = today.replace(day=1) if today.day > 1 else today - timedelta(days=30)
    start_date_str = request.GET.get('start_date', default_start.isoformat())
    end_date_str = request.GET.get('end_date', today.isoformat())

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        start_date = default_start
        start_date_str = start_date.isoformat()

    try:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        end_date = today
        end_date_str = end_date.isoformat()

    report_type = request.GET.get('report_type', 'student_summary')
    try:
        threshold = float(request.GET.get('threshold', 75.0))
    except ValueError:
        threshold = 75.0

    report_data = None
    export_format = request.GET.get('export')

    if report_type in ('student_summary', 'shortage_alert') and selected_year and selected_class and selected_section_name:
        summary = AttendanceReportService.get_student_attendance_summary(
            academic_year=selected_year,
            class_name=selected_class.name,
            section_name=selected_section_name,
            start_date=start_date,
            end_date=end_date,
            threshold=threshold
        )
        if report_type == 'shortage_alert':
            summary['rows'] = [r for r in summary['rows'] if r['is_shortage']]
        report_data = summary

        if export_format == 'csv' and can_export:
            headers = ['Roll No', 'Admission No', 'Student Name', 'Present Days', 'Half Days', 'Absent Days', 'Leave Days', 'Effective Days', 'Total Working Days', 'Percentage (%)', 'Shortage Alert']
            csv_rows = []
            for r in report_data['rows']:
                csv_rows.append([
                    r['roll_number'] or '-',
                    r['admission_number'],
                    r['name'],
                    r['present_days'],
                    r['half_days'],
                    r['absent_days'],
                    r['leave_days'],
                    r['effective_attendance'],
                    r['total_working_days'],
                    f"{r['percentage']}%",
                    'YES (<75%)' if r['is_shortage'] else 'NO'
                ])
            fname = f"Attendance_Report_{selected_class.name}_{selected_section_name}_{start_date_str}_to_{end_date_str}.csv"
            return AttendanceReportService.export_csv(fname, headers, csv_rows)

    elif report_type == 'staff_summary':
        department_id = request.GET.get('department')
        report_data = AttendanceReportService.get_staff_attendance_summary(
            start_date=start_date,
            end_date=end_date,
            department_id=department_id
        )
        if export_format == 'csv' and can_export:
            headers = ['Staff Name', 'Department', 'Designation', 'Present Days', 'Half Days', 'Absent Days', 'Leave Days', 'Total Working Days', 'Percentage (%)']
            csv_rows = []
            for r in report_data['rows']:
                csv_rows.append([
                    r['name'],
                    r['department'],
                    r['designation'],
                    r['present_days'],
                    r['half_days'],
                    r['absent_days'],
                    r['leave_days'],
                    r['total_working_days'],
                    f"{r['percentage']}%"
                ])
            fname = f"Staff_Attendance_Report_{start_date_str}_to_{end_date_str}.csv"
            return AttendanceReportService.export_csv(fname, headers, csv_rows)

    departments = Department.objects.all().order_by('name')

    context = {
        'academic_years': academic_years,
        'selected_year': selected_year,
        'classes': classes,
        'selected_class': selected_class,
        'sections': sections,
        'selected_section_name': selected_section_name,
        'start_date_str': start_date_str,
        'end_date_str': end_date_str,
        'report_type': report_type,
        'threshold': threshold,
        'departments': departments,
        'department_id': request.GET.get('department'),
        'report_data': report_data,
        'can_export': can_export,
    }
    return render(request, 'dashboard/attendance/reports.html', context)
