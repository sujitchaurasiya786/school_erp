"""
Students Web Views — Dashboard views for Student Profiles, Enrollments,
Class Promotions, Sibling Linkage, Transfer Certificates, and Bonafide Certificates.
"""
from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.academics.models import SchoolClass, Section
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.rbac.services import RBACService
from apps.school_config.models import AcademicYear, SchoolProfile
from apps.students.models import (
    BonafideCertificate, ClassEnrollment, Guardian, Student,
    StudentDocument, StudentStatus, TransferCertificate
)
from apps.students.services import StudentService
from common.exceptions import BusinessConflictError, SchoolERPException


def _check_students_permission(request, codename):
    if not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True
    return RBACService.has_permission(request.user, codename)


@login_required(login_url='login')
def students_list_view(request):
    """
    Dashboard view for student roster, directory, search, and filtering.
    """
    if not _check_students_permission(request, 'students.profile.view'):
        messages.error(request, "You do not have permission to view student profiles.")
        return redirect('home-dashboard')

    can_add = _check_students_permission(request, 'students.profile.add')
    can_change = _check_students_permission(request, 'students.profile.change')
    can_export = _check_students_permission(request, 'students.report.export')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    selected_year_id = request.GET.get('year')
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if selected_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year_id)
    else:
        selected_year = current_year or academic_years.first()

    classes = SchoolClass.objects.all().order_by('order')
    sections = Section.objects.all().order_by('name')

    # QuerySet filtering
    qs = Student.objects.select_related('primary_guardian').prefetch_related('enrollments__academic_year')

    # Restrict normal student / parent accounts
    user = request.user
    if not user.is_superuser and user.user_type not in ('ADMIN', 'PRINCIPAL', 'TEACHER', 'STAFF', 'ACCOUNTANT'):
        if user.user_type == 'STUDENT' and hasattr(user, 'student_profile'):
            qs = qs.filter(id=user.student_profile.id)
        elif user.user_type == 'PARENT' and hasattr(user, 'guardian_profile'):
            qs = qs.filter(primary_guardian=user.guardian_profile)
        else:
            qs = qs.none()

    # Filters
    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)

    class_filter = request.GET.get('class_name')
    if class_filter:
        qs = qs.filter(enrollments__class_name=class_filter)
        if selected_year:
            qs = qs.filter(enrollments__academic_year=selected_year)

    section_filter = request.GET.get('section_name')
    if section_filter:
        qs = qs.filter(enrollments__section_name=section_filter)
        if selected_year:
            qs = qs.filter(enrollments__academic_year=selected_year)

    gender_filter = request.GET.get('gender')
    if gender_filter:
        qs = qs.filter(gender=gender_filter)

    search_query = request.GET.get('q', '').strip()
    if search_query:
        qs = qs.filter(
            Q(admission_number__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(primary_guardian__phone__icontains=search_query) |
            Q(primary_guardian__first_name__icontains=search_query)
        )

    # Metrics
    total_students = Student.objects.count()
    active_students = Student.objects.filter(status=StudentStatus.ACTIVE).count()
    tc_issued_count = Student.objects.filter(status=StudentStatus.TC_ISSUED).count()
    male_count = Student.objects.filter(status=StudentStatus.ACTIVE, gender='M').count()
    female_count = Student.objects.filter(status=StudentStatus.ACTIVE, gender='F').count()

    context = {
        'students': qs.distinct().order_by('admission_number')[:150],
        'academic_years': academic_years,
        'selected_year': selected_year,
        'classes': classes,
        'sections': sections,
        'status_choices': StudentStatus.choices,
        'can_add': can_add,
        'can_change': can_change,
        'can_export': can_export,
        'metrics': {
            'total': total_students,
            'active': active_students,
            'tc_issued': tc_issued_count,
            'male': male_count,
            'female': female_count,
        },
        'search_query': search_query,
        'status_filter': status_filter,
        'class_filter': class_filter,
        'section_filter': section_filter,
        'gender_filter': gender_filter,
    }
    return render(request, 'dashboard/students/students_list.html', context)


@login_required(login_url='login')
def student_detail_view(request, pk):
    """
    Detailed Student Profile view:
      - Personal details & contact info
      - Primary & secondary guardians
      - Linked siblings & sibling search/link
      - Academic enrollments & class history
      - Document register with upload/download
      - Transfer Certificate & Bonafide issuance
    """
    if not _check_students_permission(request, 'students.profile.view'):
        messages.error(request, "You do not have permission to view student profiles.")
        return redirect('home-dashboard')

    can_change = _check_students_permission(request, 'students.profile.change')
    can_issue_tc = _check_students_permission(request, 'students.tc.issue')

    student = get_object_or_404(
        Student.objects.select_related('primary_guardian', 'secondary_guardian')
        .prefetch_related('siblings', 'enrollments__academic_year', 'documents', 'bonafide_certificates__academic_year'),
        pk=pk
    )

    # Privacy check for student/parent accounts
    user = request.user
    if not user.is_superuser and user.user_type not in ('ADMIN', 'PRINCIPAL', 'TEACHER', 'STAFF', 'ACCOUNTANT'):
        if user.user_type == 'STUDENT' and getattr(user, 'student_profile', None) != student:
            messages.error(request, "Access denied to other student profiles.")
            return redirect('home-dashboard')
        if user.user_type == 'PARENT' and student.primary_guardian != getattr(user, 'guardian_profile', None):
            messages.error(request, "Access denied to other student profiles.")
            return redirect('home-dashboard')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    classes = SchoolClass.objects.all().order_by('order')
    sections = Section.objects.all().order_by('name')

    # Handle POST Actions
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'issue_tc':
            if not can_issue_tc:
                messages.error(request, "You do not have permission to issue Transfer Certificates.")
                return redirect('students-student-detail', pk=student.pk)

            reason = request.POST.get('reason', '').strip()
            dues_cleared = request.POST.get('dues_cleared') == 'on'
            conduct = request.POST.get('conduct_remarks', 'Good').strip()

            try:
                tc = StudentService.issue_transfer_certificate(
                    student_id=student.pk,
                    reason=reason,
                    dues_cleared=dues_cleared,
                    conduct_remarks=conduct,
                    user=request.user,
                    request=request
                )
                messages.success(request, f"Transfer Certificate #{tc.tc_number} issued successfully.")
            except Exception as e:
                messages.error(request, f"Error issuing TC: {e}")
            return redirect('students-student-detail', pk=student.pk)

        elif action == 'issue_bonafide':
            if not can_change:
                messages.error(request, "You do not have permission to issue certificates.")
                return redirect('students-student-detail', pk=student.pk)

            ay_id = request.POST.get('academic_year_id')
            purpose = request.POST.get('purpose', '').strip()
            conduct = request.POST.get('conduct_remarks', 'Good').strip()

            try:
                ay = get_object_or_404(AcademicYear, pk=ay_id)
                bonafide = StudentService.issue_bonafide_certificate(
                    student_id=student.pk,
                    academic_year=ay,
                    purpose=purpose,
                    conduct_remarks=conduct,
                    user=request.user,
                    request=request
                )
                messages.success(request, f"Bonafide Certificate #{bonafide.certificate_number} issued successfully.")
            except Exception as e:
                messages.error(request, f"Error issuing Bonafide certificate: {e}")
            return redirect('students-student-detail', pk=student.pk)

        elif action == 'link_sibling':
            if not can_change:
                messages.error(request, "You do not have permission to modify student records.")
                return redirect('students-student-detail', pk=student.pk)

            sibling_adm = request.POST.get('sibling_admission_number', '').strip()
            try:
                sibling = Student.objects.get(admission_number=sibling_adm)
                StudentService.link_siblings(student.pk, sibling.pk, user=request.user, request=request)
                messages.success(request, f"Linked sibling {sibling.admission_number} ({sibling.full_name}).")
            except Student.DoesNotExist:
                messages.error(request, f"No student found with admission number '{sibling_adm}'.")
            except Exception as e:
                messages.error(request, f"Error linking sibling: {e}")
            return redirect('students-student-detail', pk=student.pk)

        elif action == 'unlink_sibling':
            if not can_change:
                messages.error(request, "You do not have permission to modify student records.")
                return redirect('students-student-detail', pk=student.pk)

            sibling_id = request.POST.get('sibling_id')
            try:
                StudentService.unlink_siblings(student.pk, sibling_id, user=request.user, request=request)
                messages.success(request, "Sibling unlinked successfully.")
            except Exception as e:
                messages.error(request, f"Error unlinking sibling: {e}")
            return redirect('students-student-detail', pk=student.pk)

        elif action == 'upload_document':
            if not can_change:
                messages.error(request, "You do not have permission to upload student documents.")
                return redirect('students-student-detail', pk=student.pk)

            doc_type = request.POST.get('doc_type', '').strip()
            title = request.POST.get('title', '').strip()
            doc_file = request.FILES.get('document_file')

            if doc_type and doc_file:
                try:
                    StudentService.add_student_document(
                        student_id=student.pk,
                        doc_type=doc_type,
                        document_file=doc_file,
                        title=title,
                        user=request.user,
                        request=request
                    )
                    messages.success(request, f"Document '{doc_type}' uploaded successfully.")
                except Exception as e:
                    messages.error(request, f"Error uploading document: {e}")
            else:
                messages.error(request, "Please specify document type and select a file.")
            return redirect('students-student-detail', pk=student.pk)

        elif action == 'update_status':
            if not can_change:
                messages.error(request, "You do not have permission to modify student status.")
                return redirect('students-student-detail', pk=student.pk)

            new_status = request.POST.get('status')
            reason = request.POST.get('reason', '').strip()
            try:
                StudentService.update_student_status(student.pk, new_status, reason, user=request.user, request=request)
                messages.success(request, f"Status updated to {student.get_status_display()}.")
            except Exception as e:
                messages.error(request, f"Error updating status: {e}")
            return redirect('students-student-detail', pk=student.pk)

    context = {
        'student': student,
        'guardians': [student.primary_guardian, student.secondary_guardian] if student.secondary_guardian else [student.primary_guardian],
        'enrollments': student.enrollments.all().order_by('-academic_year__start_date'),
        'siblings': student.siblings.all(),
        'documents': student.documents.all(),
        'bonafide_certificates': student.bonafide_certificates.all(),
        'academic_years': academic_years,
        'classes': classes,
        'sections': sections,
        'status_choices': StudentStatus.choices,
        'can_change': can_change,
        'can_issue_tc': can_issue_tc,
    }
    return render(request, 'dashboard/students/student_detail.html', context)


@login_required(login_url='login')
def student_create_view(request):
    """
    Direct student admission/registration screen.
    """
    if not _check_students_permission(request, 'students.profile.add'):
        messages.error(request, "You do not have permission to create student profiles.")
        return redirect('students-student-list')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    current_year = AcademicYear.objects.filter(is_current=True).first()
    classes = SchoolClass.objects.all().order_by('order')
    sections = Section.objects.all().order_by('name')

    if request.method == 'POST':
        # Candidate Info
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        dob_str = request.POST.get('date_of_birth', '').strip()
        gender = request.POST.get('gender', 'M')
        blood_group = request.POST.get('blood_group', '').strip()
        address = request.POST.get('address', '').strip()
        city = request.POST.get('city', '').strip()
        state = request.POST.get('state', '').strip()
        pincode = request.POST.get('pincode', '').strip()
        prev_school = request.POST.get('previous_school', '').strip()

        # Guardian Info
        guardian_mode = request.POST.get('guardian_mode', 'new')
        if guardian_mode == 'existing':
            guardian_id = request.POST.get('existing_guardian_id')
            primary_guardian = get_object_or_404(Guardian, pk=guardian_id)
        else:
            g_fname = request.POST.get('guardian_first_name', '').strip()
            g_lname = request.POST.get('guardian_last_name', '').strip()
            g_rel = request.POST.get('guardian_relationship', 'Father').strip()
            g_phone = request.POST.get('guardian_phone', '').strip()
            g_email = request.POST.get('guardian_email', '').strip()
            g_occ = request.POST.get('guardian_occupation', '').strip()

            # Check if guardian with phone exists already
            existing_g = Guardian.objects.filter(phone=g_phone).first()
            if existing_g:
                primary_guardian = existing_g
            else:
                primary_guardian = Guardian.objects.create(
                    first_name=g_fname or f"{first_name}'s Guardian",
                    last_name=g_lname or last_name,
                    relationship=g_rel,
                    phone=g_phone,
                    email=g_email,
                    occupation=g_occ
                )

        # Enrollment Info
        ay_id = request.POST.get('academic_year_id')
        class_name = request.POST.get('class_name', '').strip()
        section_name = request.POST.get('section_name', 'A').strip()
        roll_str = request.POST.get('roll_number', '').strip()
        roll_number = int(roll_str) if roll_str else None

        ay = get_object_or_404(AcademicYear, pk=ay_id) if ay_id else current_year

        # Admission number auto-generation
        from apps.admissions.services import AdmissionService
        adm_number = AdmissionService.generate_admission_number(ay)

        try:
            dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
            student = StudentService.create_student_profile(
                admission_number=adm_number,
                first_name=first_name,
                last_name=last_name,
                date_of_birth=dob,
                gender=gender,
                primary_guardian=primary_guardian,
                address=address,
                blood_group=blood_group,
                city=city,
                state=state,
                pincode=pincode,
                previous_school=prev_school,
                academic_year=ay,
                class_name=class_name,
                section_name=section_name,
                roll_number=roll_number,
                user=request.user,
                request=request
            )
            messages.success(request, f"Student #{student.admission_number} created successfully!")
            return redirect('students-student-detail', pk=student.pk)
        except Exception as e:
            messages.error(request, f"Error creating student: {e}")

    context = {
        'academic_years': academic_years,
        'current_year': current_year,
        'classes': classes,
        'sections': sections,
        'guardians': Guardian.objects.all().order_by('first_name')[:100],
    }
    return render(request, 'dashboard/students/student_create.html', context)


@login_required(login_url='login')
def class_promotion_view(request):
    """
    Console for executing year-end student promotions, retentions, and class transitions.
    """
    if not _check_students_permission(request, 'students.promotion.approve'):
        messages.error(request, "You do not have permission to execute class promotions.")
        return redirect('students-student-list')

    academic_years = AcademicYear.objects.all().order_by('-start_date')
    current_year = AcademicYear.objects.filter(is_current=True).first()
    classes = SchoolClass.objects.all().order_by('order')
    sections = Section.objects.all().order_by('name')

    # Selected source filters
    src_year_id = request.GET.get('source_year')
    src_class = request.GET.get('source_class')
    src_section = request.GET.get('source_section')

    source_year = get_object_or_404(AcademicYear, pk=src_year_id) if src_year_id else current_year

    students = []
    if source_year and src_class:
        enrollment_qs = ClassEnrollment.objects.filter(
            academic_year=source_year,
            class_name=src_class,
            student__status=StudentStatus.ACTIVE
        ).select_related('student')
        if src_section:
            enrollment_qs = enrollment_qs.filter(section_name=src_section)
        students = enrollment_qs.order_by('roll_number', 'student__first_name')

    if request.method == 'POST':
        tgt_year_id = request.POST.get('target_year_id')
        tgt_class = request.POST.get('target_class')
        tgt_section = request.POST.get('target_section', 'A')

        target_year = get_object_or_404(AcademicYear, pk=tgt_year_id)

        student_ids = request.POST.getlist('selected_students')
        promotions = []
        for s_id in student_ids:
            act = request.POST.get(f"action_{s_id}", 'PROMOTE')
            custom_class = request.POST.get(f"target_class_{s_id}") or tgt_class
            custom_section = request.POST.get(f"target_section_{s_id}") or tgt_section
            roll_str = request.POST.get(f"roll_{s_id}", '').strip()
            roll = int(roll_str) if roll_str else None

            promotions.append({
                'student_id': int(s_id),
                'action': act,
                'target_class': custom_class,
                'target_section': custom_section,
                'roll_number': roll
            })

        try:
            summary = StudentService.promote_class_with_retention(
                source_academic_year=source_year,
                target_academic_year=target_year,
                promotions=promotions,
                user=request.user,
                request=request
            )
            messages.success(
                request,
                f"Promotion executed: {summary['promoted']} promoted, {summary['detained']} detained, "
                f"{summary['passed_out']} passed out, {summary['withdrawn']} withdrawn."
            )
            return redirect(f"{request.path}?source_year={target_year.pk}&source_class={tgt_class}&source_section={tgt_section}")
        except Exception as e:
            messages.error(request, f"Error processing class promotion: {e}")

    context = {
        'academic_years': academic_years,
        'source_year': source_year,
        'classes': classes,
        'sections': sections,
        'src_class': src_class,
        'src_section': src_section,
        'students': students,
    }
    return render(request, 'dashboard/students/class_promotion.html', context)


@login_required(login_url='login')
def certificates_list_view(request):
    """
    Consolidated view for Transfer Certificates (TC) and Bonafide Certificates.
    """
    if not _check_students_permission(request, 'students.profile.view'):
        messages.error(request, "You do not have permission to view certificates.")
        return redirect('home-dashboard')

    can_issue_tc = _check_students_permission(request, 'students.tc.issue')

    tc_list = TransferCertificate.objects.select_related('student').order_by('-issue_date')[:100]
    bonafide_list = BonafideCertificate.objects.select_related('student', 'academic_year').order_by('-issue_date')[:100]

    context = {
        'tc_list': tc_list,
        'bonafide_list': bonafide_list,
        'can_issue_tc': can_issue_tc,
    }
    return render(request, 'dashboard/students/certificates_list.html', context)


@login_required(login_url='login')
def print_tc_view(request, pk):
    """
    Printable Transfer Certificate (TC) document.
    """
    if not _check_students_permission(request, 'students.profile.view'):
        messages.error(request, "You do not have permission to view certificates.")
        return redirect('home-dashboard')

    tc = get_object_or_404(
        TransferCertificate.objects.select_related('student__primary_guardian'),
        pk=pk
    )
    school = SchoolProfile.objects.first()
    context = {
        'tc': tc,
        'student': tc.student,
        'school': school,
    }
    return render(request, 'dashboard/students/print_tc.html', context)


@login_required(login_url='login')
def print_bonafide_view(request, pk):
    """
    Printable Bonafide Certificate document.
    """
    if not _check_students_permission(request, 'students.profile.view'):
        messages.error(request, "You do not have permission to view certificates.")
        return redirect('home-dashboard')

    bonafide = get_object_or_404(
        BonafideCertificate.objects.select_related('student__primary_guardian', 'academic_year'),
        pk=pk
    )
    school = SchoolProfile.objects.first()
    context = {
        'bonafide': bonafide,
        'student': bonafide.student,
        'school': school,
    }
    return render(request, 'dashboard/students/print_bonafide.html', context)


@login_required(login_url='login')
def guardians_list_view(request):
    """
    Directory of guardians and their wards.
    """
    if not _check_students_permission(request, 'students.profile.view'):
        messages.error(request, "You do not have permission to view guardians.")
        return redirect('home-dashboard')

    search_query = request.GET.get('q', '').strip()
    guardians = Guardian.objects.prefetch_related('students').all()

    if search_query:
        guardians = guardians.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(students__admission_number__icontains=search_query)
        ).distinct()

    context = {
        'guardians': guardians.order_by('first_name')[:150],
        'search_query': search_query,
    }
    return render(request, 'dashboard/students/guardians_list.html', context)
