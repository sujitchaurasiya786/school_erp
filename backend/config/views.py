from django.shortcuts import render
from apps.students.models import Student, StudentStatus
from apps.school_config.models import AcademicYear, SchoolProfile
from apps.admissions.models import AdmissionApplication
from apps.finance.models import StudentInvoice


def dashboard_view(request):
    """
    Renders the primary web dashboard.
    """
    current_year = AcademicYear.objects.filter(is_current=True).first()
    school_profile = SchoolProfile.objects.first()

    total_students = Student.objects.filter(status=StudentStatus.ACTIVE).count()
    total_applications = AdmissionApplication.objects.count()

    context = {
        'school_profile': school_profile,
        'current_year': current_year,
        'total_students': total_students,
        'total_applications': total_applications,
    }
    return render(request, 'dashboard.html', context)
