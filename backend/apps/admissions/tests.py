from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from apps.identity.models import User, UserType
from apps.school_config.models import AcademicYear
from apps.admissions.models import AdmissionApplication, ApplicationStatus
from apps.admissions.services import AdmissionService


class AdmissionsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username='admin_adm_test',
            email='admin_adm@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.app = AdmissionApplication.objects.create(
            application_number='APP-TEST-001',
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Rohan',
            last_name='Verma',
            date_of_birth=date(2020, 1, 15),
            gender='M',
            father_name='Suresh Verma',
            mother_name='Anita Verma',
            guardian_phone='9811223344',
            address='Civil Lines',
            status=ApplicationStatus.SUBMITTED
        )

    def test_application_approval(self):
        updated = AdmissionService.approve_application(self.app, self.admin, remarks='Interview passed')
        self.assertEqual(updated.status, ApplicationStatus.APPROVED)
        self.assertEqual(updated.interview_remarks, 'Interview passed')

    def test_application_rejection(self):
        updated = AdmissionService.reject_application(self.app, self.admin, reason='Age criteria not met')
        self.assertEqual(updated.status, ApplicationStatus.REJECTED)
        self.assertEqual(updated.rejection_reason, 'Age criteria not met')
