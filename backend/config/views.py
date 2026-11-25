from datetime import datetime
from django.contrib import messages
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from apps.academics.models import ClassSubject, SchoolClass, Section, TimetableSlot
from apps.admissions.models import AdmissionApplication
from apps.attendance.models import AttendanceStatus, Holiday, StaffDailyAttendance, StudentDailyAttendance
from apps.examinations.models import Assessment, ExamMark
from apps.finance.models import StudentInvoice
from apps.helpdesk.models import Ticket, TicketStatus
from apps.hrms.models import StaffProfile
from apps.identity.models import UserType
from apps.library.models import BookIssueRecord
from apps.rbac.models import Role
from apps.school_config.models import AcademicYear, SchoolProfile
from apps.students.models import ClassEnrollment, Student, StudentStatus

User = get_user_model()


@login_required(login_url='login')
def dashboard_view(request):
    """
    Renders the primary school ERP web dashboard with role-aware redirection.
    - Teachers automatically see the Teacher Dashboard.
    - Students automatically see the Student Dashboard.
    - Admin/Principal/Staff see the Admin Overview strictly bounded by their approved permissions.
    """
    if request.user.user_type == UserType.TEACHER:
        return redirect('teacher-dashboard')
    elif request.user.user_type == UserType.STUDENT:
        return redirect('student-dashboard')

    current_year = AcademicYear.objects.filter(is_current=True).first()
    school_profile = SchoolProfile.objects.first()

    from apps.rbac.services import RBACService
    can_view_students = RBACService.has_permission(request.user, 'students.profile.view')
    can_view_applications = RBACService.has_permission(request.user, 'admissions.application.view')
    can_view_staff = RBACService.has_permission(request.user, 'hrms.staff.view')
    can_view_roles = RBACService.has_permission(request.user, 'rbac.role.view')
    can_view_users = RBACService.has_permission(request.user, 'identity.users.view')
    can_view_student_att = RBACService.has_permission(request.user, 'attendance.record.view')
    can_view_staff_att = RBACService.has_permission(request.user, 'attendance.staff.view')

    today = timezone.localdate()
    total_students = Student.objects.filter(status=StudentStatus.ACTIVE).count() if can_view_students else 0
    total_applications = AdmissionApplication.objects.count() if can_view_applications else 0
    total_staff = StaffProfile.objects.count() if can_view_staff else 0
    total_staff_users = User.objects.filter(
        is_active=True,
        user_type__in=[UserType.TEACHER, UserType.STAFF, UserType.PRINCIPAL, UserType.ADMIN, UserType.ACCOUNTANT]
    ).count() if (can_view_staff or can_view_staff_att) else 0
    total_roles = Role.objects.count() if can_view_roles else 0
    total_users = User.objects.count() if can_view_users else 0

    student_present_today = 0
    student_absent_today = 0
    if can_view_students or can_view_student_att:
        stu_att_today = StudentDailyAttendance.objects.filter(date=today)
        if current_year:
            stu_att_today = stu_att_today.filter(academic_year=current_year)
        student_present_today = stu_att_today.filter(status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]).count()
        student_absent_today = stu_att_today.filter(status=AttendanceStatus.ABSENT).count()

    staff_present_today = 0
    staff_absent_today = 0
    if can_view_staff or can_view_staff_att:
        stf_att_today = StaffDailyAttendance.objects.filter(date=today)
        staff_present_today = stf_att_today.filter(status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]).count()
        staff_absent_today = stf_att_today.filter(status=AttendanceStatus.ABSENT).count()

    recent_users = User.objects.order_by('-date_joined')[:5] if can_view_users else []
    recent_applications = AdmissionApplication.objects.order_by('-created_at')[:5] if can_view_applications else []

    user_roles = request.user.role_assignments.filter(is_active=True).select_related('role')
    user_permissions = RBACService.get_user_permissions(request.user)

    context = {
        'school_profile': school_profile,
        'current_year': current_year,
        'today': today,
        'total_students': total_students,
        'student_present_today': student_present_today,
        'student_absent_today': student_absent_today,
        'total_applications': total_applications,
        'total_staff': total_staff,
        'total_staff_users': total_staff_users,
        'staff_present_today': staff_present_today,
        'staff_absent_today': staff_absent_today,
        'total_roles': total_roles,
        'total_users': total_users,
        'recent_users': recent_users,
        'recent_applications': recent_applications,
        'can_view_students': can_view_students,
        'can_view_applications': can_view_applications,
        'can_view_staff': can_view_staff,
        'can_view_roles': can_view_roles,
        'can_view_users': can_view_users,
        'can_view_student_att': can_view_student_att,
        'can_view_staff_att': can_view_staff_att,
        'user_roles': user_roles,
        'user_permissions_count': len(user_permissions),
    }
    return render(request, 'dashboard/dashboard.html', context)


