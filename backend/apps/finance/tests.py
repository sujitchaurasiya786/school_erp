from datetime import date
from decimal import Decimal
from django.test import TestCase
from apps.identity.models import User, UserType
from apps.school_config.models import AcademicYear
from apps.students.models import Student, Guardian, StudentStatus
from apps.finance.models import FeeHead, StudentInvoice, InvoiceStatus
from apps.finance.services import FinanceService


class FinanceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin_fin_test',
            email='admin_fin@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.guardian = Guardian.objects.create(
            first_name='Sunil',
            last_name='Sharma',
            relationship='Father',
            phone='9812345678'
        )
        self.student = Student.objects.create(
            admission_number='ADM-FIN-001',
            first_name='Kavya',
            last_name='Sharma',
            date_of_birth=date(2014, 8, 20),
            gender='F',
            address='Model Town',
            primary_guardian=self.guardian,
            admission_date=date(2026, 4, 1)
        )
        self.invoice = StudentInvoice.objects.create(
            invoice_number='INV-2026-001',
            student=self.student,
            academic_year=self.year,
            title='April 2026 Tuition Fee',
            total_amount=Decimal('5000.00'),
            due_date=date(2026, 4, 15)
        )

    def test_payment_and_reversal(self):
        # 1. Record partial payment
        payment = FinanceService.record_payment(
            invoice_id=self.invoice.id,
            amount=2000,
            payment_mode='UPI',
            reference_number='UPI123456',
            user=self.admin
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('2000.00'))
        self.assertEqual(self.invoice.status, InvoiceStatus.PARTIALLY_PAID)

        # 2. Reverse payment
        FinanceService.reverse_payment(
            payment_id=payment.id,
            reason='Duplicate transaction',
            user=self.admin
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('0.00'))
        self.assertEqual(self.invoice.status, InvoiceStatus.UNPAID)
