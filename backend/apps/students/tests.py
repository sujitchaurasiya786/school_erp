from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from apps.identity.models import User, UserType
from apps.students.models import Student, Guardian, StudentStatus
from apps.students.services import StudentService


class StudentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username='admin_student_test',
            email='admin_student@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.guardian = Guardian.objects.create(
            first_name='Ramesh',
            last_name='Kumar',
            relationship='Father',
            phone='9876543210',
            email='ramesh@test.com'
        )
        self.student = Student.objects.create(
            admission_number='ADM-2026-001',
            first_name='Aarav',
            last_name='Kumar',
            date_of_birth=date(2015, 5, 10),
            gender='M',
            address='123 Green Park',
            primary_guardian=self.guardian,
            admission_date=date(2026, 4, 1),
            status=StudentStatus.ACTIVE
        )

    def test_student_list_api(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/v1/students/profiles/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_tc_issuance(self):
        tc = StudentService.issue_transfer_certificate(
            student_id=self.student.id,
            reason='Relocating to another city',
            user=self.admin
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.TC_ISSUED)
        self.assertTrue(tc.tc_number.startswith('TC-'))