@login_required(login_url='login')
def teacher_dashboard_view(request):
    """
    Dedicated Teacher Dashboard:
      1. Today's Timetable (periods, rooms, live status)
      2. One-Click Attendance (highlighting sections pending roll call)
      3. Assignment & Grading Queue (homework/assessments pending marks entry)
      4. At-Risk Student Alerts (attendance < 75% or scores < 40%)
      5. Parent Communication & PTM Scheduling (unread tickets/queries)
    """
    current_year = AcademicYear.objects.filter(is_current=True).first()
    school_profile = SchoolProfile.objects.first()
    today = timezone.now().date()
    today_dow = today.isoweekday()  # 1=Monday ... 7=Sunday
    now_time = timezone.now().time()

    # Allow Admin/Principal to preview any teacher's dashboard
    teacher_user = request.user
    if request.user.is_superuser or request.user.user_type in [UserType.ADMIN, UserType.PRINCIPAL]:
        teacher_id = request.GET.get('teacher_id')
        if teacher_id:
            candidate = User.objects.filter(pk=teacher_id).first()
            if candidate:
                teacher_user = candidate
        elif request.user.user_type != UserType.TEACHER:
            candidate = User.objects.filter(user_type=UserType.TEACHER).first()
            if candidate:
                teacher_user = candidate

    all_teachers = User.objects.filter(
        Q(user_type=UserType.TEACHER) | Q(staff_profile__is_teaching=True)
    ).distinct().order_by('first_name', 'last_name', 'username')

    # 1. TODAY'S TIMETABLE
    timetable_slots = []
    if current_year:
        slots_qs = TimetableSlot.objects.filter(
            academic_year=current_year,
            day_of_week=today_dow,
            class_subject__teacher=teacher_user
        ).select_related(
            'period',
            'class_subject__section__school_class',
            'class_subject__subject',
            'room_ref'
        ).order_by('period__period_number')

        for slot in slots_qs:
            p = slot.period
            if p.start_time <= now_time <= p.end_time:
                slot_status = 'IN_PROGRESS'
                badge_class = 'bg-danger text-white'
                badge_text = 'Happening Now'
            elif now_time < p.start_time:
                slot_status = 'UPCOMING'
                badge_class = 'bg-primary-subtle text-primary border border-primary-subtle'
                badge_text = 'Upcoming'
            else:
                slot_status = 'COMPLETED'
                badge_class = 'bg-light text-muted'
                badge_text = 'Completed'

            room_disp = (slot.room_ref.room_number if slot.room_ref else slot.room) or slot.class_subject.section.room_number or 'Classroom'
            timetable_slots.append({
                'slot': slot,
                'period': p,
                'subject': slot.class_subject.subject,
                'section': slot.class_subject.section,
                'room_display': room_disp,
                'status': slot_status,
                'badge_class': badge_class,
                'badge_text': badge_text,
            })

    # 2. ONE-CLICK ATTENDANCE
    # Assigned sections (as Class Teacher or Subject Teacher)
    assigned_sections = Section.objects.filter(
        Q(class_teacher=teacher_user) | Q(class_subjects__teacher=teacher_user)
    ).distinct().select_related('school_class').order_by('school_class__order', 'name')

    attendance_classes = []
    pending_attendance_count = 0
    ct_total_students = 0
    ct_present_today = 0
    ct_absent_today = 0
    ct_section_labels = []
    for sec in assigned_sections:
        is_marked = False
        present_count = 0
        absent_count = 0
        total_students = Student.objects.filter(
            enrollments__academic_year=current_year,
            enrollments__class_name=sec.school_class.name,
            enrollments__section_name=sec.name,
            status=StudentStatus.ACTIVE
        ).count()

        if current_year:
            att_qs = StudentDailyAttendance.objects.filter(
                academic_year=current_year,
                class_name=sec.school_class.name,
                section_name=sec.name,
                date=today
            )
            is_marked = att_qs.exists()
            if is_marked:
                present_count = att_qs.filter(status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]).count()
                absent_count = att_qs.filter(status=AttendanceStatus.ABSENT).count()
            else:
                pending_attendance_count += 1

        is_class_teacher = (sec.class_teacher_id == teacher_user.id)
        if is_class_teacher:
            ct_total_students += total_students
            ct_present_today += present_count
            ct_absent_today += absent_count
            ct_section_labels.append(f"{sec.school_class.name} - {sec.name}")

        attendance_classes.append({
            'section': sec,
            'is_class_teacher': is_class_teacher,
            'is_marked': is_marked,
            'present_count': present_count,
            'absent_count': absent_count,
            'total_students': total_students,
            'direct_url': f"/dashboard/attendance/daily/?year={current_year.id if current_year else ''}&class_id={sec.school_class.id}&section_name={sec.name}&date={today.strftime('%Y-%m-%d')}"
        })

    ct_stats = {
        'has_class': len(ct_section_labels) > 0,
        'classes_label': ", ".join(ct_section_labels),
        'total_students': ct_total_students,
        'present_today': ct_present_today,
        'absent_today': ct_absent_today,
    }

    # 3. ASSIGNMENT & GRADING QUEUE
    teacher_subject_ids = ClassSubject.objects.filter(teacher=teacher_user).values_list('subject_id', flat=True)
    assessments_queue = []
    if current_year and teacher_subject_ids.exists():
        recent_assessments = Assessment.objects.filter(
            subject_id__in=teacher_subject_ids,
            term__academic_year=current_year
        ).select_related('term', 'subject').order_by('-created_at')[:6]

        for ass in recent_assessments:
            total_eligible = Student.objects.filter(
                enrollments__academic_year=current_year,
                enrollments__class_name=ass.class_name,
                status=StudentStatus.ACTIVE
            ).count()
            graded_count = ExamMark.objects.filter(
                assessment=ass,
                marks_obtained__isnull=False
            ).count()
            pending_count = max(0, total_eligible - graded_count)
            assessments_queue.append({
                'assessment': ass,
                'total_students': total_eligible,
                'graded_count': graded_count,
                'pending_count': pending_count,
                'is_completed': (pending_count == 0 and total_eligible > 0),
            })

    # 4. AT-RISK STUDENT ALERTS
    at_risk_students = []
    monitored_students_seen = set()
    for sec in assigned_sections:
        students = Student.objects.filter(
            enrollments__academic_year=current_year,
            enrollments__class_name=sec.school_class.name,
            enrollments__section_name=sec.name,
            status=StudentStatus.ACTIVE
        ).select_related('primary_guardian')

        for stu in students:
            if stu.id in monitored_students_seen:
                continue
            monitored_students_seen.add(stu.id)

            total_days = StudentDailyAttendance.objects.filter(student=stu, academic_year=current_year).count()
            present_days = StudentDailyAttendance.objects.filter(student=stu, academic_year=current_year, status=AttendanceStatus.PRESENT).count()
            att_pct = round((present_days / total_days * 100), 1) if total_days > 0 else 100.0

            recent_marks = ExamMark.objects.filter(
                student=stu,
                assessment__subject_id__in=teacher_subject_ids if teacher_subject_ids.exists() else []
            ).select_related('assessment__subject', 'assessment__term').order_by('-created_at')[:2]

            risk_reasons = []
            if total_days >= 3 and att_pct < 75.0:
                risk_reasons.append(f"Low Attendance: {att_pct}% (Below 75% CBSE threshold)")

            for rm in recent_marks:
                if rm.marks_obtained is not None and rm.assessment.maximum_marks > 0:
                    pct = (rm.marks_obtained / rm.assessment.maximum_marks) * 100
                    if pct < 40.0:
                        risk_reasons.append(f"Low Score: {rm.marks_obtained}/{rm.assessment.maximum_marks} ({round(pct,1)}%) in {rm.assessment.subject.name}")
                elif rm.is_absent:
                    risk_reasons.append(f"Absent in {rm.assessment.subject.name} ({rm.assessment.term.title})")

            if risk_reasons:
                at_risk_students.append({
                    'student': stu,
                    'class_name': sec.school_class.name,
                    'section_name': sec.name,
                    'roll_number': stu.current_roll_number,
                    'attendance_pct': att_pct,
                    'reasons': risk_reasons,
                    'guardian_phone': stu.primary_guardian.phone if stu.primary_guardian else None,
                    'guardian_name': f"{stu.primary_guardian.first_name} {stu.primary_guardian.last_name}" if stu.primary_guardian else None
                })

    # 5. PARENT COMMUNICATION & TICKETS
    parent_tickets_qs = Ticket.objects.filter(
        Q(assigned_to=teacher_user) | Q(creator__user_type=UserType.PARENT)
    ).select_related('category', 'creator').order_by('-created_at')

    unread_ticket_count = parent_tickets_qs.filter(status=TicketStatus.OPEN).count()
    parent_tickets = list(parent_tickets_qs[:6])

    context = {
        'school_profile': school_profile,
        'current_year': current_year,
        'today': today,
        'teacher_user': teacher_user,
        'all_teachers': all_teachers,
        'timetable_slots': timetable_slots,
        'attendance_classes': attendance_classes,
        'ct_stats': ct_stats,
        'pending_attendance_count': pending_attendance_count,
        'assessments_queue': assessments_queue,
        'at_risk_students': at_risk_students[:10],
        'parent_tickets': parent_tickets,
        'unread_ticket_count': unread_ticket_count,
    }
    return render(request, 'dashboard/teacher_dashboard.html', context)


