from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.hrms.models import Department, Designation, StaffProfile, StaffLeaveRequest, PayrollRun, Payslip
from apps.hrms.serializers import (
    DepartmentSerializer, DesignationSerializer, StaffProfileSerializer,
    StaffLeaveRequestSerializer, PayrollRunSerializer, PayslipSerializer
)
from apps.rbac.permissions import HasDynamicPermission


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'hrms'


class DesignationViewSet(viewsets.ModelViewSet):
    queryset = Designation.objects.select_related('department').all()
    serializer_class = DesignationSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'hrms'


class StaffProfileViewSet(viewsets.ModelViewSet):
    queryset = StaffProfile.objects.select_related('user_account', 'department', 'designation').all()
    serializer_class = StaffProfileSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'hrms'
    filterset_fields = ['department', 'is_teaching']
    search_fields = ['employee_id', 'user_account__first_name', 'user_account__last_name']


class StaffLeaveRequestViewSet(viewsets.ModelViewSet):
    queryset = StaffLeaveRequest.objects.select_related('staff').all()
    serializer_class = StaffLeaveRequestSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'hrms'


class PayrollRunViewSet(viewsets.ModelViewSet):
    queryset = PayrollRun.objects.prefetch_related('payslips').all()
    serializer_class = PayrollRunSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'hrms'


class PayslipViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Payslip.objects.select_related('staff', 'payroll_run').all()
    serializer_class = PayslipSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'hrms'
