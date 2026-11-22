import json
from datetime import date
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.academics.models import SchoolClass, Section
from apps.admissions.models import (
    AdmissionApplication, ApplicationDocument, ApplicationStatus,
    DocumentStatus, SeatQuota
)
from apps.admissions.services import AdmissionService
from apps.identity.models import User, UserType
from apps.rbac.models import ActionCode, ModuleCode, Permission, Role, ScopeType, UserRoleAssignment
from apps.school_config.models import AcademicYear
from apps.students.models import ClassEnrollment, Guardian, Student
from common.exceptions import BusinessConflictError


class AdmissionsServiceTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.admin = User.objects.create_superuser(
            username='admin_serv_test',
            email='admin_serv@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.school_class = SchoolClass.objects.create(name='Class 1', code='CLS-1', order=1)
        self.section_a = Section.objects.create(school_class=self.school_class, name='A')

    def test_create_application_and_auto_numbering(self):
        app1 = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Aarav',
            last_name='Sharma',
            date_of_birth=date(2020, 5, 10),
            gender='M',
            father_name='Rajesh Sharma',
            mother_name='Sunita Sharma',
            guardian_phone='9811001122',
            address='123 Park Street',
            actor=self.admin
        )
        self.assertTrue(app1.application_number.startswith('APP-2026'))
        self.assertEqual(app1.status, ApplicationStatus.SUBMITTED)

        app2 = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Ananya',
            last_name='Sharma',
            date_of_birth=date(2020, 8, 15),
            gender='F',
            father_name='Rajesh Sharma',
            mother_name='Sunita Sharma',
            guardian_phone='9811001122',
            address='123 Park Street',
            actor=self.admin
        )
        num1 = int(app1.application_number.split('-')[-1])
        num2 = int(app2.application_number.split('-')[-1])
        self.assertEqual(num2, num1 + 1)

    def test_create_application_validation_error(self):
        with self.assertRaises(ValidationError):
            AdmissionService.create_application(
                academic_year=self.year,
                applying_class='Class 1',
                first_name='',  # Blank first name
                last_name='Test',
                date_of_birth=date(2020, 1, 1),
                gender='M',
                father_name='Father',
                mother_name='Mother',
                guardian_phone='9811001122',
                address='Address'
            )

    def test_approve_application_with_seat_quota_and_waitlist(self):
        SeatQuota.objects.create(
            academic_year=self.year,
            class_name='Class 1',
            total_seats=1,
            waitlist_limit=1
        )

        app1 = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Student',
            last_name='One',
            date_of_birth=date(2020, 1, 1),
            gender='M',
            father_name='Father One',
            mother_name='Mother One',
            guardian_phone='9800000001',
            address='Address 1'
        )
        app2 = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Student',
            last_name='Two',
            date_of_birth=date(2020, 1, 2),
            gender='F',
            father_name='Father Two',
            mother_name='Mother Two',
            guardian_phone='9800000002',
            address='Address 2'
        )
        app3 = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Student',
            last_name='Three',
            date_of_birth=date(2020, 1, 3),
            gender='M',
            father_name='Father Three',
            mother_name='Mother Three',
            guardian_phone='9800000003',
            address='Address 3'
        )

        # First application approved within quota
        res1 = AdmissionService.approve_application(app1, user=self.admin)
        self.assertEqual(res1.status, ApplicationStatus.APPROVED)

        # Second application overflows total seats, moves to WAITLIST
        res2 = AdmissionService.approve_application(app2, user=self.admin)
        self.assertEqual(res2.status, ApplicationStatus.WAITLIST)

        # Third application exceeds waitlist threshold -> raises BusinessConflictError
        with self.assertRaises(BusinessConflictError):
            AdmissionService.approve_application(app3, user=self.admin)

    def test_reject_application_requires_reason(self):
        app = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Reject',
            last_name='Candidate',
            date_of_birth=date(2020, 1, 1),
            gender='M',
            father_name='Father',
            mother_name='Mother',
            guardian_phone='9800000004',
            address='Address'
        )

        with self.assertRaises(ValidationError):
            AdmissionService.reject_application(app, user=self.admin, reason='')

        rejected = AdmissionService.reject_application(app, user=self.admin, reason='Eligibility criteria not met')
        self.assertEqual(rejected.status, ApplicationStatus.REJECTED)
        self.assertEqual(rejected.rejection_reason, 'Eligibility criteria not met')

    def test_document_verification_and_auto_status(self):
        app = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Doc',
            last_name='Candidate',
            date_of_birth=date(2020, 1, 1),
            gender='M',
            father_name='Father',
            mother_name='Mother',
            guardian_phone='9800000005',
            address='Address'
        )
        doc = ApplicationDocument.objects.create(
            application=app,
            doc_type='Birth Certificate',
            document_file='admission_documents/sample.pdf'
        )

        verified_doc = AdmissionService.verify_document(
            document_id=doc.pk,
            verification_status=DocumentStatus.VERIFIED,
            notes='Original verified',
            user=self.admin
        )
        self.assertEqual(verified_doc.verification_status, DocumentStatus.VERIFIED)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.DOCUMENTS_VERIFIED)

    def test_record_evaluation(self):
        app = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Eval',
            last_name='Candidate',
            date_of_birth=date(2020, 1, 1),
            gender='M',
            father_name='Father',
            mother_name='Mother',
            guardian_phone='9800000006',
            address='Address'
        )
        updated = AdmissionService.record_evaluation(
            application_id=app.pk,
            test_score=88.5,
            interview_remarks='Excellent interaction',
            status=ApplicationStatus.TEST_SCHEDULED,
            user=self.admin
        )
        self.assertEqual(float(updated.test_score), 88.5)
        self.assertEqual(updated.interview_remarks, 'Excellent interaction')
        self.assertEqual(updated.status, ApplicationStatus.TEST_SCHEDULED)

    def test_convert_to_student_workflow_and_deduplication(self):
        existing_guardian = Guardian.objects.create(
            first_name='Rajesh',
            last_name='Sharma',
            relationship='Father',
            phone='9811009988',
            email='rajesh@test.com'
        )
        existing_student = Student.objects.create(
            admission_number='ADM-2025-0001',
            admission_date=date(2025, 4, 1),
            first_name='Older',
            last_name='Sibling',
            date_of_birth=date(2018, 2, 2),
            gender='M',
            primary_guardian=existing_guardian,
            address='123 Park Street'
        )

        app = AdmissionService.create_application(
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Younger',
            last_name='Sibling',
            date_of_birth=date(2020, 4, 15),
            gender='F',
            father_name='Rajesh Sharma',
            mother_name='Sunita Sharma',
            guardian_phone='9811009988',
            address='123 Park Street'
        )

        # Cannot convert unapproved application
        with self.assertRaises(ValidationError):
            AdmissionService.convert_to_student(app.pk, section_name='A', user=self.admin)

        # Approve application
        AdmissionService.approve_application(app, user=self.admin)

        # Convert to student
        new_student = AdmissionService.convert_to_student(
            application_id=app.pk,
            section_name='A',
            roll_number=15,
            user=self.admin
        )

        self.assertIsNotNone(new_student)
        self.assertTrue(new_student.admission_number.startswith('ADM-2026-'))
        # Guardian deduplication check
        self.assertEqual(new_student.primary_guardian.pk, existing_guardian.pk)
        self.assertEqual(Guardian.objects.filter(phone='9811009988').count(), 1)

        # Sibling linking check
        self.assertTrue(new_student.siblings.filter(pk=existing_student.pk).exists())
        self.assertTrue(existing_student.siblings.filter(pk=new_student.pk).exists())

        # Enrollment check
        enrollment = ClassEnrollment.objects.get(student=new_student, academic_year=self.year)
        self.assertEqual(enrollment.class_name, 'Class 1')
        self.assertEqual(enrollment.section_name, 'A')
        self.assertEqual(enrollment.roll_number, 15)

        # Application state check
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.ADMITTED)
        self.assertEqual(app.converted_student_id, new_student.admission_number)


class AdmissionsAPIRBACTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.school_class = SchoolClass.objects.create(name='Class 1', code='CLS-1', order=1)
        self.section = Section.objects.create(school_class=self.school_class, name='A')

        self.superuser = User.objects.create_superuser(
            username='adm_super',
            email='super@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )

        self.user_none = User.objects.create_user(
            username='user_none',
            email='none@test.com',
            password='Password123!',
            user_type=UserType.STAFF
        )

        self.user_viewer = User.objects.create_user(
            username='user_viewer',
            email='viewer@test.com',
            password='Password123!',
            user_type=UserType.STAFF
        )
        role_viewer = Role.objects.create(name='Admission Viewer', code='ADM_VIEWER')
        perm_view, _ = Permission.objects.get_or_create(
            codename='admissions.application.view',
            defaults={
                'name': 'View Admission Application',
                'module': ModuleCode.ADMISSIONS,
                'action': ActionCode.VIEW
            }
        )
        role_viewer.permissions.add(perm_view)
        UserRoleAssignment.objects.create(
            user=self.user_viewer,
            role=role_viewer,
            scope_type=ScopeType.GLOBAL
        )

        self.user_approver = User.objects.create_user(
            username='user_approver',
            email='approver@test.com',
            password='Password123!',
            user_type=UserType.STAFF
        )
        role_approver = Role.objects.create(name='Admission Approver', code='ADM_APPROVER')
        perm_approve, _ = Permission.objects.get_or_create(
            codename='admissions.application.approve',
            defaults={
                'name': 'Approve Admission Application',
                'module': ModuleCode.ADMISSIONS,
                'action': ActionCode.APPROVE
            }
        )
        role_approver.permissions.add(perm_view, perm_approve)
        UserRoleAssignment.objects.create(
            user=self.user_approver,
            role=role_approver,
            scope_type=ScopeType.GLOBAL
        )

        self.app = AdmissionApplication.objects.create(
            application_number='APP-2026-TEST',
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Kavya',
            last_name='Nair',
            date_of_birth=date(2020, 3, 10),
            gender='F',
            father_name='Ramesh Nair',
            guardian_phone='9811334455',
            address='Kochi',
            status=ApplicationStatus.SUBMITTED
        )

    def test_api_list_applications_permission(self):
        response = self.client.get('/api/v1/admissions/applications/')
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

        self.client.force_authenticate(user=self.user_none)
        response = self.client.get('/api/v1/admissions/applications/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.user_viewer)
        response = self.client.get('/api/v1/admissions/applications/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.superuser)
        response = self.client.get('/api/v1/admissions/applications/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_api_approve_and_reject_permission(self):
        url_approve = f'/api/v1/admissions/applications/{self.app.pk}/approve/'

        self.client.force_authenticate(user=self.user_viewer)
        resp = self.client.post(url_approve, {'remarks': 'OK'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.user_approver)
        resp = self.client.post(url_approve, {'remarks': 'Selected'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, ApplicationStatus.APPROVED)

    def test_api_convert_to_student(self):
        self.app.status = ApplicationStatus.APPROVED
        self.app.save()

        url_convert = f'/api/v1/admissions/applications/{self.app.pk}/convert-to-student/'
        self.client.force_authenticate(user=self.user_approver)
        resp = self.client.post(url_convert, {
            'section_name': 'A',
            'roll_number': 5
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('admission_number', resp.data)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, ApplicationStatus.ADMITTED)

    def test_api_seat_quota_crud_and_permission(self):
        url = '/api/v1/admissions/seat-quotas/'

        self.client.force_authenticate(user=self.user_viewer)
        resp = self.client.post(url, {
            'academic_year': self.year.pk,
            'class_name': 'Class 1',
            'total_seats': 50,
            'waitlist_limit': 10
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(url, {
            'academic_year': self.year.pk,
            'class_name': 'Class 1',
            'total_seats': 50,
            'waitlist_limit': 10
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['total_seats'], 50)
        self.assertEqual(resp.data['available_seats'], 50)


class AdmissionsWebViewsTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME='localhost')
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.school_class = SchoolClass.objects.create(name='Class 1', code='CLS-1', order=1)
        self.section = Section.objects.create(school_class=self.school_class, name='A')

        self.superuser = User.objects.create_superuser(
            username='adm_web_admin',
            email='adm_web@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.app = AdmissionApplication.objects.create(
            application_number='APP-2026-WEB01',
            academic_year=self.year,
            applying_class='Class 1',
            first_name='Diya',
            last_name='Kapoor',
            date_of_birth=date(2020, 6, 20),
            gender='F',
            father_name='Sunil Kapoor',
            guardian_phone='9811776655',
            address='Delhi',
            status=ApplicationStatus.SUBMITTED
        )

    def test_applications_list_view_get_and_post(self):
        self.client.force_login(self.superuser)

        resp = self.client.get('/dashboard/admissions/applications/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Admissions & Applications')
        self.assertContains(resp, 'APP-2026-WEB01')

        resp = self.client.post('/dashboard/admissions/applications/', {
            'action': 'create_application',
            'first_name': 'Aanya',
            'last_name': 'Singh',
            'applying_class': 'Class 1',
            'date_of_birth': '2020-04-12',
            'gender': 'F',
            'father_name': 'Vikram Singh',
            'guardian_phone': '9876543210',
            'address': 'Noida'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(AdmissionApplication.objects.filter(first_name='Aanya', last_name='Singh').exists())

    def test_application_detail_and_workflow_actions(self):
        self.client.force_login(self.superuser)

        resp = self.client.get(f'/dashboard/admissions/applications/{self.app.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Diya Kapoor')

        # Evaluation
        resp = self.client.post(f'/dashboard/admissions/applications/{self.app.pk}/', {
            'action': 'record_evaluation',
            'test_score': '92.5',
            'interview_remarks': 'High aptitude'
        })
        self.assertEqual(resp.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(float(self.app.test_score), 92.5)

        # Approve
        resp = self.client.post(f'/dashboard/admissions/applications/{self.app.pk}/', {
            'action': 'approve_application',
            'remarks': 'Approved by Admissions Committee'
        })
        self.assertEqual(resp.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, ApplicationStatus.APPROVED)

        # Convert to student
        resp = self.client.post(f'/dashboard/admissions/applications/{self.app.pk}/', {
            'action': 'convert_to_student',
            'section_name': 'A',
            'roll_number': '12'
        })
        self.assertEqual(resp.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, ApplicationStatus.ADMITTED)
        self.assertIsNotNone(self.app.converted_student_id)

    def test_seat_quotas_view_get_save_and_delete(self):
        self.client.force_login(self.superuser)

        resp = self.client.get(f'/dashboard/admissions/quotas/?year={self.year.pk}')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Seat Quotas & Waitlist Capacity')

        resp = self.client.post(f'/dashboard/admissions/quotas/?year={self.year.pk}', {
            'action': 'save_quota',
            'class_name': 'Class 1',
            'total_seats': '45',
            'waitlist_limit': '15'
        })
        self.assertEqual(resp.status_code, 302)
        quota = SeatQuota.objects.get(academic_year=self.year, class_name='Class 1')
        self.assertEqual(quota.total_seats, 45)
        self.assertEqual(quota.waitlist_limit, 15)

        resp = self.client.post(f'/dashboard/admissions/quotas/?year={self.year.pk}', {
            'action': 'delete_quota',
            'quota_id': quota.pk
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(SeatQuota.objects.filter(pk=quota.pk).exists())
