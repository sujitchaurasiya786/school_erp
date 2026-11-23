from datetime import date, timedelta
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.academics.models import SchoolClass, Section
from apps.identity.models import User, UserType
from apps.rbac.models import (
    ActionCode, ModuleCode, Permission, Role, ScopeType, UserRoleAssignment
)
from apps.school_config.models import AcademicYear, SchoolProfile
from apps.students.models import (
    BonafideCertificate, ClassEnrollment, Guardian, Student,
    StudentDocument, StudentStatus, TransferCertificate
)
from apps.students.services import StudentService
from common.exceptions import BusinessConflictError, SchoolERPException


class StudentServiceTests(TestCase):
    def setUp(self):
        call_command('sync_permissions')
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.next_year = AcademicYear.objects.create(
            name='2027-2028',
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31),
            is_current=False
        )
        self.admin = User.objects.create_superuser(
            username='admin_serv_student',
            email='admin_serv_s@test.com',
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
            admission_number='ADM-2026-0001',
            first_name='Aarav',
            last_name='Kumar',
            date_of_birth=date(2015, 5, 10),
            gender='M',
            address='123 Green Park',
            primary_guardian=self.guardian,
            admission_date=date(2026, 4, 1),
            status=StudentStatus.ACTIVE
        )

    def test_create_student_profile(self):
        student2 = StudentService.create_student_profile(
            admission_number='ADM-2026-0002',
            first_name='Ananya',
            last_name='Kumar',
            date_of_birth=date(2018, 8, 15),
            gender='F',
            primary_guardian=self.guardian,
            address='123 Green Park',
            admission_date=date(2026, 4, 1),
            academic_year=self.year,
            class_name='Class 1',
            section_name='A',
            roll_number=1,
            user=self.admin
        )
        self.assertEqual(student2.admission_number, 'ADM-2026-0002')
        self.assertEqual(student2.status, StudentStatus.ACTIVE)
        # Sibling auto-link verification (shared guardian phone)
        self.assertTrue(self.student.siblings.filter(pk=student2.pk).exists())
        self.assertTrue(student2.siblings.filter(pk=self.student.pk).exists())
        # Enrollment check
        enr = ClassEnrollment.objects.get(student=student2, academic_year=self.year)
        self.assertEqual(enr.class_name, 'Class 1')
        self.assertEqual(enr.section_name, 'A')
        self.assertEqual(enr.roll_number, 1)

    def test_create_student_duplicate_admission_number(self):
        with self.assertRaises(BusinessConflictError):
            StudentService.create_student_profile(
                admission_number='ADM-2026-0001',
                first_name='Duplicate',
                last_name='Test',
                date_of_birth=date(2016, 1, 1),
                gender='M',
                primary_guardian=self.guardian,
                address='Address'
            )

    def test_create_student_dob_validation(self):
        future_dob = date.today() + timedelta(days=10)
        with self.assertRaises(ValidationError):
            StudentService.create_student_profile(
                admission_number='ADM-2026-0003',
                first_name='Future',
                last_name='Baby',
                date_of_birth=future_dob,
                gender='M',
                primary_guardian=self.guardian,
                address='Address'
            )

    def test_tc_issuance_and_status_update(self):
        tc = StudentService.issue_transfer_certificate(
            student_id=self.student.id,
            reason='Relocating to another city',
            user=self.admin,
            dues_cleared=True
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.TC_ISSUED)
        self.assertTrue(tc.tc_number.startswith('TC-'))
        self.assertTrue(tc.dues_cleared)

        # Duplicate TC issuance should fail
        with self.assertRaises(SchoolERPException):
            StudentService.issue_transfer_certificate(
                student_id=self.student.id,
                reason='Second request',
                user=self.admin
            )

    def test_tc_issuance_requires_reason(self):
        with self.assertRaises(ValidationError):
            StudentService.issue_transfer_certificate(
                student_id=self.student.id,
                reason='',
                user=self.admin
            )

    def test_bonafide_issuance(self):
        bonafide = StudentService.issue_bonafide_certificate(
            student_id=self.student.id,
            academic_year=self.year,
            purpose='Passport Application',
            conduct_remarks='Exemplary',
            user=self.admin
        )
        self.assertTrue(bonafide.certificate_number.startswith('BON-'))
        self.assertEqual(bonafide.purpose, 'Passport Application')
        self.assertEqual(bonafide.conduct_remarks, 'Exemplary')

    def test_promote_students_bulk(self):
        promoted = StudentService.promote_students(
            target_academic_year=self.next_year,
            target_class='Class 2',
            target_section='B',
            student_ids=[self.student.id],
            user=self.admin,
            roll_numbers={self.student.id: 5}
        )
        self.assertEqual(len(promoted), 1)
        enr = ClassEnrollment.objects.get(student=self.student, academic_year=self.next_year)
        self.assertEqual(enr.class_name, 'Class 2')
        self.assertEqual(enr.section_name, 'B')
        self.assertEqual(enr.roll_number, 5)

    def test_promote_class_with_retention_and_detention(self):
        s2 = Student.objects.create(
            admission_number='ADM-2026-0004',
            first_name='Rohan',
            last_name='Das',
            date_of_birth=date(2015, 6, 12),
            gender='M',
            primary_guardian=self.guardian,
            address='Delhi',
            admission_date=date(2026, 4, 1),
            status=StudentStatus.ACTIVE
        )
        promotions = [
            {'student_id': self.student.id, 'action': 'PROMOTE', 'target_class': 'Class 2', 'target_section': 'A', 'roll_number': 1},
            {'student_id': s2.id, 'action': 'DETAIN', 'target_class': 'Class 1', 'target_section': 'A', 'roll_number': 2},
        ]
        results = StudentService.promote_class_with_retention(
            source_academic_year=self.year,
            target_academic_year=self.next_year,
            promotions=promotions,
            user=self.admin
        )
        self.assertEqual(results['promoted'], 1)
        self.assertEqual(results['detained'], 1)

        # Check enrollments
        enr1 = ClassEnrollment.objects.get(student=self.student, academic_year=self.next_year)
        self.assertEqual(enr1.class_name, 'Class 2')
        enr2 = ClassEnrollment.objects.get(student=s2, academic_year=self.next_year)
        self.assertEqual(enr2.class_name, 'Class 1')

    def test_link_and_unlink_siblings(self):
        s2 = Student.objects.create(
            admission_number='ADM-2026-0005',
            first_name='Kavya',
            last_name='Kumar',
            date_of_birth=date(2017, 3, 20),
            gender='F',
            primary_guardian=self.guardian,
            address='Delhi',
            admission_date=date(2026, 4, 1),
            status=StudentStatus.ACTIVE
        )
        StudentService.link_siblings(self.student.id, s2.id, user=self.admin)
        self.assertTrue(self.student.siblings.filter(pk=s2.pk).exists())
        self.assertTrue(s2.siblings.filter(pk=self.student.pk).exists())

        StudentService.unlink_siblings(self.student.id, s2.id, user=self.admin)
        self.assertFalse(self.student.siblings.filter(pk=s2.pk).exists())
        self.assertFalse(s2.siblings.filter(pk=self.student.pk).exists())

    def test_update_student_status(self):
        StudentService.update_student_status(
            student_id=self.student.id,
            new_status=StudentStatus.SUSPENDED,
            reason='Disciplinary review',
            user=self.admin
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.SUSPENDED)

    def test_add_student_document(self):
        fake_file = SimpleUploadedFile("birth_cert.pdf", b"file_content", content_type="application/pdf")
        doc = StudentService.add_student_document(
            student_id=self.student.id,
            doc_type='Birth Certificate',
            document_file=fake_file,
            title='Official Birth Cert',
            user=self.admin
        )
        self.assertEqual(doc.student.id, self.student.id)
        self.assertEqual(doc.doc_type, 'Birth Certificate')
        self.assertTrue(StudentDocument.objects.filter(pk=doc.pk).exists())


