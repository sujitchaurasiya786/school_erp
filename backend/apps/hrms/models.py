from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class Department(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)

    class Meta:
        db_table = 'hrms_departments'

    def __str__(self):
        return self.name


class Designation(TimeStampedModel):
    title = models.CharField(max_length=100, unique=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='designations')

    class Meta:
        db_table = 'hrms_designations'

    def __str__(self):
        return f"{self.title} ({self.department.name})"


class StaffProfile(TimeStampedModel):
    user_account = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='staff_profile')
    employee_id = models.CharField(max_length=30, unique=True, db_index=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    designation = models.ForeignKey(Designation, on_delete=models.PROTECT)
    is_teaching = models.BooleanField(default=True)
    qualification = models.CharField(max_length=150, blank=True, default='')
    joining_date = models.DateField()
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    class Meta:
        db_table = 'hrms_staff_profiles'

    def __str__(self):
        return f"{self.employee_id} - {self.user_account.get_full_name() or self.user_account.username}"


class StaffLeaveRequest(TimeStampedModel):
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.CharField(max_length=50, default='Casual Leave')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    is_approved = models.BooleanField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        db_table = 'hrms_leave_requests'


class PayrollRun(TimeStampedModel):
    academic_year = models.ForeignKey('school_config.AcademicYear', on_delete=models.PROTECT)
    month = models.PositiveSmallIntegerField(help_text=_("1-12"))
    year = models.PositiveIntegerField()
    total_disbursed = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)
    is_finalized = models.BooleanField(default=False)

    class Meta:
        db_table = 'hrms_payroll_runs'
        unique_together = ('month', 'year')

    def __str__(self):
        return f"Payroll {self.month}/{self.year}"


class Payslip(TimeStampedModel):
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payslips')
    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name='payslips')
    basic_pay = models.DecimalField(max_digits=12, decimal_places=2)
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'hrms_payslips'
        unique_together = ('payroll_run', 'staff')
