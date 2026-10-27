import uuid
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.finance.models import StudentInvoice, PaymentTransaction, InvoiceStatus
from apps.audit.services import AuditService
from apps.audit.models import AuditAction
from common.exceptions import SchoolERPException


class FinanceService:
    """
    Handles payment recording, invoice reconciliation, and reversals with audit trails.
    """

    @classmethod
    @transaction.atomic
    def record_payment(cls, invoice_id, amount, payment_mode, reference_number, user):
        invoice = StudentInvoice.objects.select_for_update().get(id=invoice_id)
        amount = Decimal(str(amount))

        if amount <= 0:
            raise SchoolERPException("Payment amount must be greater than zero.")

        if amount > invoice.balance_due:
            raise SchoolERPException(f"Amount ₹{amount} exceeds remaining balance ₹{invoice.balance_due}.")

        receipt_num = f"REC-{timezone.now().year}-{uuid.uuid4().hex[:6].upper()}"
        payment = PaymentTransaction.objects.create(
            receipt_number=receipt_num,
            invoice=invoice,
            amount_paid=amount,
            payment_mode=payment_mode,
            reference_number=reference_number,
            collected_by=user
        )

        invoice.paid_amount += amount
        if invoice.balance_due <= 0:
            invoice.status = InvoiceStatus.PAID
        else:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        invoice.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module='finance',
            entity_type='PaymentTransaction',
            entity_id=payment.id,
            actor=user,
            reason=f"Payment recorded: ₹{amount} for invoice {invoice.invoice_number}"
        )
        return payment

    @classmethod
    @transaction.atomic
    def reverse_payment(cls, payment_id, reason, user):
        payment = PaymentTransaction.objects.select_for_update().get(id=payment_id)
        if payment.is_reversed:
            raise SchoolERPException("Payment has already been reversed.")

        if not reason:
            raise SchoolERPException("Reversal reason is strictly required.")

        invoice = payment.invoice
        payment.is_reversed = True
        payment.reversal_reason = reason
        payment.save()

        invoice.paid_amount -= payment.amount_paid
        if invoice.paid_amount <= 0:
            invoice.status = InvoiceStatus.UNPAID
        else:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        invoice.save()

        AuditService.log(
            action=AuditAction.PAYMENT_REVERSAL,
            module='finance',
            entity_type='PaymentTransaction',
            entity_id=payment.id,
            actor=user,
            reason=reason
        )
        return payment
