from rest_framework import serializers
from apps.hrms.models import Department, Designation, StaffProfile, StaffLeaveRequest, PayrollRun, Payslip


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'code']


class DesignationSerializer(serializers.ModelSerializer):
    department_name = serializers.ReadOnlyField(source='department.name')

    class Meta:
        model = Designation
        fields = ['id', 'title', 'department', 'department_name']


class StaffProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.ReadOnlyField(source='user_account.get_full_name')
    department_name = serializers.ReadOnlyField(source='department.name')
    designation_title = serializers.ReadOnlyField(source='designation.title')

    class Meta:
        model = StaffProfile
        fields = [
            'id', 'user_account', 'full_name', 'employee_id', 'department',
            'department_name', 'designation', 'designation_title',
            'is_teaching', 'qualification', 'joining_date'
        ]


class StaffLeaveRequestSerializer(serializers.ModelSerializer):
    staff_name = serializers.ReadOnlyField(source='staff.user_account.get_full_name')

    class Meta:
        model = StaffLeaveRequest
        fields = ['id', 'staff', 'staff_name', 'leave_type', 'start_date', 'end_date', 'reason', 'is_approved']


class PayslipSerializer(serializers.ModelSerializer):
    staff_name = serializers.ReadOnlyField(source='staff.user_account.get_full_name')
    employee_id = serializers.ReadOnlyField(source='staff.employee_id')

    class Meta:
        model = Payslip
        fields = ['id', 'payroll_run', 'staff', 'staff_name', 'employee_id', 'basic_pay', 'allowances', 'deductions', 'net_pay']


class PayrollRunSerializer(serializers.ModelSerializer):
    payslips = PayslipSerializer(many=True, read_only=True)

    class Meta:
        model = PayrollRun
        fields = ['id', 'academic_year', 'month', 'year', 'total_disbursed', 'is_finalized', 'payslips']
