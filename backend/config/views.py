from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from apps.students.models import Student, StudentStatus
from apps.school_config.models import AcademicYear, SchoolProfile
from apps.admissions.models import AdmissionApplication
from apps.finance.models import StudentInvoice
from apps.hrms.models import StaffProfile
from apps.rbac.models import Role

User = get_user_model()


@login_required(login_url='login')
def dashboard_view(request):
    """
    Renders the primary school ERP web dashboard with real metrics.
    Requires authenticated session.
    """
    current_year = AcademicYear.objects.filter(is_current=True).first()
    school_profile = SchoolProfile.objects.first()

    total_students = Student.objects.filter(status=StudentStatus.ACTIVE).count()
    total_applications = AdmissionApplication.objects.count()
    total_staff = StaffProfile.objects.count()
    total_roles = Role.objects.count()
    total_users = User.objects.count()

    recent_users = User.objects.order_by('-date_joined')[:5]
    recent_applications = AdmissionApplication.objects.order_by('-created_at')[:5]

    context = {
        'school_profile': school_profile,
        'current_year': current_year,
        'total_students': total_students,
        'total_applications': total_applications,
        'total_staff': total_staff,
        'total_roles': total_roles,
        'total_users': total_users,
        'recent_users': recent_users,
        'recent_applications': recent_applications,
    }
    return render(request, 'dashboard/dashboard.html', context)
