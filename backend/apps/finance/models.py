from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class FeeFrequency(models.TextChoices):
    ONE_TIME = 'ONE_TIME', _('One Time')
    MONTHLY = 'MONTHLY', _('Monthly')
    QUARTERLY = 'QUARTERLY', _('Quarterly')
    HALF_YEARLY = 'HALF_YEARLY', _('Half-Yearly')
    ANNUAL = 'ANNUAL', _('Annual')


class PaymentMode(models.TextChoices):
    CASH = 'CASH', _('Cash')
    CHEQUE = 'CHEQUE', _('Cheque')
    UPI = 'UPI', _('UPI / Net Banking')
    DEMAND_DRAFT = 'DEMAND_DRAFT', _('Demand Draft')


class InvoiceStatus(models.TextChoices):
    UNPAID = 'UNPAID', _('Unpaid')
    PARTIALLY_PAID = 'PARTIALLY_PAID', _('Partially Paid')
    PAID = 'PAID', _('Paid')
    CANCELLED = 'CANCELLED', _('Cancelled')


class FeeHead(TimeStampedModel):
    """
    Fee categories (e.g. Tuition Fee, Admission Fee, Annual Charges, Exam Fee, Transport Fee).
    """
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'finance_fee_heads'

    def __str__(self):
        return self.name


class FeeStructure(TimeStampedModel):
    """
    Class-wise fee structure per academic year.
    """
    academic_year = models.ForeignKey('school_config.AcademicYear', on_delete=models.CASCADE)
    class_name = models.CharField(max_length=50, help_text=_("e.g. Class 10"))
    fee_head = models.ForeignKey(FeeHead, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    frequency = models.CharField(max_length=20, choices=FeeFrequency.choices, default=FeeFrequency.MONTHLY)

    class Meta:
        db_table = 'finance_fee_structures'
        unique_together = ('academic_year', 'class_name', 'fee_head')

    def __str__(self):
        return f"{self.class_name} - {self.fee_head.name}: ₹{self.amount} ({self.frequency})"


class StudentInvoice(TimeStampedModel):
    """
    Invoice generated for a student.
    """
    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    student = models.ForeignKey('students.Student', on_delete=models.PROTECT, related_name='invoices')
    academic_year = models.ForeignKey('school_config.AcademicYear', on_delete=models.PROTECT)
    title = models.CharField(max_length=150)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    concession_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    fine_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.UNPAID)

    class Meta:
        db_table = 'finance_invoices'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.invoice_number} - {self.student.admission_number}: ₹{self.total_amount}"

    @property
    def balance_due(self):
        return (self.total_amount + self.fine_amount) - (self.paid_amount + self.concession_amount)


class PaymentTransaction(TimeStampedModel):
    """
    Payment receipts. Never deleted; only reversed with an audit record.
    """
    receipt_number = models.CharField(max_length=50, unique=True, db_index=True)
    invoice = models.ForeignKey(StudentInvoice, on_delete=models.PROTECT, related_name='payments')
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices, default=PaymentMode.CASH)
    reference_number = models.CharField(max_length=100, blank=True, default='', help_text=_("Cheque/UPI ref"))
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    is_reversed = models.BooleanField(default=False)
    reversal_reason = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'finance_payments'

    def __str__(self):
        return f"Receipt #{self.receipt_number} - ₹{self.amount_paid}"