@login_required(login_url='login')
def student_dashboard_view(request):
    """
    Dedicated Student Dashboard:
      1. Daily Class Schedule (periods, subject teachers, room numbers)
      2. Pending Homework & Exams (upcoming assessment deadlines with countdowns)
      3. Attendance Summary (CBSE 75% gauge, present/absent counts, 7-day pill timeline)
      4. Grades & Report Cards (marks, maximum marks, percentages, CBSE grades)
      5. Notices & Fee Status (balance due, invoices, library book returns, holidays)
    """
    current_year = AcademicYear.objects.filter(is_current=True).first()
    school_profile = SchoolProfile.objects.first()
    today = timezone.now().date()
    today_dow = today.isoweekday()

    # If the user is a student, lock to their own student profile
    student = getattr(request.user, 'student_profile', None)
    if not student:
        guardian = getattr(request.user, 'guardian_profile', None)
        if guardian:
            student = guardian.students.filter(status=StudentStatus.ACTIVE).first()

    # Scoping check: Teachers are strictly restricted to their assigned classes and sections
    is_teacher_user = (request.user.user_type == UserType.TEACHER and not request.user.is_superuser)
    teacher_sections = None
    if is_teacher_user:
        teacher_sections = Section.objects.filter(
            Q(class_teacher=request.user) | Q(class_subjects__teacher=request.user)
        ).distinct().select_related('school_class')
        allowed_class_ids = teacher_sections.values_list('school_class_id', flat=True)
        classes_list = SchoolClass.objects.filter(id__in=allowed_class_ids).order_by('order')
    else:
        classes_list = SchoolClass.objects.all().order_by('order')

    selected_class_name = request.GET.get('class_name', '').strip()
    selected_section_name = request.GET.get('section_name', '').strip().upper()
    search_roll = request.GET.get('roll_number', '').strip()
    search_admission = request.GET.get('admission_number', '').strip()

    if request.user.is_superuser or request.user.user_type in [UserType.ADMIN, UserType.PRINCIPAL, UserType.TEACHER, UserType.STAFF]:
        # 1. Filter by Admission Number
        if search_admission:
            matched_student = Student.objects.filter(admission_number__iexact=search_admission).first()
            if matched_student:
                student = matched_student
            else:
                messages.warning(request, f"No student found with Admission Number '{search_admission}'.")

        # 2. Filter by Class & Roll Number
        elif selected_class_name and search_roll:
            try:
                roll_int = int(search_roll)
                enr_qs = ClassEnrollment.objects.filter(
                    class_name__iexact=selected_class_name,
                    roll_number=roll_int
                ).select_related('student')
                if selected_section_name:
                    enr_qs = enr_qs.filter(section_name__iexact=selected_section_name)
                if current_year:
                    enr = enr_qs.filter(academic_year=current_year).first() or enr_qs.first()
                else:
                    enr = enr_qs.first()

                if enr and enr.student:
                    student = enr.student
                else:
                    messages.warning(request, f"No student found in '{selected_class_name}' with Roll Number '{search_roll}'.")
            except ValueError:
                messages.warning(request, f"Roll Number must be numeric. Received '{search_roll}'.")

        # 3. Filter by Roll Number alone (across classes)
        elif search_roll and search_roll.isdigit():
            enr_qs = ClassEnrollment.objects.filter(
                roll_number=int(search_roll)
            ).select_related('student')
            if current_year:
                enr = enr_qs.filter(academic_year=current_year).first() or enr_qs.first()
            else:
                enr = enr_qs.first()

            if enr and enr.student:
                student = enr.student
                selected_class_name = enr.class_name
                selected_section_name = enr.section_name
            else:
                messages.warning(request, f"No student found with Roll Number '{search_roll}'.")

        # 4. Filter by Class alone (first student in class)
        elif selected_class_name:
            enr_qs = ClassEnrollment.objects.filter(
                class_name__iexact=selected_class_name
            ).select_related('student')
            if selected_section_name:
                enr_qs = enr_qs.filter(section_name__iexact=selected_section_name)
            if current_year:
                enr = enr_qs.filter(academic_year=current_year).first() or enr_qs.first()
            else:
                enr = enr_qs.first()

            if enr and enr.student:
                student = enr.student
            else:
                messages.warning(request, f"No student found enrolled in '{selected_class_name}'.")

        # 5. Filter by student_id direct parameter
        elif request.GET.get('student_id'):
            matched_student = Student.objects.filter(pk=request.GET.get('student_id')).first()
            if matched_student:
                student = matched_student

        # Teacher Boundary Check: Teachers can ONLY see students in their assigned sections
        if is_teacher_user and student:
            allowed_class_names = set(teacher_sections.values_list('school_class__name', flat=True))
            allowed_section_names = set(teacher_sections.values_list('name', flat=True))
            ce = student.current_enrollment
            if not ce or ce.class_name not in allowed_class_names or ce.section_name not in allowed_section_names:
                messages.error(request, "Permission Denied: Teachers can only view students enrolled in their assigned classes and sections.")
                student = None

        # 6. Default fallback to the first enrolled student if no filter was submitted
        has_search_params = bool(search_admission or search_roll or selected_class_name or request.GET.get('student_id'))
        if not student and not has_search_params:
            if is_teacher_user:
                first_enr = ClassEnrollment.objects.filter(
                    academic_year=current_year,
                    class_name__in=teacher_sections.values_list('school_class__name', flat=True),
                    section_name__in=teacher_sections.values_list('name', flat=True)
                ).select_related('student').first() if current_year else None
                student = first_enr.student if first_enr else None
            else:
                first_enr = ClassEnrollment.objects.filter(academic_year=current_year).select_related('student').first() if current_year else None
                student = first_enr.student if first_enr else Student.objects.filter(status=StudentStatus.ACTIVE).first()

    if student and not selected_class_name:
        selected_class_name = student.current_class_name
        selected_section_name = student.current_section_name

    # 1. DAILY CLASS SCHEDULE
    schedule_slots = []
    if student and current_year:
        ce = student.current_enrollment
        if ce:
            section = Section.objects.filter(
                school_class__name=ce.class_name,
                name=ce.section_name
            ).first()
            if section:
                slots = TimetableSlot.objects.filter(
                    academic_year=current_year,
                    day_of_week=today_dow,
                    class_subject__section=section
                ).select_related(
                    'period',
                    'class_subject__subject',
                    'class_subject__teacher',
                    'room_ref'
                ).order_by('period__period_number')

                now_time = timezone.now().time()
                for slot in slots:
                    p = slot.period
                    if p.start_time <= now_time <= p.end_time:
                        slot_status = 'LIVE'
                        badge_class = 'bg-danger text-white'
                        badge_text = 'Happening Now'
                    elif now_time < p.start_time:
                        slot_status = 'UPCOMING'
                        badge_class = 'bg-primary-subtle text-primary border border-primary-subtle'
                        badge_text = 'Upcoming'
                    else:
                        slot_status = 'DONE'
                        badge_class = 'bg-light text-muted'
                        badge_text = 'Completed'

                    schedule_slots.append({
                        'period': p,
                        'subject': slot.class_subject.subject,
                        'teacher_name': slot.class_subject.teacher.get_full_name() if slot.class_subject.teacher else 'Teacher Assigned',
                        'room': (slot.room_ref.room_number if slot.room_ref else slot.room) or section.room_number or 'Classroom',
                        'status': slot_status,
                        'badge_class': badge_class,
                        'badge_text': badge_text,
                    })

    # 2. PENDING HOMEWORK & EXAMS
    upcoming_exams = []
    if student and current_year:
        ce = student.current_enrollment
        if ce:
            assessments = Assessment.objects.filter(
                class_name=ce.class_name,
                term__academic_year=current_year,
                term__end_date__gte=today
            ).select_related('term', 'subject').order_by('term__start_date')[:6]

            for ass in assessments:
                days_left = (ass.term.start_date - today).days
                if days_left < 0:
                    countdown_str = "Ongoing Now"
                    badge_style = "bg-warning text-dark"
                elif days_left == 0:
                    countdown_str = "Today"
                    badge_style = "bg-danger text-white"
                elif days_left == 1:
                    countdown_str = "Tomorrow"
                    badge_style = "bg-primary text-white"
                else:
                    countdown_str = f"In {days_left} days"
                    badge_style = "bg-info-subtle text-info border border-info-subtle"

                upcoming_exams.append({
                    'term_title': ass.term.title,
                    'exam_type': ass.term.get_exam_type_display(),
                    'subject': ass.subject.name,
                    'max_marks': ass.maximum_marks,
                    'start_date': ass.term.start_date,
                    'end_date': ass.term.end_date,
                    'countdown': countdown_str,
                    'badge_style': badge_style,
                })

    # 3. ATTENDANCE SUMMARY
    att_total = 0
    att_present = 0
    att_absent = 0
    att_leave = 0
    att_pct = 100.0
    recent_attendance = []
    if student and current_year:
        att_qs = StudentDailyAttendance.objects.filter(
            student=student,
            academic_year=current_year
        ).order_by('-date')

        att_total = att_qs.count()
        att_present = att_qs.filter(status=AttendanceStatus.PRESENT).count()
        att_absent = att_qs.filter(status=AttendanceStatus.ABSENT).count()
        att_leave = att_qs.filter(status=AttendanceStatus.LEAVE).count()
        if att_total > 0:
            att_pct = round((att_present / att_total) * 100, 1)

        recent_attendance = list(att_qs[:7])

    # 4. GRADES & REPORT CARDS
    exam_marks_list = []
    if student:
        marks_qs = ExamMark.objects.filter(
            student=student
        ).select_related('assessment__term', 'assessment__subject').order_by('-assessment__term__start_date', 'assessment__subject__name')[:8]

        for m in marks_qs:
            pct = round((m.marks_obtained / m.assessment.maximum_marks * 100), 1) if (m.marks_obtained is not None and m.assessment.maximum_marks > 0) else None
            grade = '—'
            if pct is not None:
                if pct >= 91: grade = 'A1'
                elif pct >= 81: grade = 'A2'
                elif pct >= 71: grade = 'B1'
                elif pct >= 61: grade = 'B2'
                elif pct >= 51: grade = 'C1'
                elif pct >= 41: grade = 'C2'
                elif pct >= 33: grade = 'D'
                else: grade = 'E (Needs Improvement)'

            exam_marks_list.append({
                'term': m.assessment.term.title,
                'subject': m.assessment.subject.name,
                'marks_obtained': m.marks_obtained,
                'max_marks': m.assessment.maximum_marks,
                'percentage': pct,
                'grade': grade,
                'is_absent': m.is_absent,
                'status': m.assessment.term.status,
            })

    # 5. NOTICES, LIBRARY, AND FEE STATUS
    from apps.rbac.services import RBACService
    can_view_student_finance = (
        request.user.is_superuser
        or request.user.user_type in [UserType.STUDENT, UserType.PARENT]
        or RBACService.has_permission(request.user, 'finance.invoice.view')
    )
    invoices_list = []
    total_balance_due = 0
    if student and current_year and can_view_student_finance:
        invoices = StudentInvoice.objects.filter(
            student=student,
            academic_year=current_year
        ).order_by('-created_at')[:4]
        for inv in invoices:
            total_balance_due += inv.balance_due
            invoices_list.append(inv)

    borrowed_books = []
    if request.user.is_authenticated:
        borrower_user = student.user_account if (student and student.user_account) else request.user
        borrowed_books = BookIssueRecord.objects.filter(
            borrower=borrower_user,
            return_date__isnull=True
        ).select_related('book_copy__catalog').order_by('due_date')[:4]

    upcoming_holidays = Holiday.objects.filter(
        academic_year=current_year,
        date__gte=today
    ).order_by('date')[:4] if current_year else []

    context = {
        'school_profile': school_profile,
        'current_year': current_year,
        'today': today,
        'student': student,
        'classes_list': classes_list,
        'selected_class_name': selected_class_name,
        'selected_section_name': selected_section_name,
        'search_roll': search_roll,
        'search_admission': search_admission,
        'schedule_slots': schedule_slots,
        'upcoming_exams': upcoming_exams,
        'att_total': att_total,
        'att_present': att_present,
        'att_absent': att_absent,
        'att_leave': att_leave,
        'att_pct': att_pct,
        'recent_attendance': recent_attendance,
        'exam_marks_list': exam_marks_list,
        'can_view_student_finance': can_view_student_finance,
        'invoices_list': invoices_list,
        'total_balance_due': total_balance_due,
        'borrowed_books': borrowed_books,
        'upcoming_holidays': upcoming_holidays,
    }
    return render(request, 'dashboard/student_dashboard.html', context)