class StudentAPIRBACTests(TestCase):
    def setUp(self):
        call_command('sync_permissions')
        self.client = APIClient()
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.next_year = AcademicYear.objects.create(
            name='2027-2028',
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31),
            is_current=False
        )
        self.superuser = User.objects.create_superuser(
            username='stud_super',
            email='super_s@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.user_none = User.objects.create_user(
            username='stud_none',
            email='none_s@test.com',
            password='Password123!',
            user_type=UserType.STAFF
        )
        self.user_viewer = User.objects.create_user(
            username='stud_viewer',
            email='viewer_s@test.com',
            password='Password123!',
            user_type=UserType.STAFF
        )
        role_viewer = Role.objects.create(name='Student Viewer', code='STUD_VIEWER')
        perm_view = Permission.objects.get(codename='students.profile.view')
        role_viewer.permissions.add(perm_view)
        UserRoleAssignment.objects.create(user=self.user_viewer, role=role_viewer, scope_type=ScopeType.GLOBAL)

        self.user_tc_issuer = User.objects.create_user(
            username='stud_tc_issuer',
            email='tc_issuer_s@test.com',
            password='Password123!',
            user_type=UserType.STAFF
        )
        role_tc = Role.objects.create(name='TC Issuer', code='STUD_TC_ISSUER')
        perm_tc = Permission.objects.get(codename='students.tc.issue')
        role_tc.permissions.add(perm_view, perm_tc)
        UserRoleAssignment.objects.create(user=self.user_tc_issuer, role=role_tc, scope_type=ScopeType.GLOBAL)

        self.guardian = Guardian.objects.create(
            first_name='Sunil',
            last_name='Verma',
            relationship='Father',
            phone='9811223344',
            email='sunil@test.com'
        )
        self.student = Student.objects.create(
            admission_number='ADM-2026-API01',
            first_name='Rohan',
            last_name='Verma',
            date_of_birth=date(2015, 8, 20),
            gender='M',
            address='Delhi',
            primary_guardian=self.guardian,
            admission_date=date(2026, 4, 1),
            status=StudentStatus.ACTIVE
        )

    def test_student_list_permissions(self):
        url = '/api/v1/students/profiles/'

        # Unauthorized
        resp = self.client.get(url)
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

        # Normal user without permission -> 403
        self.client.force_authenticate(user=self.user_none)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Viewer -> 200
        self.client.force_authenticate(user=self.user_viewer)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(resp.data['data']), 1)

        # Superuser -> 200
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_issue_tc_permissions(self):
        url = f'/api/v1/students/profiles/{self.student.pk}/issue-tc/'

        # Viewer without TC permission -> 403
        self.client.force_authenticate(user=self.user_viewer)
        resp = self.client.post(url, {'reason': 'Transferring'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # TC Issuer -> 201
        self.client.force_authenticate(user=self.user_tc_issuer)
        resp = self.client.post(url, {'reason': 'Transferring to another branch'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('tc_number', resp.data)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.TC_ISSUED)

    def test_issue_bonafide_api(self):
        url = f'/api/v1/students/profiles/{self.student.pk}/issue-bonafide/'
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(url, {
            'academic_year_id': self.year.pk,
            'purpose': 'Bank Account Opening',
            'conduct_remarks': 'Good'
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('certificate_number', resp.data)

    def test_link_and_unlink_sibling_api(self):
        s2 = Student.objects.create(
            admission_number='ADM-2026-API02',
            first_name='Ananya',
            last_name='Verma',
            date_of_birth=date(2018, 2, 14),
            gender='F',
            address='Delhi',
            primary_guardian=self.guardian,
            admission_date=date(2026, 4, 1),
            status=StudentStatus.ACTIVE
        )
        self.client.force_authenticate(user=self.superuser)

        # Link
        url_link = f'/api/v1/students/profiles/{self.student.pk}/link-sibling/'
        resp = self.client.post(url_link, {'sibling_id': s2.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(self.student.siblings.filter(pk=s2.pk).exists())

        # Unlink
        url_unlink = f'/api/v1/students/profiles/{self.student.pk}/unlink-sibling/'
        resp = self.client.post(url_unlink, {'sibling_id': s2.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(self.student.siblings.filter(pk=s2.pk).exists())

    def test_class_enrollment_promote_api(self):
        url = '/api/v1/students/enrollments/promote/'
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(url, {
            'academic_year_id': self.next_year.pk,
            'class_name': 'Class 2',
            'section_name': 'A',
            'student_ids': [self.student.pk]
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['promoted_count'], 1)


class StudentWebViewsTests(TestCase):
    def setUp(self):
        call_command('sync_permissions')
        self.client = Client(SERVER_NAME='localhost')
        self.year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.school_class = SchoolClass.objects.create(name='Class 1', code='CLS-1', order=1)
        self.section = Section.objects.create(school_class=self.school_class, name='A')
        self.school = SchoolProfile.objects.create(school_name='Delhi Public School')

        self.superuser = User.objects.create_superuser(
            username='stud_web_admin',
            email='stud_web@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.guardian = Guardian.objects.create(
            first_name='Manoj',
            last_name='Tiwari',
            relationship='Father',
            phone='9811998877',
            email='manoj@test.com'
        )
        self.student = Student.objects.create(
            admission_number='ADM-2026-WEB01',
            first_name='Aryan',
            last_name='Tiwari',
            date_of_birth=date(2015, 4, 10),
            gender='M',
            address='Noida Sector 62',
            primary_guardian=self.guardian,
            admission_date=date(2026, 4, 1),
            status=StudentStatus.ACTIVE
        )
        self.enrollment = ClassEnrollment.objects.create(
            student=self.student,
            academic_year=self.year,
            class_name='Class 1',
            section_name='A',
            roll_number=10
        )

    def test_students_list_view(self):
        self.client.force_login(self.superuser)
        resp = self.client.get('/dashboard/students/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Student Directory')
        self.assertContains(resp, 'ADM-2026-WEB01')

    def test_student_detail_view_and_post_actions(self):
        self.client.force_login(self.superuser)

        # GET detail view
        resp = self.client.get(f'/dashboard/students/{self.student.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Aryan Tiwari')

        # POST Issue Bonafide
        resp = self.client.post(f'/dashboard/students/{self.student.pk}/', {
            'action': 'issue_bonafide',
            'academic_year_id': self.year.pk,
            'purpose': 'Passport Application',
            'conduct_remarks': 'Good'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(BonafideCertificate.objects.filter(student=self.student).exists())

        # POST Update Status
        resp = self.client.post(f'/dashboard/students/{self.student.pk}/', {
            'action': 'update_status',
            'status': 'SUSPENDED',
            'reason': 'Pending review'
        })
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.SUSPENDED)

        # POST Issue TC
        resp = self.client.post(f'/dashboard/students/{self.student.pk}/', {
            'action': 'issue_tc',
            'reason': 'Family relocation',
            'dues_cleared': 'on',
            'conduct_remarks': 'Good'
        })
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentStatus.TC_ISSUED)
        self.assertTrue(TransferCertificate.objects.filter(student=self.student).exists())

    def test_student_create_view_get_and_post(self):
        self.client.force_login(self.superuser)

        resp = self.client.get('/dashboard/students/create/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Register New Student')

        resp = self.client.post('/dashboard/students/create/', {
            'first_name': 'Ishaan',
            'last_name': 'Sharma',
            'date_of_birth': '2016-07-25',
            'gender': 'M',
            'address': 'Ghaziabad',
            'guardian_mode': 'new',
            'guardian_first_name': 'Alok',
            'guardian_last_name': 'Sharma',
            'guardian_relationship': 'Father',
            'guardian_phone': '9876501234',
            'academic_year_id': self.year.pk,
            'class_name': 'Class 1',
            'section_name': 'A',
            'roll_number': '15'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Student.objects.filter(first_name='Ishaan', last_name='Sharma').exists())

    def test_class_promotion_view_get_and_post(self):
        self.client.force_login(self.superuser)

        # GET
        url = f'/dashboard/students/promotion/?source_year={self.year.pk}&source_class=Class 1&source_section=A'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Year-End Class Promotion Console')
        self.assertContains(resp, 'Aryan Tiwari')

        # Next Year
        next_year = AcademicYear.objects.create(
            name='2027-2028',
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31)
        )

        # POST execute promotion
        resp = self.client.post(url, {
            'target_year_id': next_year.pk,
            'target_class': 'Class 2',
            'target_section': 'A',
            'selected_students': [self.student.pk],
            f'action_{self.student.pk}': 'PROMOTE',
            f'target_class_{self.student.pk}': 'Class 2',
            f'target_section_{self.student.pk}': 'A',
            f'roll_{self.student.pk}': '1'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ClassEnrollment.objects.filter(student=self.student, academic_year=next_year, class_name='Class 2').exists())

    def test_certificates_and_print_views(self):
        self.client.force_login(self.superuser)

        # 1. Certificates list
        resp = self.client.get('/dashboard/students/certificates/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Certificates & Document Issuance')

        # 2. TC Print view
        tc = StudentService.issue_transfer_certificate(
            student_id=self.student.pk,
            reason='Course completion',
            user=self.superuser
        )
        resp = self.client.get(f'/dashboard/students/tc/{tc.pk}/print/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'TRANSFER CERTIFICATE')
        self.assertContains(resp, tc.tc_number)

        # 3. Bonafide Print view
        bonafide = StudentService.issue_bonafide_certificate(
            student_id=self.student.pk,
            academic_year=self.year,
            purpose='Higher Education Admission',
            user=self.superuser
        )
        resp = self.client.get(f'/dashboard/students/bonafide/{bonafide.pk}/print/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'TO WHOMSOEVER IT MAY CONCERN')
        self.assertContains(resp, bonafide.certificate_number)

    def test_guardians_list_view(self):
        self.client.force_login(self.superuser)
        resp = self.client.get('/dashboard/students/guardians/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Guardians Directory')
        self.assertContains(resp, 'Manoj Tiwari')
