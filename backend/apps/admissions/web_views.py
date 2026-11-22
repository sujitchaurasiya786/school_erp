"""
Admissions Web Views — Dashboard views for Applications, Evaluations, Seat Quotas, and Student Conversion.
"""
from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.academics.models import SchoolClass, Section
from apps.admissions.models import (
    AdmissionApplication, ApplicationDocument, ApplicationStatus,
    DocumentStatus, SeatQuota
)
from apps.admissions.services import AdmissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.rbac.services import RBACService
from apps.school_config.models import AcademicYear
from common.exceptions import BusinessConflictError


def _check_admissions_permission(request, codename):
    if not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True
    return RBACService.has_permission(request.user, codename)


@login_required(login_url='login')
def applications_list_view(request):
    """
    Dashboard view for admission applications directory, search, filter, and intake.
    """
    if not _check_admissions_permission(request, 'admissions.application.view'):
        messages.error(request, "You do not have permission to view admission applications.")
        return redirect('home-dashboard')

    can_add = _check_admissions_permission(request, 'admissions.application.add')
    can_approve = _check_admissions_permission(request, 'admissions.application.approve')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    selected_year_id = request.GET.get('year')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    classes = SchoolClass.objects.all().order_by('order')

    # Handle POST (Application Creation)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_application':
            if not can_add:
                messages.error(request, "You do not have permission to submit admission applications.")
                return redirect('admissions-applications-list')

            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            applying_class = request.POST.get('applying_class', '').strip()
            dob_str = request.POST.get('date_of_birth', '').strip()
            gender = request.POST.get('gender', 'M')
            father_name = request.POST.get('father_name', '').strip()
            mother_name = request.POST.get('mother_name', '').strip()
            guardian_phone = request.POST.get('guardian_phone', '').strip()
            guardian_email = request.POST.get('guardian_email', '').strip()
            address = request.POST.get('address', '').strip()
            previous_school = request.POST.get('previous_school', '').strip()

            try:
                dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
                app = AdmissionService.create_application(
                    academic_year=selected_year,
                    applying_class=applying_class,
                    first_name=first_name,
                    last_name=last_name,
                    date_of_birth=dob,
                    gender=gender,
                    father_name=father_name,
                    mother_name=mother_name,
                    guardian_phone=guardian_phone,
                    address=address,
                    guardian_email=guardian_email,
                    previous_school=previous_school,
                    actor=request.user,
                    request=request
                )
                messages.success(request, f"Application {app.application_number} submitted successfully.")
                return redirect('admissions-application-detail', pk=app.pk)
            except Exception as e:
                messages.error(request, f"Error creating application: {e}")

    # Filtering & Search
    qs = AdmissionApplication.objects.all().select_related('academic_year')
    if selected_year:
        qs = qs.filter(academic_year=selected_year)

    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)

    class_filter = request.GET.get('class_name')
    if class_filter:
        qs = qs.filter(applying_class=class_filter)

    search_query = request.GET.get('q', '').strip()
    if search_query:
        qs = qs.filter(
            Q(application_number__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(guardian_phone__icontains=search_query)
        )

    # Metrics
    base_qs = AdmissionApplication.objects.filter(academic_year=selected_year) if selected_year else AdmissionApplication.objects.none()
    metrics = {
        'total': base_qs.count(),
        'submitted': base_qs.filter(status=ApplicationStatus.SUBMITTED).count(),
        'under_review': base_qs.filter(status__in=[ApplicationStatus.UNDER_REVIEW, ApplicationStatus.DOCUMENTS_VERIFIED, ApplicationStatus.TEST_SCHEDULED]).count(),
        'approved': base_qs.filter(status=ApplicationStatus.APPROVED).count(),
        'admitted': base_qs.filter(status=ApplicationStatus.ADMITTED).count(),
        'waitlist': base_qs.filter(status=ApplicationStatus.WAITLIST).count(),
    }

    context = {
        'academic_years': academic_years,
        'selected_year': selected_year,
        'current_year': current_year,
        'classes': classes,
        'applications': qs.order_by('-created_at')[:100],
        'metrics': metrics,
        'status_choices': ApplicationStatus.choices,
        'can_add': can_add,
        'can_approve': can_approve,
        'status_filter': status_filter,
        'class_filter': class_filter,
        'search_query': search_query,
    }
    return render(request, 'dashboard/admissions/applications_list.html', context)


@login_required(login_url='login')
def application_detail_view(request, pk):
    """
    Detailed applicant view: documents, test/interview evaluation, approval/rejection, and student conversion.
    """
    if not _check_admissions_permission(request, 'admissions.application.view'):
        messages.error(request, "You do not have permission to view admission applications.")
        return redirect('home-dashboard')

    app = get_object_or_404(
        AdmissionApplication.objects.select_related('academic_year').prefetch_related('documents'),
        pk=pk
    )

    can_change = _check_admissions_permission(request, 'admissions.application.change')
    can_approve = _check_admissions_permission(request, 'admissions.application.approve')

    sections = Section.objects.all().select_related('school_class').order_by('name')
    matching_class = SchoolClass.objects.filter(name__iexact=app.applying_class).first()
    if matching_class:
        sections = sections.filter(school_class=matching_class)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'upload_document':
            if not can_change:
                messages.error(request, "You do not have permission to upload documents.")
                return redirect('admissions-application-detail', pk=app.pk)

            doc_type = request.POST.get('doc_type', '').strip()
            doc_file = request.FILES.get('document_file')
            if doc_type and doc_file:
                ApplicationDocument.objects.create(
                    application=app,
                    doc_type=doc_type,
                    document_file=doc_file
                )
                messages.success(request, f"Document '{doc_type}' uploaded successfully.")
            else:
                messages.error(request, "Please provide document type and select a file.")
            return redirect('admissions-application-detail', pk=app.pk)

        elif action == 'verify_document':
            if not can_approve:
                messages.error(request, "You do not have permission to verify documents.")
                return redirect('admissions-application-detail', pk=app.pk)

            doc_id = request.POST.get('document_id')
            ver_status = request.POST.get('verification_status')
            ver_notes = request.POST.get('verification_notes', '')

            try:
                AdmissionService.verify_document(
                    document_id=doc_id,
                    verification_status=ver_status,
                    notes=ver_notes,
                    user=request.user,
                    request=request
                )
                messages.success(request, "Document verification status updated.")
            except Exception as e:
                messages.error(request, f"Error verifying document: {e}")
            return redirect('admissions-application-detail', pk=app.pk)

        elif action == 'record_evaluation':
            if not can_change:
                messages.error(request, "You do not have permission to record evaluations.")
                return redirect('admissions-application-detail', pk=app.pk)

            test_score_str = request.POST.get('test_score', '').strip()
            interview_remarks = request.POST.get('interview_remarks', '').strip()
            new_status = request.POST.get('status')

            test_score = float(test_score_str) if test_score_str else None
            try:
                AdmissionService.record_evaluation(
                    application_id=app.pk,
                    test_score=test_score,
                    interview_remarks=interview_remarks,
                    status=new_status,
                    user=request.user,
                    request=request
                )
                messages.success(request, "Evaluation details saved.")
            except Exception as e:
                messages.error(request, f"Error saving evaluation: {e}")
            return redirect('admissions-application-detail', pk=app.pk)

        elif action == 'approve_application':
            if not can_approve:
                messages.error(request, "You do not have permission to approve applications.")
                return redirect('admissions-application-detail', pk=app.pk)

            remarks = request.POST.get('remarks', '').strip()
            try:
                AdmissionService.approve_application(
                    application=app,
                    user=request.user,
                    remarks=remarks,
                    request=request
                )
                messages.success(request, f"Application {app.application_number} has been approved.")
            except BusinessConflictError as e:
                messages.error(request, str(e))
            except Exception as e:
                messages.error(request, f"Error approving application: {e}")
            return redirect('admissions-application-detail', pk=app.pk)

        elif action == 'reject_application':
            if not can_approve:
                messages.error(request, "You do not have permission to reject applications.")
                return redirect('admissions-application-detail', pk=app.pk)

            reason = request.POST.get('reason', '').strip()
            try:
                AdmissionService.reject_application(
                    application=app,
                    user=request.user,
                    reason=reason,
                    request=request
                )
                messages.success(request, f"Application {app.application_number} has been rejected.")
            except Exception as e:
                messages.error(request, f"Error rejecting application: {e}")
            return redirect('admissions-application-detail', pk=app.pk)

        elif action == 'convert_to_student':
            if not can_approve:
                messages.error(request, "You do not have permission to convert applications to students.")
                return redirect('admissions-application-detail', pk=app.pk)

            section_name = request.POST.get('section_name', 'A')
            roll_str = request.POST.get('roll_number', '').strip()
            roll_number = int(roll_str) if roll_str else None

            try:
                student = AdmissionService.convert_to_student(
                    application_id=app.pk,
                    section_name=section_name,
                    roll_number=roll_number,
                    user=request.user,
                    request=request
                )
                messages.success(request, f"Applicant successfully converted to Student #{student.admission_number}!")
            except Exception as e:
                messages.error(request, f"Error converting to student: {e}")
            return redirect('admissions-application-detail', pk=app.pk)

    context = {
        'app': app,
        'documents': app.documents.all(),
        'sections': sections,
        'can_change': can_change,
        'can_approve': can_approve,
        'status_choices': ApplicationStatus.choices,
        'doc_status_choices': DocumentStatus.choices,
    }
    return render(request, 'dashboard/admissions/application_detail.html', context)


@login_required(login_url='login')
def seat_quotas_view(request):
    """
    Dashboard view for configuring class-wise seat capacity and waitlist limit.
    """
    if not _check_admissions_permission(request, 'admissions.application.view'):
        messages.error(request, "You do not have permission to view seat quotas.")
        return redirect('home-dashboard')

    can_manage = _check_admissions_permission(request, 'admissions.seats.manage')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    selected_year_id = request.GET.get('year')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    classes = SchoolClass.objects.all().order_by('order')

    if request.method == 'POST':
        if not can_manage:
            messages.error(request, "You do not have permission to configure seat quotas.")
            return redirect(f"{request.path}?year={selected_year.pk if selected_year else ''}")

        action = request.POST.get('action')
        if action == 'save_quota':
            class_name = request.POST.get('class_name', '').strip()
            total_seats = int(request.POST.get('total_seats', 40))
            waitlist_limit = int(request.POST.get('waitlist_limit', 10))

            try:
                quota, created = SeatQuota.objects.update_or_create(
                    academic_year=selected_year,
                    class_name=class_name,
                    defaults={
                        'total_seats': total_seats,
                        'waitlist_limit': waitlist_limit
                    }
                )
                AuditService.log(
                    action=AuditAction.CREATE if created else AuditAction.UPDATE,
                    module='admissions',
                    entity_type='SeatQuota',
                    entity_id=str(quota.pk),
                    actor=request.user,
                    reason=f"Configured seat quota for {class_name} ({total_seats} seats, {waitlist_limit} waitlist)",
                    request=request
                )
                messages.success(request, f"Seat quota saved for {class_name}.")
            except Exception as e:
                messages.error(request, f"Error saving seat quota: {e}")
            return redirect(f"{request.path}?year={selected_year.pk}")

        elif action == 'delete_quota':
            quota_id = request.POST.get('quota_id')
            quota = get_object_or_404(SeatQuota, pk=quota_id, academic_year=selected_year)
            c_name = quota.class_name
            quota.delete()
            messages.success(request, f"Deleted seat quota for {c_name}.")
            return redirect(f"{request.path}?year={selected_year.pk}")

    # Load quotas
    quotas = []
    if selected_year:
        raw_quotas = SeatQuota.objects.filter(academic_year=selected_year).order_by('class_name')
        for q in raw_quotas:
            filled = AdmissionApplication.objects.filter(
                academic_year=selected_year,
                applying_class=q.class_name,
                status__in=[ApplicationStatus.APPROVED, ApplicationStatus.ADMITTED]
            ).count()
            waitlisted = AdmissionApplication.objects.filter(
                academic_year=selected_year,
                applying_class=q.class_name,
                status=ApplicationStatus.WAITLIST
            ).count()
            quotas.append({
                'quota': q,
                'filled_seats': filled,
                'available_seats': max(0, q.total_seats - filled),
                'waitlisted': waitlisted,
            })

    context = {
        'academic_years': academic_years,
        'selected_year': selected_year,
        'current_year': current_year,
        'classes': classes,
        'quotas': quotas,
        'can_manage': can_manage,
    }
    return render(request, 'dashboard/admissions/seat_quotas.html', context)
