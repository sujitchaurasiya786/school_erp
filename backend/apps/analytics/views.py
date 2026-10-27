from django.db.models import Count, Sum
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.students.models import Student, StudentStatus
from apps.admissions.models import AdmissionApplication
from apps.finance.models import StudentInvoice, PaymentTransaction
from apps.rbac.permissions import HasDynamicPermission


class DashboardOverviewAPIView(APIView):
    """
    Consolidated executive KPI metrics for School Admin / Principal.
    """
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'analytics'

    def get(self, request):
        active_students = Student.objects.filter(status=StudentStatus.ACTIVE).count()
        total_applications = AdmissionApplication.objects.count()
        finance_summary = StudentInvoice.objects.aggregate(
            total_invoiced=Sum('total_amount'),
            total_collected=Sum('paid_amount'),
        )
        total_collected = finance_summary.get('total_collected') or 0
        total_invoiced = finance_summary.get('total_invoiced') or 0
        total_dues = total_invoiced - total_collected

        return Response({
            'status': 'success',
            'data': {
                'total_active_students': active_students,
                'total_admission_applications': total_applications,
                'total_fees_invoiced': float(total_invoiced),
                'total_fees_collected': float(total_collected),
                'total_outstanding_dues': float(total_dues),
            }
        })
