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
    DocumentStatus, SeatQuota, StudentCategory
)
from apps.admissions.services import AdmissionService
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.rbac.services import RBACService
from apps.school_config.models import AcademicYear
from common.exceptions import BusinessConflictError

BLOOD_GROUP_CHOICES = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
RELATION_CHOICES = ['Father', 'Mother', 'Guardian', 'Other']


def _check_admissions_permission(request, codename):
    if not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True
    return RBACService.has_permission(request.user, codename)


def _extract_and_create_application(request, selected_year):
    """
    Helper to extract 4-step admission intake parameters, validate files, and create application & documents.
    """
    first_name = request.POST.get('first_name', '').strip()
    last_name = request.POST.get('last_name', '').strip()
    applying_class = request.POST.get('applying_class', '').strip()
    dob_str = request.POST.get('date_of_birth', '').strip()
    gender = request.POST.get('gender', 'M').strip()

    # Step 1 Demographics
    aadhaar_number = request.POST.get('aadhaar_number', '').strip()
    blood_group = request.POST.get('blood_group', '').strip()
    religion = request.POST.get('religion', '').strip()
    mother_tongue = request.POST.get('mother_tongue', '').strip()
    student_email = request.POST.get('student_email', '').strip()
    student_photo = request.FILES.get('student_photo')

    # Step 2 Parents / Guardian
    father_name = request.POST.get('father_name', '').strip()
    mother_name = request.POST.get('mother_name', '').strip()
    guardian_relation = request.POST.get('guardian_relation', 'Father').strip() or 'Father'
    guardian_phone = request.POST.get('guardian_phone', '').strip()
    alternate_phone = request.POST.get('alternate_phone', '').strip()
    guardian_email = request.POST.get('guardian_email', '').strip()
    father_occupation = request.POST.get('father_occupation', '').strip()
    mother_occupation = request.POST.get('mother_occupation', '').strip()

    # Step 3 Address & Academic History
    address = request.POST.get('address', '').strip()
    city = request.POST.get('city', '').strip()
    district = request.POST.get('district', '').strip()
    state = request.POST.get('state', '').strip()
    pincode = request.POST.get('pincode', '').strip()

    same_as_current_address = request.POST.get('same_as_current_address') in ['on', 'true', 'True', '1', True]
    if same_as_current_address:
        permanent_address = address
        permanent_city = city
        permanent_district = district
        permanent_state = state
        permanent_pincode = pincode
    else:
        permanent_address = request.POST.get('permanent_address', '').strip()
        permanent_city = request.POST.get('permanent_city', '').strip()
        permanent_district = request.POST.get('permanent_district', '').strip()
        permanent_state = request.POST.get('permanent_state', '').strip()
        permanent_pincode = request.POST.get('permanent_pincode', '').strip()

    previous_school = request.POST.get('previous_school', '').strip()
    previous_class = request.POST.get('previous_class', '').strip()
    prev_pct_str = request.POST.get('previous_percentage', '').strip()
    previous_percentage = float(prev_pct_str) if prev_pct_str else None

    # Step 4 Category, Facilities & Consent
    category = request.POST.get('category', StudentCategory.GENERAL).strip()
    needs_transport = request.POST.get('needs_transport') in ['on', 'true', 'True', '1', True]
    needs_hostel = request.POST.get('needs_hostel') in ['on', 'true', 'True', '1', True]
    has_sibling_in_school = request.POST.get('has_sibling_in_school') in ['on', 'true', 'True', '1', True]
    sibling_details = request.POST.get('sibling_details', '').strip()
    medical_conditions = request.POST.get('medical_conditions', '').strip()
    declaration_accepted = (request.POST.get('declaration_accepted') in ['on', 'true', 'True', '1', True]) if 'declaration_accepted' in request.POST else True

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
        request=request,
        aadhaar_number=aadhaar_number,
        blood_group=blood_group,
        religion=religion,
        mother_tongue=mother_tongue,
        student_email=student_email,
        student_photo=student_photo,
        guardian_relation=guardian_relation,
        alternate_phone=alternate_phone,
        father_occupation=father_occupation,
        mother_occupation=mother_occupation,
        city=city,
        district=district,
        state=state,
        pincode=pincode,
        same_as_current_address=same_as_current_address,
        permanent_address=permanent_address,
        permanent_city=permanent_city,
        permanent_district=permanent_district,
        permanent_state=permanent_state,
        permanent_pincode=permanent_pincode,
        previous_class=previous_class,
        previous_percentage=previous_percentage,
        category=category,
        declaration_accepted=declaration_accepted,
        needs_transport=needs_transport,
        needs_hostel=needs_hostel,
        has_sibling_in_school=has_sibling_in_school,
        sibling_details=sibling_details,
        medical_conditions=medical_conditions,
    )

    # Document uploads
    doc_fields = [
        ('doc_birth_certificate', 'Birth Certificate'),
        ('doc_transfer_certificate', 'Transfer Certificate'),
        ('doc_marksheet', 'Previous Marksheet'),
        ('doc_aadhaar', 'Aadhaar Card'),
        ('doc_caste_certificate', 'Caste Certificate'),
        ('doc_address_proof', 'Address Proof'),
    ]
    for field_name, label in doc_fields:
        f = request.FILES.get(field_name)
        if f:
            ApplicationDocument.objects.create(
                application=app,
                doc_type=label,
                title=f"{app.first_name} {label}",
                document_file=f
            )

    return app


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

            try:
                app = _extract_and_create_application(request, selected_year)
                messages.success(request, f"Application {app.application_number} submitted successfully.")
                return redirect('admissions-application-detail', pk=app.pk)
            except ValidationError as ve:
                err_msg = "; ".join([f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in ve.message_dict.items()]) if hasattr(ve, 'message_dict') else str(ve)
                messages.error(request, f"Validation error: {err_msg}")
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
        'categories': StudentCategory.choices,
        'blood_groups': BLOOD_GROUP_CHOICES,
        'relation_choices': RELATION_CHOICES,
        'can_add': can_add,
        'can_approve': can_approve,
        'status_filter': status_filter,
        'class_filter': class_filter,
        'search_query': search_query,
    }
    return render(request, 'dashboard/admissions/applications_list.html', context)


@login_required(login_url='login')
def application_apply_view(request):
    """
    Dedicated 4-Step Intake Wizard Page for Admission Applications.
    """
    if not _check_admissions_permission(request, 'admissions.application.add'):
        messages.error(request, "You do not have permission to submit admission applications.")
        return redirect('admissions-applications-list')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    selected_year_id = request.GET.get('year') or request.POST.get('academic_year')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    classes = SchoolClass.objects.all().order_by('order')

    if request.method == 'POST':
        try:
            app = _extract_and_create_application(request, selected_year)
            messages.success(request, f"Application {app.application_number} submitted successfully.")
            return redirect('admissions-application-detail', pk=app.pk)
        except ValidationError as ve:
            err_msg = "; ".join([f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in ve.message_dict.items()]) if hasattr(ve, 'message_dict') else str(ve)
            messages.error(request, f"Validation error: {err_msg}")
        except Exception as e:
            messages.error(request, f"Error creating application: {e}")

    context = {
        'academic_years': academic_years,
        'selected_year': selected_year,
        'current_year': current_year,
        'classes': classes,
        'categories': StudentCategory.choices,
        'blood_groups': BLOOD_GROUP_CHOICES,
        'relation_choices': RELATION_CHOICES,
    }
    return render(request, 'dashboard/admissions/application_form.html', context)


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
