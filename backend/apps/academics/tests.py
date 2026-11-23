from datetime import date
import threading
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import OperationalError, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.academics.models import (
    AcademicStream, ClassSubject, Room, RoomType, SchoolClass, Section, Stream, Subject, SubjectType,
    TimetablePeriod, TimetableSlot
)
from apps.academics.services import (
    AcademicYearService, ClassSubjectService, RoomService, SchoolClassService, SectionService, StreamService, SubjectService,
    TeacherAssignmentService, TimetablePeriodService, TimetableService
)
from apps.audit.models import AuditAction, AuditLog
from apps.rbac.models import Permission, Role, ScopeType, UserRoleAssignment
from apps.rbac.registry import PERMISSION_REGISTRY
from apps.school_config.models import AcademicYear, SchoolProfile
from common.exceptions import BusinessConflictError, ResourceNotFoundError

from apps.identity.models import UserType
from rest_framework.test import APIClient

User = get_user_model()


class AcademicYearModelValidationTests(TestCase):
    """
    Unit tests for AcademicYear model validation and format rules.
    """

    def test_valid_academic_year_format(self):
        year = AcademicYear(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=False,
            is_active=True,
        )
        # Should validate without error
        year.full_clean()

    def test_invalid_name_patterns_rejected(self):
        invalid_names = [
            '2026/2027',
            '26-27',
            '2026 - 2027',
            '2026-2028',  # end != start + 1
            '2027-2026',  # reverse
            'abcd-efgh',
            '2026',
            '',
        ]
        for bad_name in invalid_names:
            year = AcademicYear(
                name=bad_name,
                start_date=date(2026, 4, 1),
                end_date=date(2027, 3, 31),
            )
            with self.assertRaises(ValidationError, msg=f"Should fail for {bad_name}"):
                year.full_clean()

    def test_end_date_before_start_date_rejected(self):
        year = AcademicYear(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2026, 3, 31),
        )
        with self.assertRaises(ValidationError) as ctx:
            year.full_clean()
        self.assertIn('end_date', ctx.exception.message_dict)

    def test_name_and_dates_year_alignment(self):
        # Name is 2026-2027, but start date is 2025
        year = AcademicYear(
            name='2026-2027',
            start_date=date(2025, 4, 1),
            end_date=date(2027, 3, 31),
        )
        with self.assertRaises(ValidationError) as ctx:
            year.full_clean()
        self.assertIn('name', ctx.exception.message_dict)

    def test_overlapping_date_ranges_rejected(self):
        AcademicYear.objects.create(
            name='2025-2026',
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )

        overlapping_year = AcademicYear(
            name='2025-2026',  # Overlap
            start_date=date(2025, 10, 1),
            end_date=date(2026, 9, 30),
        )
        with self.assertRaises(ValidationError):
            overlapping_year.full_clean()

    def test_direct_bulk_update_is_current_prohibited(self):
        """
        QuerySet.update(is_current=True) must be blocked to prevent bypassing singleton checks.
        """
        year1 = AcademicYear.objects.create(
            name='2024-2025',
            start_date=date(2024, 4, 1),
            end_date=date(2025, 3, 31),
            is_current=True,
        )
        year2 = AcademicYear.objects.create(
            name='2025-2026',
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
            is_current=False,
        )

        # 1. Bulk update with is_current=True is blocked
        with self.assertRaises(ValidationError):
            AcademicYear.objects.filter(pk=year2.pk).update(is_current=True)

        # 2. Direct model save() with is_current=True enforces single-current
        year2.is_current = True
        year2.save()

        year1.refresh_from_db()
        year2.refresh_from_db()
        self.assertFalse(year1.is_current)
        self.assertTrue(year2.is_current)
        self.assertTrue(year1.is_active)

    def test_lock_failure_in_save_is_not_silently_ignored(self):
        """
        Verify that when SchoolProfile.acquire_lock() fails during save(is_current=True),
        the error is raised and NOT silently ignored with broad except Exception: pass.
        """
        year = AcademicYear(
            name='2028-2029',
            start_date=date(2028, 4, 1),
            end_date=date(2029, 3, 31),
            is_current=True,
            is_active=True,
        )
        with patch('apps.school_config.models.SchoolProfile.acquire_lock', side_effect=OperationalError("Lock wait timeout exceeded")):
            with self.assertRaises(OperationalError):
                year.save()


class AcademicYearServiceTests(TestCase):
    """
    Focused tests for AcademicYearService business workflows and audit logging.
    """

    def setUp(self):
        call_command('sync_permissions')
        self.actor = User.objects.create_user(
            username='academic_admin',
            email='acad_admin@school.local',
            password='Password123!',
        )

    def test_create_academic_year_service(self):
        year = AcademicYearService.create_academic_year(
            name='2025-2026',
            start_date_val='2025-04-01',
            end_date_val='2026-03-31',
            is_current=True,
            is_active=True,
            actor=self.actor,
            reason='Initial session setup',
        )
        self.assertEqual(year.name, '2025-2026')
        self.assertTrue(year.is_current)
        self.assertTrue(year.is_active)

        # Verify audit log
        log = AuditLog.objects.filter(
            module='academics',
            entity_type='AcademicYear',
            entity_id=str(year.pk),
            action=AuditAction.CREATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.actor)
        self.assertEqual(log.changes['name'], '2025-2026')

    def test_update_academic_year_service(self):
        year = AcademicYearService.create_academic_year(
            name='2025-2026',
            start_date_val=date(2025, 4, 1),
            end_date_val=date(2026, 3, 31),
            is_current=False,
            actor=self.actor,
        )

        updated = AcademicYearService.update_academic_year(
            academic_year_id=year.pk,
            actor=self.actor,
            reason='Adjustment of end date',
            end_date=date(2026, 3, 30),
        )
        self.assertEqual(updated.end_date, date(2026, 3, 30))

        log = AuditLog.objects.filter(
            module='academics',
            entity_type='AcademicYear',
            entity_id=str(year.pk),
            action=AuditAction.UPDATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('end_date', log.changes)

    def test_activation_when_no_current_year_exists(self):
        year = AcademicYear.objects.create(
            name='2024-2025',
            start_date=date(2024, 4, 1),
            end_date=date(2025, 3, 31),
            is_current=False,
        )
        self.assertIsNone(AcademicYearService.get_current_year())

        activated = AcademicYearService.set_current_year(year.pk, actor=self.actor)
        self.assertTrue(activated.is_current)
        self.assertEqual(AcademicYearService.get_current_year().pk, year.pk)

    def test_set_current_year_switching_and_preserves_active_status(self):
        year1 = AcademicYearService.create_academic_year(
            name='2024-2025',
            start_date_val=date(2024, 4, 1),
            end_date_val=date(2025, 3, 31),
            is_current=True,
            is_active=True,
        )
        year2 = AcademicYearService.create_academic_year(
            name='2025-2026',
            start_date_val=date(2025, 4, 1),
            end_date_val=date(2026, 3, 31),
            is_current=False,
            is_active=True,
        )

        self.assertTrue(year1.is_current)
        self.assertFalse(year2.is_current)

        # Switch current year to year2
        AcademicYearService.set_current_year(
            academic_year_id=year2.pk,
            actor=self.actor,
            reason='Annual promotion rollover',
        )

        year1.refresh_from_db()
        year2.refresh_from_db()

        # Rule 5: Only one current year
        self.assertTrue(year2.is_current)
        self.assertFalse(year1.is_current)

        # Rule 7: Switching current year must NOT automatically deactivate previous year's is_active
        self.assertTrue(year1.is_active, "Previous academic year must remain is_active=True!")
        self.assertTrue(year2.is_active)

        # Verify audit log recorded the switch
        log = AuditLog.objects.filter(
            module='academics',
            entity_type='AcademicYear',
            entity_id=str(year2.pk),
            action=AuditAction.UPDATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('2024-2025', log.changes['previous_current_years'])

    def test_prevent_inactive_year_from_becoming_current(self):
        inactive_year = AcademicYear.objects.create(
            name='2023-2024',
            start_date=date(2023, 4, 1),
            end_date=date(2024, 3, 31),
            is_current=False,
            is_active=False,
        )

        with self.assertRaises(ValidationError):
            AcademicYearService.set_current_year(
                academic_year_id=inactive_year.pk,
                actor=self.actor,
            )

    def test_lock_failure_in_set_current_year_propagates(self):
        """
        Verify that when SchoolProfile.acquire_lock() fails during set_current_year,
        the error is raised cleanly.
        """
        year = AcademicYear.objects.create(
            name='2028-2029',
            start_date=date(2028, 4, 1),
            end_date=date(2029, 3, 31),
            is_current=False,
            is_active=True,
        )
        with patch('apps.school_config.models.SchoolProfile.acquire_lock', side_effect=OperationalError("Lock wait timeout exceeded")):
            with self.assertRaises(OperationalError):
                AcademicYearService.set_current_year(year.pk, actor=self.actor)

    def test_get_current_and_previous_year(self):
        year1 = AcademicYearService.create_academic_year(
            name='2024-2025',
            start_date_val=date(2024, 4, 1),
            end_date_val=date(2025, 3, 31),
            is_current=False,
        )
        year2 = AcademicYearService.create_academic_year(
            name='2025-2026',
            start_date_val=date(2025, 4, 1),
            end_date_val=date(2026, 3, 31),
            is_current=True,
        )

        curr = AcademicYearService.get_current_year()
        self.assertEqual(curr.pk, year2.pk)

        prev = AcademicYearService.get_previous_year()
        self.assertEqual(prev.pk, year1.pk)

        history = list(AcademicYearService.get_academic_year_history())
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].name, '2025-2026')
        self.assertEqual(history[1].name, '2024-2025')

    def test_dependent_record_deletion_protection(self):
        year = AcademicYearService.create_academic_year(
            name='2024-2025',
            start_date_val=date(2024, 4, 1),
            end_date_val=date(2025, 3, 31),
            is_current=False,
        )

        # Create dependent TimetableSlot
        school_class = SchoolClass.objects.create(name='Class 9', code='CL-09', order=9)
        section = Section.objects.create(school_class=school_class, name='A')
        subject = Subject.objects.create(name='Mathematics', code='MTH-09')
        class_sub = ClassSubject.objects.create(section=section, subject=subject)
        period = TimetablePeriod.objects.create(period_number=1, start_time='08:30:00', end_time='09:15:00')
        slot = TimetableSlot.objects.create(
            academic_year=year,
            day_of_week=1,
            period=period,
            class_subject=class_sub,
        )

        # Rule 10: Attempting to delete must fail with BusinessConflictError
        with self.assertRaises(BusinessConflictError) as ctx:
            AcademicYearService.delete_academic_year(
                academic_year_id=year.pk,
                actor=self.actor,
            )
        self.assertIn("timetable slot(s)", str(ctx.exception))

        # Remove the dependent slot
        slot.delete()

        # Deletion now succeeds
        AcademicYearService.delete_academic_year(
            academic_year_id=year.pk,
            actor=self.actor,
        )
        self.assertFalse(AcademicYear.objects.filter(pk=year.pk).exists())

        # Verify deletion audit log
        log = AuditLog.objects.filter(
            module='academics',
            entity_type='AcademicYear',
            entity_id=str(year.pk),
            action=AuditAction.DELETE,
        ).first()
        self.assertIsNotNone(log)


class AcademicYearConcurrencyTests(TransactionTestCase):
    """
    Real multi-threaded concurrency and transaction rollback tests against MySQL InnoDB.
    Verifies that concurrent activations serialize safely even when no current year exists,
    and failed transactions roll back completely without partial state.
    """

    def setUp(self):
        call_command('sync_permissions')
        self.actor = User.objects.create_user(
            username='concur_tester',
            email='concur@school.local',
            password='Password123!',
        )
        # Ensure singleton profile exists
        SchoolProfile.get_singleton()

        self.year_a = AcademicYear.objects.create(
            name='2024-2025',
            start_date=date(2024, 4, 1),
            end_date=date(2025, 3, 31),
            is_current=False,
            is_active=True,
        )
        self.year_b = AcademicYear.objects.create(
            name='2025-2026',
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
            is_current=False,
            is_active=True,
        )

    def test_concurrent_activation_when_no_current_year_exists(self):
        """
        Two concurrent threads race to activate different AcademicYears when
        no academic year is currently active.
        """
        self.assertEqual(AcademicYear.objects.filter(is_current=True).count(), 0)

        barrier = threading.Barrier(2)
        errors = []

        def worker(year_id):
            try:
                barrier.wait(timeout=5)
                AcademicYearService.set_current_year(year_id, actor=self.actor)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker, args=(self.year_a.pk,))
        t2 = threading.Thread(target=worker, args=(self.year_b.pk,))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Invariant check: Exactly ONE year can be current
        current_count = AcademicYear.objects.filter(is_current=True).count()
        self.assertEqual(current_count, 1, f"Expected exactly 1 current year, got {current_count}")

        # Both years must remain is_active=True
        self.year_a.refresh_from_db()
        self.year_b.refresh_from_db()
        self.assertTrue(self.year_a.is_active)
        self.assertTrue(self.year_b.is_active)

    def test_transaction_rollback_leaves_no_partial_changes(self):
        """
        Verifies that an exception during set_current_year (e.g. AuditService failure)
        triggers a full database rollback, leaving the previously current year active
        and target year non-current.
        """
        self.year_a.is_current = True
        self.year_a.save()

        # Simulate unexpected failure right during audit logging
        with patch('apps.audit.services.AuditService.log', side_effect=RuntimeError("Audit system failure")):
            with self.assertRaises(RuntimeError):
                AcademicYearService.set_current_year(self.year_b.pk, actor=self.actor)

        # Confirm atomic rollback: year_a is still current, year_b remains non-current
        self.year_a.refresh_from_db()
        self.year_b.refresh_from_db()
        self.assertTrue(self.year_a.is_current, "Previous current year must remain current after rollback")
        self.assertFalse(self.year_b.is_current, "Target year must remain non-current after rollback")

    def test_concurrent_update_and_set_current_year_deadlock_free(self):
        """
        Verify standardized lock acquisition order:
        Concurrent execution of update_academic_year(is_current=True) and
        set_current_year() serializes safely without deadlocks, leaving exactly
        one year marked as current.
        """
        barrier = threading.Barrier(2)
        errors = []

        def worker_set_current():
            try:
                barrier.wait(timeout=5)
                AcademicYearService.set_current_year(self.year_a.pk, actor=self.actor)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        def worker_update():
            try:
                barrier.wait(timeout=5)
                AcademicYearService.update_academic_year(
                    academic_year_id=self.year_b.pk,
                    actor=self.actor,
                    is_current=True,
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker_set_current)
        t2 = threading.Thread(target=worker_update)

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Neither thread should hit a deadlock
        self.assertEqual(errors, [], f"Encountered unexpected concurrency errors: {errors}")

        # Invariant check: Exactly ONE year can be current
        current_count = AcademicYear.objects.filter(is_current=True).count()
        self.assertEqual(current_count, 1, f"Expected exactly 1 current year, got {current_count}")

        # Both years must remain is_active=True
        self.year_a.refresh_from_db()
        self.year_b.refresh_from_db()
        self.assertTrue(self.year_a.is_active)
        self.assertTrue(self.year_b.is_active)


class AcademicYearRBACAndMigrationTests(TestCase):
    """
    Verifies permission registry and migration consistency.
    """

    def test_permission_codes_in_registry(self):
        registered_codenames = {p['codename'] for p in PERMISSION_REGISTRY}
        expected_codenames = {
            'academics.year.view',
            'academics.year.manage',
            'academics.year.activate',
            'academics.class.view',
            'academics.class.manage',
            'academics.section.view',
            'academics.section.manage',
            'academics.stream.view',
            'academics.stream.manage',
            'academics.subjects.view',
            'academics.subjects.manage',
        }
        self.assertTrue(
            expected_codenames.issubset(registered_codenames),
            f"Missing permissions in registry: {expected_codenames - registered_codenames}"
        )

    def test_sync_permissions_creates_active_records(self):
        call_command('sync_permissions')
        expected_codenames = [
            'academics.year.view',
            'academics.year.manage',
            'academics.year.activate',
            'academics.class.view',
            'academics.class.manage',
            'academics.section.view',
            'academics.section.manage',
            'academics.stream.view',
            'academics.stream.manage',
            'academics.subjects.view',
            'academics.subjects.manage',
        ]
        for codename in expected_codenames:
            perm = Permission.objects.filter(codename=codename, is_active=True).first()
            self.assertIsNotNone(perm, f"Permission {codename} not found in database!")
            self.assertEqual(perm.module, 'academics')

    def test_migration_consistency_dry_run(self):
        # Asserts no ungenerated migrations exist across school_config and academics
        from io import StringIO
        out = StringIO()
        call_command('makemigrations', 'school_config', check=True, dry_run=True, stdout=out)
        self.assertIn("No changes detected", out.getvalue())
        out_acad = StringIO()
        call_command('makemigrations', 'academics', check=True, dry_run=True, stdout=out_acad)
        self.assertIn("No changes detected", out_acad.getvalue())


class SchoolClassModelValidationTests(TestCase):
    """
    Tests for SchoolClass model validation and normalization rules.
    """

    def test_valid_class_creation_and_normalization(self):
        c = SchoolClass(name='  Class 10  ', code='  cl-10  ', order=10)
        c.full_clean()
        c.save()
        self.assertEqual(c.name, 'Class 10')
        self.assertEqual(c.code, 'CL-10')
        self.assertEqual(c.order, 10)
        self.assertTrue(c.is_active)

    def test_empty_name_or_code_rejected(self):
        with self.assertRaises(ValidationError):
            c = SchoolClass(name='', code='CL-01')
            c.full_clean()

        with self.assertRaises(ValidationError):
            c = SchoolClass(name='   ', code='CL-01')
            c.full_clean()

        with self.assertRaises(ValidationError):
            c = SchoolClass(name='Class 1', code='')
            c.full_clean()

        with self.assertRaises(ValidationError):
            c = SchoolClass(name='Class 1', code='   ')
            c.full_clean()

    def test_duplicate_name_case_insensitive_rejected(self):
        SchoolClass.objects.create(name='Class 9', code='CL-09', order=9)

        duplicate = SchoolClass(name='class 9', code='CL-09B', order=9)
        with self.assertRaises(ValidationError) as ctx:
            duplicate.full_clean()
        self.assertIn('name', ctx.exception.message_dict)

    def test_duplicate_code_case_insensitive_rejected(self):
        SchoolClass.objects.create(name='Class 9', code='CL-09', order=9)

        duplicate = SchoolClass(name='Class Nine', code='cl-09', order=9)
        with self.assertRaises(ValidationError) as ctx:
            duplicate.full_clean()
        self.assertIn('code', ctx.exception.message_dict)

    def test_invalid_order_rejected(self):
        with self.assertRaises(ValidationError):
            c = SchoolClass(name='Class 1', code='CL-01', order=0)
            c.full_clean()


class SchoolClassServiceTests(TestCase):
    """
    Tests for SchoolClassService business workflows, dependency protection, and audit logs.
    """

    def setUp(self):
        call_command('sync_permissions')
        self.actor = User.objects.create_user(
            username='academic_director',
            email='director@school.local',
            password='Password123!',
        )

    def test_create_class_service_with_audit(self):
        c = SchoolClassService.create_class(
            name='Class 11',
            code='cl-11',
            order=11,
            is_active=True,
            actor=self.actor,
            reason='Senior secondary expansion',
        )
        self.assertEqual(c.name, 'Class 11')
        self.assertEqual(c.code, 'CL-11')
        self.assertEqual(c.order, 11)
        self.assertTrue(c.is_active)

        # Audit log verification
        log = AuditLog.objects.filter(
            module='academics',
            entity_type='SchoolClass',
            entity_id=str(c.pk),
            action=AuditAction.CREATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.actor)
        self.assertEqual(log.changes['code'], 'CL-11')

    def test_update_class_service_with_audit(self):
        c = SchoolClassService.create_class(
            name='Class 1',
            code='CL-01',
            order=1,
            actor=self.actor,
        )

        updated = SchoolClassService.update_class(
            class_id=c.pk,
            actor=self.actor,
            name='Class 1 (Primary)',
            order=2,
            reason='Reordering grades',
        )
        self.assertEqual(updated.name, 'Class 1 (Primary)')
        self.assertEqual(updated.order, 2)

        # Audit log verification
        log = AuditLog.objects.filter(
            module='academics',
            entity_type='SchoolClass',
            entity_id=str(c.pk),
            action=AuditAction.UPDATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('name', log.changes)
        self.assertIn('order', log.changes)

    def test_get_and_list_classes(self):
        c1 = SchoolClassService.create_class(name='Class 2', code='CL-02', order=2, is_active=True)
        c2 = SchoolClassService.create_class(name='Class 1', code='CL-01', order=1, is_active=True)
        c3 = SchoolClassService.create_class(name='Class Inactive', code='CL-INACT', order=99, is_active=False)

        fetched = SchoolClassService.get_class(c1.pk)
        self.assertEqual(fetched.name, 'Class 2')

        all_classes = list(SchoolClassService.list_classes(active_only=False))
        self.assertEqual(len(all_classes), 3)
        self.assertEqual(all_classes[0].name, 'Class 1')
        self.assertEqual(all_classes[1].name, 'Class 2')

        active_classes = list(SchoolClassService.list_classes(active_only=True))
        self.assertEqual(len(active_classes), 2)
        self.assertNotIn(c3, active_classes)

    def test_delete_class_service_success_when_no_sections(self):
        c = SchoolClassService.create_class(name='Class Temp', code='CL-TMP', order=50, actor=self.actor)
        class_id = c.pk

        SchoolClassService.delete_class(class_id=class_id, actor=self.actor, reason='Deleted temporary grade')
        self.assertFalse(SchoolClass.objects.filter(pk=class_id).exists())

        log = AuditLog.objects.filter(
            module='academics',
            entity_type='SchoolClass',
            entity_id=str(class_id),
            action=AuditAction.DELETE,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.actor)

    def test_delete_class_service_blocked_when_sections_exist(self):
        c = SchoolClassService.create_class(name='Class 5', code='CL-05', order=5, actor=self.actor)
        # Create dependent section
        Section.objects.create(school_class=c, name='A')

        # Deletion must be blocked
        with self.assertRaises(BusinessConflictError) as ctx:
            SchoolClassService.delete_class(class_id=c.pk, actor=self.actor)
        self.assertIn("section(s) are associated", str(ctx.exception))
        self.assertTrue(SchoolClass.objects.filter(pk=c.pk).exists())


class AcademicsDashboardWebViewsTests(TestCase):
    """
    Tests for Academics dashboard web templates and UI workflows.
    """

    def setUp(self):
        call_command('sync_permissions')
        self.admin_user = User.objects.create_superuser(
            username='admin_web_tester',
            email='admin_web@school.local',
            password='Password123!',
        )
        self.client.force_login(self.admin_user)

    def test_academic_years_list_view_get(self):
        response = self.client.get(reverse('academics-years-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/academic_years_list.html')
        self.assertIn('Academic Sessions', response.content.decode('utf-8'))

    def test_academic_years_list_view_create_post(self):
        post_data = {
            'action': 'create',
            'name': '2027-2028',
            'start_date': '2027-04-01',
            'end_date': '2028-03-31',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-years-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AcademicYear.objects.filter(name='2027-2028').exists())

    def test_academic_year_activate_view_post(self):
        year = AcademicYearService.create_academic_year(
            name='2026-2027',
            start_date_val='2026-04-01',
            end_date_val='2027-03-31',
            is_current=False,
            is_active=True,
        )
        response = self.client.post(reverse('academics-year-activate', kwargs={'pk': year.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        year.refresh_from_db()
        self.assertTrue(year.is_current)

    def test_classes_list_view_get(self):
        response = self.client.get(reverse('academics-classes-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/classes_list.html')
        self.assertIn('Class Master Management', response.content.decode('utf-8'))

    def test_classes_list_view_create_post(self):
        post_data = {
            'action': 'create',
            'name': 'Class 12',
            'code': 'cl-12',
            'order': '12',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-classes-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SchoolClass.objects.filter(code='CL-12').exists())

    def test_classes_list_view_update_post(self):
        c = SchoolClassService.create_class(name='Class 6', code='CL-06', order=6)
        post_data = {
            'action': 'update',
            'class_id': c.pk,
            'name': 'Class 6 (Middle)',
            'code': 'CL-06M',
            'order': '7',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-classes-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.name, 'Class 6 (Middle)')
        self.assertEqual(c.code, 'CL-06M')
        self.assertEqual(c.order, 7)

    def test_classes_list_view_delete_post(self):
        c = SchoolClassService.create_class(name='Class 8', code='CL-08', order=8)
        post_data = {
            'action': 'delete',
            'class_id': c.pk,
            'reason': 'Testing web deletion',
        }
        response = self.client.post(reverse('academics-classes-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SchoolClass.objects.filter(pk=c.pk).exists())


class SectionModelValidationTests(TestCase):
    """
    Tests for Section model validation, normalization, and uniqueness invariants.
    """

    def setUp(self):
        self.school_class = SchoolClass.objects.create(name='Class 10', code='CL-10', order=10)

    def test_valid_section_creation_and_normalization(self):
        sec = Section(school_class=self.school_class, name='  a  ', capacity=45, room_number='Room 101')
        sec.full_clean()
        sec.save()
        self.assertEqual(sec.name, 'A')
        self.assertEqual(sec.capacity, 45)
        self.assertEqual(sec.room_number, 'Room 101')
        self.assertEqual(sec.stream, Stream.NONE)
        self.assertTrue(sec.is_active)

    def test_empty_section_name_rejected(self):
        with self.assertRaises(ValidationError):
            sec = Section(school_class=self.school_class, name='', capacity=40)
            sec.full_clean()

        with self.assertRaises(ValidationError):
            sec = Section(school_class=self.school_class, name='   ', capacity=40)
            sec.full_clean()

    def test_invalid_capacity_rejected(self):
        with self.assertRaises(ValidationError):
            sec = Section(school_class=self.school_class, name='A', capacity=0)
            sec.full_clean()

    def test_duplicate_section_within_class_and_stream_rejected(self):
        Section.objects.create(school_class=self.school_class, name='A', capacity=40)
        with self.assertRaises(ValidationError):
            duplicate = Section(school_class=self.school_class, name='a', capacity=35)
            duplicate.full_clean()

    def test_same_section_name_different_class_allowed(self):
        class_9 = SchoolClass.objects.create(name='Class 9', code='CL-09', order=9)
        sec_10 = Section.objects.create(school_class=self.school_class, name='A', capacity=40)
        sec_9 = Section(school_class=class_9, name='A', capacity=40)
        sec_9.full_clean()
        sec_9.save()
        self.assertEqual(sec_10.name, sec_9.name)
        self.assertNotEqual(sec_10.school_class, sec_9.school_class)

    def test_same_section_name_different_stream_allowed(self):
        class_11 = SchoolClass.objects.create(name='Class 11', code='CL-11', order=11)
        sec_sci = Section.objects.create(school_class=class_11, name='A', stream=Stream.SCIENCE, capacity=35)
        sec_comm = Section(school_class=class_11, name='A', stream=Stream.COMMERCE, capacity=35)
        sec_comm.full_clean()
        sec_comm.save()
        self.assertEqual(sec_sci.name, sec_comm.name)
        self.assertNotEqual(sec_sci.stream, sec_comm.stream)


class SectionServiceTests(TestCase):
    """
    Tests for SectionService CRUD methods, audit logging, and dependency protection.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='academic_admin',
            email='acad@school.local',
            password='Password123'
        )
        self.school_class = SchoolClass.objects.create(name='Class 10', code='CL-10', order=10)

    def test_create_section_with_audit_log(self):
        sec = SectionService.create_section(
            school_class_id=self.school_class.pk,
            name='  b  ',
            capacity=42,
            room_number='201',
            actor=self.user,
            reason='Added section B'
        )
        self.assertEqual(sec.name, 'B')
        self.assertEqual(sec.capacity, 42)
        self.assertEqual(sec.room_number, '201')
        self.assertTrue(sec.is_active)

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='Section',
            entity_id=str(sec.pk),
            action=AuditAction.CREATE
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.changes['name'], 'B')
        self.assertEqual(audit.changes['capacity'], 42)

    def test_update_section_with_audit_log(self):
        sec = SectionService.create_section(
            school_class_id=self.school_class.pk,
            name='A',
            capacity=40,
            actor=self.user
        )
        updated = SectionService.update_section(
            section_id=sec.pk,
            actor=self.user,
            capacity=45,
            room_number='Room 105',
            reason='Expanded capacity'
        )
        self.assertEqual(updated.capacity, 45)
        self.assertEqual(updated.room_number, 'Room 105')

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='Section',
            entity_id=str(sec.pk),
            action=AuditAction.UPDATE
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn('capacity', audit.changes)
        self.assertEqual(audit.changes['capacity']['old'], 40)
        self.assertEqual(audit.changes['capacity']['new'], 45)

    def test_get_section_and_list_sections(self):
        sec_a = SectionService.create_section(school_class_id=self.school_class.pk, name='A')
        sec_b = SectionService.create_section(school_class_id=self.school_class.pk, name='B', is_active=False)

        retrieved = SectionService.get_section(sec_a.pk)
        self.assertEqual(retrieved.pk, sec_a.pk)

        with self.assertRaises(ResourceNotFoundError):
            SectionService.get_section(99999)

        all_sections = SectionService.list_sections(school_class_id=self.school_class.pk)
        self.assertEqual(all_sections.count(), 2)

        active_sections = SectionService.list_sections(school_class_id=self.school_class.pk, active_only=True)
        self.assertEqual(active_sections.count(), 1)
        self.assertEqual(active_sections.first().pk, sec_a.pk)

    def test_delete_section_success_with_audit_log(self):
        sec = SectionService.create_section(
            school_class_id=self.school_class.pk,
            name='C',
            actor=self.user,
            reason='To be deleted'
        )
        sec_id = sec.pk
        SectionService.delete_section(
            section_id=sec_id,
            actor=self.user,
            reason='Deleting unused section'
        )
        self.assertFalse(Section.objects.filter(pk=sec_id).exists())

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='Section',
            entity_id=str(sec_id),
            action=AuditAction.DELETE
        ).first()
        self.assertIsNotNone(audit)

    def test_delete_section_prevented_when_class_subject_mapping_exists(self):
        sec = SectionService.create_section(
            school_class_id=self.school_class.pk,
            name='A',
            actor=self.user
        )
        subject = Subject.objects.create(name='Mathematics', code='MATH-10')
        ClassSubject.objects.create(section=sec, subject=subject)

        with self.assertRaises(BusinessConflictError) as ctx:
            SectionService.delete_section(section_id=sec.pk, actor=self.user)

        self.assertIn("subject mapping(s) are associated", str(ctx.exception))
        self.assertTrue(Section.objects.filter(pk=sec.pk).exists())


class SectionWebViewsTests(TestCase):
    """
    Tests for Section Dashboard web views.
    """

    def setUp(self):
        call_command('sync_permissions')
        self.user = User.objects.create_superuser(
            username='admin_section_tester',
            email='sectiontest@school.local',
            password='Password123!',
        )
        self.client.force_login(self.user)
        self.school_class = SchoolClass.objects.create(name='Class 10', code='CL-10', order=10)

    def test_sections_list_view_get(self):
        response = self.client.get(reverse('academics-sections-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/sections_list.html')
        self.assertIn('Section Management', response.content.decode('utf-8'))

    def test_sections_list_view_create_post(self):
        post_data = {
            'action': 'create',
            'school_class_id': self.school_class.pk,
            'name': 'a',
            'capacity': '38',
            'room_number': 'Room 102',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-sections-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Section.objects.filter(school_class=self.school_class, name='A', capacity=38).exists())

    def test_sections_list_view_update_post(self):
        sec = SectionService.create_section(
            school_class_id=self.school_class.pk,
            name='B',
            capacity=40
        )
        post_data = {
            'action': 'update',
            'section_id': sec.pk,
            'school_class_id': self.school_class.pk,
            'name': 'B',
            'capacity': '45',
            'room_number': 'Room 202',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-sections-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        sec.refresh_from_db()
        self.assertEqual(sec.capacity, 45)
        self.assertEqual(sec.room_number, 'Room 202')

    def test_sections_list_view_delete_post(self):
        sec = SectionService.create_section(
            school_class_id=self.school_class.pk,
            name='D',
            capacity=40
        )
        post_data = {
            'action': 'delete',
            'section_id': sec.pk,
            'reason': 'Removing extra section',
        }
        response = self.client.post(reverse('academics-sections-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Section.objects.filter(pk=sec.pk).exists())

    def test_sections_list_view_filter_by_class(self):
        class_9 = SchoolClass.objects.create(name='Class 9', code='CL-09', order=9)
        sec_10 = SectionService.create_section(school_class_id=self.school_class.pk, name='A')
        sec_9 = SectionService.create_section(school_class_id=class_9.pk, name='A')

        response = self.client.get(f"{reverse('academics-sections-list')}?class_id={self.school_class.pk}")
        self.assertEqual(response.status_code, 200)
        sections = list(response.context['sections'])
        self.assertIn(sec_10, sections)
        self.assertNotIn(sec_9, sections)


class StreamModelValidationTests(TestCase):
    """
    Tests for AcademicStream and senior-secondary stream validation rules.
    """

    def setUp(self):
        self.class_10 = SchoolClass.objects.create(name='Class 10', code='CL-10', order=10)
        self.class_11 = SchoolClass.objects.create(name='Class 11', code='CL-11', order=11)
        self.class_12 = SchoolClass.objects.create(name='Class 12', code='CL-12', order=12)

    def test_senior_secondary_detection(self):
        self.assertFalse(self.class_10.is_senior_secondary)
        self.assertTrue(self.class_11.is_senior_secondary)
        self.assertTrue(self.class_12.is_senior_secondary)

        nursery = SchoolClass.objects.create(name='Nursery', code='NUR', order=1)
        self.assertFalse(nursery.is_senior_secondary)

    def test_stream_creation_on_class_11_or_12_succeeds(self):
        st = AcademicStream(
            school_class=self.class_11,
            name='Science',
            code='sci',
            description='Physics, Chemistry, Maths/Biology'
        )
        st.full_clean()
        st.save()
        self.assertEqual(st.code, 'SCI')
        self.assertEqual(st.name, 'Science')
        self.assertTrue(st.is_active)

    def test_stream_creation_on_junior_class_fails(self):
        with self.assertRaises(ValidationError) as ctx:
            st = AcademicStream(
                school_class=self.class_10,
                name='Science',
                code='SCI'
            )
            st.full_clean()
        self.assertIn('senior-secondary classes', str(ctx.exception))

    def test_section_stream_validation(self):
        # Setting stream on Class 10 must fail
        with self.assertRaises(ValidationError) as ctx:
            sec_junior = Section(
                school_class=self.class_10,
                name='A',
                stream=Stream.SCIENCE,
                capacity=40
            )
            sec_junior.full_clean()
        self.assertIn('Streams are applicable only to Classes 11 and 12', str(ctx.exception))

        # Setting stream=NONE on Class 10 succeeds
        sec_valid_junior = Section(
            school_class=self.class_10,
            name='A',
            stream=Stream.NONE,
            capacity=40
        )
        sec_valid_junior.full_clean()
        sec_valid_junior.save()

        # Setting stream on Class 11 succeeds
        sec_senior = Section(
            school_class=self.class_11,
            name='A',
            stream=Stream.SCIENCE,
            capacity=40
        )
        sec_senior.full_clean()
        sec_senior.save()
        self.assertEqual(sec_senior.stream, Stream.SCIENCE)

        # Setting stream=NONE or empty on Class 11 must fail
        with self.assertRaises(ValidationError) as ctx:
            sec_senior_general = Section(
                school_class=self.class_11,
                name='B',
                stream=Stream.NONE,
                capacity=40
            )
            sec_senior_general.full_clean()
        self.assertIn('General', str(ctx.exception))

    def test_duplicate_stream_name_or_code_within_class_rejected(self):
        AcademicStream.objects.create(school_class=self.class_11, name='Commerce', code='COMM')

        with self.assertRaises(ValidationError):
            dup_name = AcademicStream(school_class=self.class_11, name='commerce', code='COMM2')
            dup_name.full_clean()

        with self.assertRaises(ValidationError):
            dup_code = AcademicStream(school_class=self.class_11, name='Commerce Advanced', code='comm')
            dup_code.full_clean()

    def test_same_stream_allowed_in_different_senior_classes(self):
        st_11 = AcademicStream.objects.create(school_class=self.class_11, name='Arts', code='ARTS')
        st_12 = AcademicStream(school_class=self.class_12, name='Arts', code='ARTS')
        st_12.full_clean()
        st_12.save()
        self.assertEqual(st_11.code, st_12.code)
        self.assertNotEqual(st_11.school_class, st_12.school_class)


class StreamServiceTests(TestCase):
    """
    Tests for StreamService CRUD methods, audit logging, and dependency protection.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='academic_stream_admin',
            email='stream_admin@school.local',
            password='Password123'
        )
        self.class_10 = SchoolClass.objects.create(name='Class 10', code='CL-10', order=10)
        self.class_11 = SchoolClass.objects.create(name='Class 11', code='CL-11', order=11)

    def test_create_stream_with_audit_log(self):
        st = StreamService.create_stream(
            school_class_id=self.class_11.pk,
            name='Science',
            code='  sci  ',
            description='Physics and Chemistry',
            actor=self.user,
            reason='Initial senior stream setup'
        )
        self.assertEqual(st.name, 'Science')
        self.assertEqual(st.code, 'SCI')
        self.assertTrue(st.is_active)

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='AcademicStream',
            entity_id=str(st.pk),
            action=AuditAction.CREATE
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.changes['code'], 'SCI')

    def test_create_stream_on_junior_class_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            StreamService.create_stream(
                school_class_id=self.class_10.pk,
                name='Science',
                code='SCI',
                actor=self.user
            )

    def test_update_stream_with_audit_log(self):
        st = StreamService.create_stream(
            school_class_id=self.class_11.pk,
            name='Commerce',
            code='COMM',
            actor=self.user
        )
        updated = StreamService.update_stream(
            stream_id=st.pk,
            actor=self.user,
            name='Commerce with Informatics',
            code='COMM-IP',
            reason='Updated curriculum naming'
        )
        self.assertEqual(updated.name, 'Commerce with Informatics')
        self.assertEqual(updated.code, 'COMM-IP')

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='AcademicStream',
            entity_id=str(st.pk),
            action=AuditAction.UPDATE
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn('code', audit.changes)
        self.assertEqual(audit.changes['code']['old'], 'COMM')
        self.assertEqual(audit.changes['code']['new'], 'COMM-IP')

    def test_get_stream_and_list_streams(self):
        st = StreamService.create_stream(school_class_id=self.class_11.pk, name='Arts', code='ARTS')
        retrieved = StreamService.get_stream(st.pk)
        self.assertEqual(retrieved.pk, st.pk)

        with self.assertRaises(ResourceNotFoundError):
            StreamService.get_stream(99999)

        all_streams = StreamService.list_streams(school_class_id=self.class_11.pk)
        self.assertEqual(all_streams.count(), 1)

    def test_delete_stream_success_with_audit_log(self):
        st = StreamService.create_stream(
            school_class_id=self.class_11.pk,
            name='Vocational',
            code='VOC',
            actor=self.user,
            reason='Temporary'
        )
        st_id = st.pk
        StreamService.delete_stream(stream_id=st_id, actor=self.user, reason='Deleted unused stream')
        self.assertFalse(AcademicStream.objects.filter(pk=st_id).exists())

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='AcademicStream',
            entity_id=str(st_id),
            action=AuditAction.DELETE
        ).first()
        self.assertIsNotNone(audit)

    def test_delete_stream_prevented_when_sections_assigned(self):
        st = StreamService.create_stream(
            school_class_id=self.class_11.pk,
            name='Science',
            code='SCIENCE',
            actor=self.user
        )
        # Create section referencing this stream
        Section.objects.create(
            school_class=self.class_11,
            name='A',
            stream='SCIENCE',
            capacity=35
        )

        with self.assertRaises(BusinessConflictError) as ctx:
            StreamService.delete_stream(stream_id=st.pk, actor=self.user)

        self.assertIn('section(s) in', str(ctx.exception))
        self.assertTrue(AcademicStream.objects.filter(pk=st.pk).exists())


class StreamWebViewsTests(TestCase):
    """
    Tests for Stream Dashboard web views.
    """

    def setUp(self):
        call_command('sync_permissions')
        self.user = User.objects.create_superuser(
            username='admin_stream_web',
            email='stream_web@school.local',
            password='Password123!',
        )
        self.client.force_login(self.user)
        self.class_11 = SchoolClass.objects.create(name='Class 11', code='CL-11', order=11)
        self.class_12 = SchoolClass.objects.create(name='Class 12', code='CL-12', order=12)

    def test_streams_list_view_get(self):
        response = self.client.get(reverse('academics-streams-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/streams_list.html')
        self.assertIn('Senior Secondary Streams', response.content.decode('utf-8'))

    def test_streams_list_view_create_post(self):
        post_data = {
            'action': 'create',
            'school_class_id': self.class_11.pk,
            'name': 'Science',
            'code': 'sci',
            'description': 'PCM/PCB',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-streams-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AcademicStream.objects.filter(school_class=self.class_11, code='SCI').exists())

    def test_streams_list_view_update_post(self):
        st = StreamService.create_stream(
            school_class_id=self.class_11.pk,
            name='Commerce',
            code='COMM'
        )
        post_data = {
            'action': 'update',
            'stream_id': st.pk,
            'school_class_id': self.class_11.pk,
            'name': 'Commerce (Standard)',
            'code': 'COMM',
            'description': 'Accountancy and Economics',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-streams-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        st.refresh_from_db()
        self.assertEqual(st.name, 'Commerce (Standard)')
        self.assertEqual(st.description, 'Accountancy and Economics')

    def test_streams_list_view_delete_post(self):
        st = StreamService.create_stream(
            school_class_id=self.class_11.pk,
            name='Arts',
            code='ARTS'
        )
        post_data = {
            'action': 'delete',
            'stream_id': st.pk,
            'reason': 'Removing unused stream',
        }
        response = self.client.post(reverse('academics-streams-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AcademicStream.objects.filter(pk=st.pk).exists())

    def test_streams_list_view_filter_by_class(self):
        st_11 = StreamService.create_stream(school_class_id=self.class_11.pk, name='Science 11', code='SCI11')
        st_12 = StreamService.create_stream(school_class_id=self.class_12.pk, name='Science 12', code='SCI12')

        response = self.client.get(f"{reverse('academics-streams-list')}?class_id={self.class_11.pk}")
        self.assertEqual(response.status_code, 200)
        streams = list(response.context['streams'])
        self.assertIn(st_11, streams)
        self.assertNotIn(st_12, streams)


class SubjectModelValidationTests(TestCase):
    """
    Tests for Subject model validation, code uppercase normalization, and component rules.
    """

    def test_valid_subject_creation_and_normalization(self):
        sub = Subject(
            name='  Mathematics  ',
            code='  math-10  ',
            subject_type=SubjectType.COMPULSORY,
            is_theory=True,
            is_practical=False
        )
        sub.full_clean()
        sub.save()
        self.assertEqual(sub.name, 'Mathematics')
        self.assertEqual(sub.code, 'MATH-10')
        self.assertEqual(sub.subject_type, SubjectType.COMPULSORY)
        self.assertTrue(sub.is_theory)
        self.assertFalse(sub.is_practical)
        self.assertTrue(sub.is_active)

    def test_empty_name_or_code_rejected(self):
        with self.assertRaises(ValidationError):
            s = Subject(name='', code='ENG-01')
            s.full_clean()

        with self.assertRaises(ValidationError):
            s = Subject(name='English', code='')
            s.full_clean()

    def test_neither_theory_nor_practical_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            s = Subject(name='Physical Education', code='PED-01', is_theory=False, is_practical=False)
            s.full_clean()
        self.assertIn('at least one component', str(ctx.exception))

    def test_practical_only_subject_allowed(self):
        s = Subject(name='Computer Lab Practicals', code='COMP-LAB', is_theory=False, is_practical=True)
        s.full_clean()
        s.save()
        self.assertFalse(s.is_theory)
        self.assertTrue(s.is_practical)

    def test_duplicate_code_case_insensitive_rejected(self):
        Subject.objects.create(name='Social Science', code='SOC-10')
        with self.assertRaises(ValidationError):
            dup = Subject(name='Social Studies', code='soc-10')
            dup.full_clean()


class SubjectServiceTests(TestCase):
    """
    Tests for SubjectService CRUD methods, audit logging, and dependency protection.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='academic_sub_admin',
            email='sub_admin@school.local',
            password='Password123'
        )

    def test_create_subject_with_audit_log(self):
        sub = SubjectService.create_subject(
            name='Physics',
            code='  phy-11  ',
            subject_type=SubjectType.ELECTIVE,
            is_theory=True,
            is_practical=True,
            description='Physics core',
            actor=self.user,
            reason='Initial subject setup'
        )
        self.assertEqual(sub.name, 'Physics')
        self.assertEqual(sub.code, 'PHY-11')
        self.assertTrue(sub.is_theory)
        self.assertTrue(sub.is_practical)

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='Subject',
            entity_id=str(sub.pk),
            action=AuditAction.CREATE
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.changes['code'], 'PHY-11')

    def test_update_subject_with_audit_log(self):
        sub = SubjectService.create_subject(name='Chemistry', code='CHEM-11', actor=self.user)
        updated = SubjectService.update_subject(
            subject_id=sub.pk,
            actor=self.user,
            name='Advanced Chemistry',
            is_practical=True,
            reason='Added practical lab'
        )
        self.assertEqual(updated.name, 'Advanced Chemistry')
        self.assertTrue(updated.is_practical)

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='Subject',
            entity_id=str(sub.pk),
            action=AuditAction.UPDATE
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn('is_practical', audit.changes)

    def test_get_subject_and_list_subjects(self):
        sub_comp = SubjectService.create_subject(name='English', code='ENG-01', subject_type=SubjectType.COMPULSORY)
        sub_opt = SubjectService.create_subject(name='Sanskrit', code='SKT-01', subject_type=SubjectType.OPTIONAL)

        retrieved = SubjectService.get_subject(sub_comp.pk)
        self.assertEqual(retrieved.pk, sub_comp.pk)

        with self.assertRaises(ResourceNotFoundError):
            SubjectService.get_subject(99999)

        comp_list = SubjectService.list_subjects(subject_type=SubjectType.COMPULSORY)
        self.assertEqual(comp_list.count(), 1)
        self.assertEqual(comp_list.first().pk, sub_comp.pk)

        search_list = SubjectService.list_subjects(search='sanskrit')
        self.assertEqual(search_list.count(), 1)
        self.assertEqual(search_list.first().pk, sub_opt.pk)

    def test_delete_subject_success_with_audit_log(self):
        sub = SubjectService.create_subject(name='German', code='GER-01', actor=self.user)
        sub_id = sub.pk
        SubjectService.delete_subject(subject_id=sub_id, actor=self.user, reason='Removed language')
        self.assertFalse(Subject.objects.filter(pk=sub_id).exists())

        audit = AuditLog.objects.filter(
            module='academics',
            entity_type='Subject',
            entity_id=str(sub_id),
            action=AuditAction.DELETE
        ).first()
        self.assertIsNotNone(audit)

    def test_delete_subject_prevented_when_mapped_to_sections(self):
        sub = SubjectService.create_subject(name='Biology', code='BIO-11', actor=self.user)
        school_class = SchoolClass.objects.create(name='Class 11', code='CL-11', order=11)
        sec = Section.objects.create(school_class=school_class, name='A', stream=Stream.SCIENCE, capacity=35)
        ClassSubject.objects.create(section=sec, subject=sub)

        with self.assertRaises(BusinessConflictError) as ctx:
            SubjectService.delete_subject(subject_id=sub.pk, actor=self.user)

        self.assertIn('class-section mapping(s) are associated', str(ctx.exception))
        self.assertTrue(Subject.objects.filter(pk=sub.pk).exists())


class SubjectWebViewsTests(TestCase):
    """
    Tests for Subject Dashboard web views.
    """

    def setUp(self):
        call_command('sync_permissions')
        self.user = User.objects.create_superuser(
            username='admin_sub_web',
            email='sub_web@school.local',
            password='Password123!',
        )
        self.client.force_login(self.user)

    def test_subjects_list_view_get(self):
        response = self.client.get(reverse('academics-subjects-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/subjects_list.html')
        self.assertIn('Subject Master Management', response.content.decode('utf-8'))

    def test_subjects_list_view_create_post(self):
        post_data = {
            'action': 'create',
            'name': 'Economics',
            'code': 'eco-11',
            'subject_type': 'ELECTIVE',
            'is_theory': 'on',
            'is_practical': '',
            'description': 'Micro and Macro economics',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-subjects-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Subject.objects.filter(code='ECO-11').exists())

    def test_subjects_list_view_update_post(self):
        sub = SubjectService.create_subject(name='Business Studies', code='BST-11')
        post_data = {
            'action': 'update',
            'subject_id': sub.pk,
            'name': 'Business Studies & Management',
            'code': 'BST-11',
            'subject_type': 'COMPULSORY',
            'is_theory': 'on',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-subjects-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.name, 'Business Studies & Management')

    def test_subjects_list_view_delete_post(self):
        sub = SubjectService.create_subject(name='French', code='FR-01')
        post_data = {
            'action': 'delete',
            'subject_id': sub.pk,
            'reason': 'Removing unused elective',
        }
        response = self.client.post(reverse('academics-subjects-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Subject.objects.filter(pk=sub.pk).exists())

    def test_subjects_list_view_filter_and_search(self):
        sub1 = SubjectService.create_subject(name='Hindi Core', code='HIN-01', subject_type=SubjectType.COMPULSORY)
        sub2 = SubjectService.create_subject(name='Hindi Elective', code='HIN-02', subject_type=SubjectType.ELECTIVE)

        # Filter by type
        response = self.client.get(f"{reverse('academics-subjects-list')}?type=COMPULSORY")
        self.assertEqual(response.status_code, 200)
        subs = list(response.context['subjects'])
        self.assertIn(sub1, subs)
        self.assertNotIn(sub2, subs)

        # Search query
        response = self.client.get(f"{reverse('academics-subjects-list')}?q=HIN-02")
        self.assertEqual(response.status_code, 200)
        subs = list(response.context['subjects'])
        self.assertNotIn(sub1, subs)
        self.assertIn(sub2, subs)


# ==============================================================================
# Phase 2 Step 5: Class–Subject Mapping Tests
# ==============================================================================

class ClassSubjectModelValidationTests(TestCase):
    """
    Unit tests for ClassSubject model validation rules.
    """

    def setUp(self):
        self.school_class = SchoolClass.objects.create(name='Class 9', code='CL-09', order=9)
        self.section = Section.objects.create(school_class=self.school_class, name='A')
        self.subject = Subject.objects.create(name='Mathematics', code='MATH-09', is_theory=True)

    def test_valid_class_subject_creation(self):
        mapping = ClassSubject(
            section=self.section,
            subject=self.subject,
            subject_type=SubjectType.COMPULSORY
        )
        mapping.full_clean()
        mapping.save()
        self.assertEqual(mapping.is_elective, False)
        self.assertIn('Class 9 - A', str(mapping))
        self.assertIn('Mathematics', str(mapping))

    def test_inactive_subject_rejected(self):
        self.subject.is_active = False
        self.subject.save()
        mapping = ClassSubject(section=self.section, subject=self.subject)
        with self.assertRaises(ValidationError) as ctx:
            mapping.full_clean()
        self.assertIn('subject', ctx.exception.message_dict)

    def test_duplicate_mapping_rejected(self):
        ClassSubject.objects.create(section=self.section, subject=self.subject)
        dup = ClassSubject(section=self.section, subject=self.subject)
        with self.assertRaises(ValidationError) as ctx:
            dup.full_clean()
        self.assertIn('subject', ctx.exception.message_dict)

    def test_is_elective_synced_with_subject_type(self):
        mapping = ClassSubject(
            section=self.section,
            subject=self.subject,
            subject_type=SubjectType.ELECTIVE
        )
        mapping.full_clean()
        mapping.save()
        self.assertTrue(mapping.is_elective)


class ClassSubjectServiceTests(TestCase):
    """
    Focused unit tests for ClassSubjectService business logic, bulk mapping, and audit trails.
    """

    def setUp(self):
        self.actor = User.objects.create_user(username='admin_sub_svc', email='admin_sub_svc@example.com', is_staff=True)
        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.actor)
        self.sec10_a = SectionService.create_section(school_class_id=self.c10.pk, name='A', actor=self.actor)
        self.sec10_b = SectionService.create_section(school_class_id=self.c10.pk, name='B', actor=self.actor)

        self.c11 = SchoolClassService.create_class(name='Class 11', code='CL-11', order=11, actor=self.actor)
        self.sec11_sci = SectionService.create_section(school_class_id=self.c11.pk, name='A', stream=Stream.SCIENCE, actor=self.actor)
        self.sec11_comm = SectionService.create_section(school_class_id=self.c11.pk, name='B', stream=Stream.COMMERCE, actor=self.actor)

        self.sub_eng = SubjectService.create_subject(name='English Core', code='ENG-10', actor=self.actor)
        self.sub_math = SubjectService.create_subject(name='Mathematics Standard', code='MATH-10', actor=self.actor)
        self.sub_phy = SubjectService.create_subject(name='Physics', code='PHY-11', is_theory=True, is_practical=True, actor=self.actor)
        self.sub_chem = SubjectService.create_subject(name='Chemistry', code='CHEM-11', is_theory=True, is_practical=True, actor=self.actor)

    def test_create_mapping_success_and_audit(self):
        m = ClassSubjectService.create_mapping(
            section_id=self.sec10_a.pk,
            subject_id=self.sub_eng.pk,
            subject_type=SubjectType.COMPULSORY,
            actor=self.actor,
            reason="Mapped English to 10-A"
        )
        self.assertEqual(m.section, self.sec10_a)
        self.assertEqual(m.subject, self.sub_eng)
        self.assertEqual(m.subject_type, SubjectType.COMPULSORY)
        self.assertFalse(m.is_elective)

        log = AuditLog.objects.filter(entity_type='ClassSubject', entity_id=str(m.pk), action=AuditAction.CREATE).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.actor)

    def test_create_mapping_duplicate_fails(self):
        ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_eng.pk, actor=self.actor)
        with self.assertRaises(ValidationError):
            ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_eng.pk, actor=self.actor)

    def test_create_mapping_inactive_subject_fails(self):
        inactive_sub = SubjectService.create_subject(name='Latin', code='LAT-01', is_active=False, actor=self.actor)
        with self.assertRaises(ValidationError):
            ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=inactive_sub.pk, actor=self.actor)

    def test_bulk_map_subjects_to_junior_class(self):
        res = ClassSubjectService.bulk_map_subjects_to_class(
            school_class_id=self.c10.pk,
            subject_ids=[self.sub_eng.pk, self.sub_math.pk],
            default_subject_type=SubjectType.COMPULSORY if hasattr(SubjectType, 'COMPULSORY') else None,
            actor=self.actor
        )
        # 2 subjects * 2 sections = 4 mappings
        self.assertEqual(res['created_count'], 4)
        self.assertEqual(res['skipped_count'], 0)
        self.assertEqual(ClassSubject.objects.filter(section__school_class=self.c10).count(), 4)

    def test_bulk_map_senior_class_with_stream(self):
        res = ClassSubjectService.bulk_map_subjects_to_class(
            school_class_id=self.c11.pk,
            subject_ids=[self.sub_phy.pk, self.sub_chem.pk],
            stream=Stream.SCIENCE,
            actor=self.actor
        )
        # Mapped to sec11_sci only, not sec11_comm
        self.assertEqual(res['created_count'], 2)
        self.assertEqual(ClassSubject.objects.filter(section=self.sec11_sci).count(), 2)
        self.assertEqual(ClassSubject.objects.filter(section=self.sec11_comm).count(), 0)

    def test_bulk_map_senior_class_rejects_general_stream(self):
        with self.assertRaises(ValidationError):
            ClassSubjectService.bulk_map_subjects_to_class(
                school_class_id=self.c11.pk,
                subject_ids=[self.sub_phy.pk],
                stream=Stream.NONE,
                actor=self.actor
            )

    def test_bulk_map_junior_class_rejects_non_general_stream(self):
        with self.assertRaises(ValidationError):
            ClassSubjectService.bulk_map_subjects_to_class(
                school_class_id=self.c10.pk,
                subject_ids=[self.sub_eng.pk],
                stream=Stream.SCIENCE,
                actor=self.actor
            )

    def test_bulk_map_skips_existing_mappings(self):
        # First map English to 10-A
        ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_eng.pk, actor=self.actor)

        # Now bulk map English and Math to Class 10 (sections A & B)
        res = ClassSubjectService.bulk_map_subjects_to_class(
            school_class_id=self.c10.pk,
            subject_ids=[self.sub_eng.pk, self.sub_math.pk],
            actor=self.actor
        )
        # Section A already has English (1 skipped), needs Math (1 created)
        # Section B needs English & Math (2 created)
        self.assertEqual(res['created_count'], 3)
        self.assertEqual(res['skipped_count'], 1)

    def test_update_mapping_and_audit(self):
        m = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_eng.pk, actor=self.actor)
        updated = ClassSubjectService.update_mapping(
            mapping_id=m.pk,
            subject_type=SubjectType.ELECTIVE,
            actor=self.actor,
            reason="Converted to elective"
        )
        self.assertEqual(updated.subject_type, SubjectType.ELECTIVE)
        self.assertTrue(updated.is_elective)

        log = AuditLog.objects.filter(entity_type='ClassSubject', entity_id=str(m.pk), action=AuditAction.UPDATE).first()
        self.assertIsNotNone(log)

    def test_delete_mapping_success_and_audit(self):
        m = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_eng.pk, actor=self.actor)
        m_pk = m.pk
        ClassSubjectService.delete_mapping(mapping_id=m_pk, actor=self.actor, reason="Removed subject")
        self.assertFalse(ClassSubject.objects.filter(pk=m_pk).exists())

        log = AuditLog.objects.filter(entity_type='ClassSubject', entity_id=str(m_pk), action=AuditAction.DELETE).first()
        self.assertIsNotNone(log)

    def test_delete_mapping_blocked_by_timetable_slots(self):
        from apps.school_config.models import AcademicYear
        ay = AcademicYear.objects.create(name='2026-2027', start_date=date(2026, 4, 1), end_date=date(2027, 3, 31))
        period = TimetablePeriod.objects.create(period_number=1, name='P1', start_time='08:30', end_time='09:15')
        m = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_eng.pk, actor=self.actor)

        # Create dependent timetable slot
        slot = TimetableSlot.objects.create(
            academic_year=ay,
            day_of_week=1,
            period=period,
            class_subject=m
        )
        with self.assertRaises(BusinessConflictError):
            ClassSubjectService.delete_mapping(mapping_id=m.pk, actor=self.actor)

        # Should still exist
        self.assertTrue(ClassSubject.objects.filter(pk=m.pk).exists())

    def test_copy_mappings_across_sections(self):
        ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_eng.pk, actor=self.actor)
        ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_math.pk, actor=self.actor)

        res = ClassSubjectService.copy_mappings_across_sections(
            source_section_id=self.sec10_a.pk,
            target_section_ids=[self.sec10_b.pk],
            actor=self.actor
        )
        self.assertEqual(res['copied_count'], 2)
        self.assertEqual(ClassSubject.objects.filter(section=self.sec10_b).count(), 2)


class ClassSubjectWebViewsTests(TestCase):
    """
    Integration tests for Class-Subject Mapping dashboard web views.
    """

    def setUp(self):
        call_command('sync_permissions', verbosity=0)
        self.superuser = User.objects.create_superuser(
            username='admin_cs_web',
            email='admin_cs_web@example.com',
            password='Password123!'
        )
        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.superuser)
        self.sec10_a = SectionService.create_section(school_class_id=self.c10.pk, name='A', actor=self.superuser)
        self.sub = SubjectService.create_subject(name='Computer Science', code='CS-01', actor=self.superuser)

        self.client.force_login(self.superuser)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('academics-class-subjects-list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_superuser_has_full_access(self):
        response = self.client.get(reverse('academics-class-subjects-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/class_subjects_list.html')
        self.assertTrue(response.context['can_manage'])

    def test_class_subjects_list_view_single_create_post(self):
        post_data = {
            'action': 'create',
            'section_id': self.sec10_a.pk,
            'subject_id': self.sub.pk,
            'subject_type': 'COMPULSORY',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-class-subjects-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ClassSubject.objects.filter(section=self.sec10_a, subject=self.sub).exists())

    def test_class_subjects_list_view_bulk_map_post(self):
        sub2 = SubjectService.create_subject(name='Economics', code='ECO-10', actor=self.superuser)
        post_data = {
            'action': 'bulk_map',
            'school_class_id': self.c10.pk,
            'subject_ids': [self.sub.pk, sub2.pk],
            'default_subject_type': 'OPTIONAL',
        }
        response = self.client.post(reverse('academics-class-subjects-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClassSubject.objects.filter(section=self.sec10_a).count(), 2)

    def test_class_subjects_list_view_update_post(self):
        m = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub.pk, actor=self.superuser)
        post_data = {
            'action': 'update',
            'mapping_id': m.pk,
            'subject_type': 'ELECTIVE',
            'is_active': 'on',
        }
        response = self.client.post(reverse('academics-class-subjects-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.subject_type, SubjectType.ELECTIVE)
        self.assertTrue(m.is_elective)

    def test_class_subjects_list_view_delete_post(self):
        m = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub.pk, actor=self.superuser)
        post_data = {
            'action': 'delete',
            'mapping_id': m.pk,
            'reason': 'Removing subject from section',
        }
        response = self.client.post(reverse('academics-class-subjects-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClassSubject.objects.filter(pk=m.pk).exists())

    def test_class_subjects_list_view_filtering(self):
        sub2 = SubjectService.create_subject(name='History', code='HIS-10', actor=self.superuser)
        m1 = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub.pk, subject_type=SubjectType.COMPULSORY, actor=self.superuser)
        m2 = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=sub2.pk, subject_type=SubjectType.ELECTIVE, actor=self.superuser)

        # Filter by subject_type
        response = self.client.get(f"{reverse('academics-class-subjects-list')}?subject_type=COMPULSORY")
        self.assertEqual(response.status_code, 200)
        mappings = list(response.context['mappings'])
        self.assertIn(m1, mappings)
        self.assertNotIn(m2, mappings)

        # Search query
        response = self.client.get(f"{reverse('academics-class-subjects-list')}?q=History")
        self.assertEqual(response.status_code, 200)
        mappings = list(response.context['mappings'])
        self.assertNotIn(m1, mappings)
        self.assertIn(m2, mappings)


# ==============================================================================
# Phase 3: Teacher Assignment Tests (Class Teachers & Subject Teachers)
# ==============================================================================

class TeacherAssignmentServiceTests(TestCase):
    """
    Focused unit tests for TeacherAssignmentService (Class Teachers & Subject Teachers).
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(username='admin_ta_svc', email='admin_ta@example.com', password='Password123!')
        self.teacher1 = User.objects.create_user(username='t_sharma', first_name='Rohan', last_name='Sharma', email='rohan@school.edu', is_staff=True, user_type=UserType.TEACHER)
        self.teacher2 = User.objects.create_user(username='t_verma', first_name='Priya', last_name='Verma', email='priya@school.edu', is_staff=True, user_type=UserType.TEACHER)
        self.student_user = User.objects.create_user(username='student_user', first_name='Arun', is_staff=False, user_type=UserType.STUDENT)

        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.admin)
        self.sec10_a = SectionService.create_section(school_class_id=self.c10.pk, name='A', actor=self.admin)
        self.sec10_b = SectionService.create_section(school_class_id=self.c10.pk, name='B', actor=self.admin)

        self.sub_math = SubjectService.create_subject(name='Mathematics Standard', code='MATH-10', actor=self.admin)
        self.sub_sci = SubjectService.create_subject(name='Science', code='SCI-10', actor=self.admin)

        self.mapping_math_a = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_math.pk, actor=self.admin)
        self.mapping_math_b = ClassSubjectService.create_mapping(section_id=self.sec10_b.pk, subject_id=self.sub_math.pk, actor=self.admin)
        self.mapping_sci_a = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_sci.pk, actor=self.admin)

    def test_assign_class_teacher_success_and_audit(self):
        sec = TeacherAssignmentService.assign_class_teacher(
            section_id=self.sec10_a.pk,
            teacher_id=self.teacher1.pk,
            actor=self.admin,
            reason="Appointed as Class Teacher for 10-A"
        )
        self.assertEqual(sec.class_teacher, self.teacher1)

        log = AuditLog.objects.filter(entity_type='Section', entity_id=str(sec.pk), action=AuditAction.UPDATE).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.admin)
        self.assertIn('class_teacher', log.changes)

    def test_assign_class_teacher_inactive_user_fails(self):
        self.teacher1.is_active = False
        self.teacher1.save()
        with self.assertRaises(ValidationError) as ctx:
            TeacherAssignmentService.assign_class_teacher(section_id=self.sec10_a.pk, teacher_id=self.teacher1.pk, actor=self.admin)
        self.assertIn('teacher', ctx.exception.message_dict)

    def test_assign_class_teacher_ineligible_user_fails(self):
        with self.assertRaises(ValidationError) as ctx:
            TeacherAssignmentService.assign_class_teacher(section_id=self.sec10_a.pk, teacher_id=self.student_user.pk, actor=self.admin)
        self.assertIn('teacher', ctx.exception.message_dict)

    def test_assign_scoped_non_staff_teacher_succeeds(self):
        """
        Verifies that a user created with user_type=TEACHER and is_staff=False
        (e.g., scoped to OWN_ONLY or SECTION from dashboard) is eligible faculty,
        appears in get_eligible_faculty_queryset(), can be assigned as Class Teacher
        and Subject Teacher, and appears in the workload summary.
        """
        scoped_teacher = User.objects.create_user(
            username='divya_test',
            email='divya_test@example.com',
            first_name='Divya',
            last_name='Singh',
            user_type=UserType.TEACHER,
            is_staff=False,
        )
        self.assertTrue(TeacherAssignmentService.is_eligible_faculty(scoped_teacher))
        faculty_qs = TeacherAssignmentService.get_eligible_faculty_queryset()
        self.assertIn(scoped_teacher, faculty_qs)

        # Assign as class teacher
        sec = TeacherAssignmentService.assign_class_teacher(
            section_id=self.sec10_a.pk,
            teacher_id=scoped_teacher.pk,
            actor=self.admin
        )
        self.assertEqual(sec.class_teacher, scoped_teacher)

        # Assign as subject teacher
        cs = TeacherAssignmentService.assign_subject_teacher(
            class_subject_id=self.mapping_math_a.pk,
            teacher_id=scoped_teacher.pk,
            actor=self.admin
        )
        self.assertEqual(cs.teacher, scoped_teacher)

        # Workload summary includes scoped_teacher
        workload = TeacherAssignmentService.get_faculty_workload_summary()
        teacher_workload = [w for w in workload if w['teacher'] == scoped_teacher]
        self.assertEqual(len(teacher_workload), 1)
        self.assertEqual(teacher_workload[0]['total_subjects'], 1)
        self.assertEqual(len(teacher_workload[0]['class_teacher_sections']), 1)

    def test_assign_class_teacher_collision_prevention(self):
        # Teacher 1 assigned to 10-A
        TeacherAssignmentService.assign_class_teacher(section_id=self.sec10_a.pk, teacher_id=self.teacher1.pk, actor=self.admin)

        # Assigning Teacher 1 to 10-B fails without force
        with self.assertRaises(ValidationError) as ctx:
            TeacherAssignmentService.assign_class_teacher(section_id=self.sec10_b.pk, teacher_id=self.teacher1.pk, actor=self.admin)
        self.assertIn('teacher', ctx.exception.message_dict)

        # With force=True, it succeeds
        sec_b = TeacherAssignmentService.assign_class_teacher(
            section_id=self.sec10_b.pk,
            teacher_id=self.teacher1.pk,
            actor=self.admin,
            force=True
        )
        self.assertEqual(sec_b.class_teacher, self.teacher1)

    def test_remove_class_teacher(self):
        TeacherAssignmentService.assign_class_teacher(section_id=self.sec10_a.pk, teacher_id=self.teacher1.pk, actor=self.admin)
        sec = TeacherAssignmentService.remove_class_teacher(section_id=self.sec10_a.pk, actor=self.admin)
        self.assertIsNone(sec.class_teacher)

    def test_assign_subject_teacher_success_and_audit(self):
        cs = TeacherAssignmentService.assign_subject_teacher(
            class_subject_id=self.mapping_math_a.pk,
            teacher_id=self.teacher1.pk,
            actor=self.admin,
            reason="Assigned Rohan Sharma to teach Math 10-A"
        )
        self.assertEqual(cs.teacher, self.teacher1)

        log = AuditLog.objects.filter(entity_type='ClassSubject', entity_id=str(cs.pk), action=AuditAction.UPDATE).first()
        self.assertIsNotNone(log)

    def test_bulk_assign_subject_teacher(self):
        # Assign teacher 2 to Math in both Section A and Section B
        count = TeacherAssignmentService.bulk_assign_subject_teacher(
            class_subject_ids=[self.mapping_math_a.pk, self.mapping_math_b.pk],
            teacher_id=self.teacher2.pk,
            actor=self.admin
        )
        self.assertEqual(count, 2)
        self.mapping_math_a.refresh_from_db()
        self.mapping_math_b.refresh_from_db()
        self.assertEqual(self.mapping_math_a.teacher, self.teacher2)
        self.assertEqual(self.mapping_math_b.teacher, self.teacher2)

    def test_remove_subject_teacher(self):
        TeacherAssignmentService.assign_subject_teacher(class_subject_id=self.mapping_math_a.pk, teacher_id=self.teacher1.pk, actor=self.admin)
        cs = TeacherAssignmentService.remove_subject_teacher(class_subject_id=self.mapping_math_a.pk, actor=self.admin)
        self.assertIsNone(cs.teacher)

    def test_get_faculty_workload_summary(self):
        # Assign teacher 1 as class teacher of 10-A and subject teacher of Math & Science
        TeacherAssignmentService.assign_class_teacher(section_id=self.sec10_a.pk, teacher_id=self.teacher1.pk, actor=self.admin)
        TeacherAssignmentService.assign_subject_teacher(class_subject_id=self.mapping_math_a.pk, teacher_id=self.teacher1.pk, actor=self.admin)
        TeacherAssignmentService.assign_subject_teacher(class_subject_id=self.mapping_sci_a.pk, teacher_id=self.teacher1.pk, actor=self.admin)

        summary = TeacherAssignmentService.get_faculty_workload_summary()
        t1_summary = next((w for w in summary if w['teacher'].pk == self.teacher1.pk), None)
        self.assertIsNotNone(t1_summary)
        self.assertEqual(len(t1_summary['class_teacher_sections']), 1)
        self.assertEqual(t1_summary['total_subjects'], 2)
        self.assertIn('Class 10', t1_summary['distinct_classes'])


class TeacherAssignmentWebViewsTests(TestCase):
    """
    Integration tests for Teacher Assignment dashboard web views.
    """

    def setUp(self):
        call_command('sync_permissions', verbosity=0)
        self.superuser = User.objects.create_superuser(
            username='admin_ta_web',
            email='admin_ta_web@example.com',
            password='Password123!'
        )
        self.teacher = User.objects.create_user(username='t_gupta', first_name='Amit', last_name='Gupta', is_staff=True)

        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.superuser)
        self.sec10_a = SectionService.create_section(school_class_id=self.c10.pk, name='A', actor=self.superuser)
        self.sub = SubjectService.create_subject(name='Computer Science', code='CS-10', actor=self.superuser)
        self.mapping = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub.pk, actor=self.superuser)

        self.client.force_login(self.superuser)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('academics-teacher-assignments-list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_superuser_has_full_access(self):
        response = self.client.get(reverse('academics-teacher-assignments-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/teacher_assignments.html')
        self.assertTrue(response.context['can_manage'])

    def test_assign_class_teacher_post(self):
        post_data = {
            'action': 'assign_class_teacher',
            'section_id': self.sec10_a.pk,
            'teacher_id': self.teacher.pk,
            'reason': 'Assigned Amit Gupta to 10-A',
        }
        response = self.client.post(reverse('academics-teacher-assignments-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.sec10_a.refresh_from_db()
        self.assertEqual(self.sec10_a.class_teacher, self.teacher)

    def test_remove_class_teacher_post(self):
        self.sec10_a.class_teacher = self.teacher
        self.sec10_a.save()
        post_data = {
            'action': 'remove_class_teacher',
            'section_id': self.sec10_a.pk,
        }
        response = self.client.post(reverse('academics-teacher-assignments-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.sec10_a.refresh_from_db()
        self.assertIsNone(self.sec10_a.class_teacher)

    def test_assign_subject_teacher_post(self):
        post_data = {
            'action': 'assign_subject_teacher',
            'class_subject_id': self.mapping.pk,
            'teacher_id': self.teacher.pk,
        }
        response = self.client.post(reverse('academics-teacher-assignments-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.teacher, self.teacher)

    def test_bulk_assign_subject_teacher_post(self):
        post_data = {
            'action': 'bulk_assign_subject_teacher',
            'class_subject_ids': [self.mapping.pk],
            'teacher_id': self.teacher.pk,
        }
        response = self.client.post(reverse('academics-teacher-assignments-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.teacher, self.teacher)

    def test_remove_subject_teacher_post(self):
        self.mapping.teacher = self.teacher
        self.mapping.save()
        post_data = {
            'action': 'remove_subject_teacher',
            'class_subject_id': self.mapping.pk,
        }
        response = self.client.post(reverse('academics-teacher-assignments-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.mapping.refresh_from_db()
        self.assertIsNone(self.mapping.teacher)

    def test_tabs_rendering(self):
        for tab_name in ('class_teachers', 'subject_teachers', 'workload'):
            response = self.client.get(f"{reverse('academics-teacher-assignments-list')}?tab={tab_name}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['active_tab'], tab_name)

    def test_scoped_teacher_appears_in_web_view_context(self):
        # Create non-staff teacher (e.g. scoped teacher created from dashboard)
        scoped_teacher = User.objects.create_user(
            username='divya_web',
            email='divya_web@school.edu',
            first_name='Divya',
            last_name='Singh',
            user_type=UserType.TEACHER,
            is_staff=False,
        )
        response = self.client.get(reverse('academics-teacher-assignments-list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(scoped_teacher, response.context['all_teachers'])
        # Also test workload tab includes scoped teacher
        response_workload = self.client.get(f"{reverse('academics-teacher-assignments-list')}?tab=workload")
        self.assertEqual(response_workload.status_code, 200)
        workload_teachers = [w['teacher'] for w in response_workload.context['faculty_workload_list']]
        self.assertIn(scoped_teacher, workload_teachers)


class TeacherAssignmentAPITests(TestCase):
    """
    Unit & Integration tests for Teacher Assignment DRF REST API endpoints (/api/v1/academics/...).
    """

    def setUp(self):
        call_command('sync_permissions', verbosity=0)
        self.superuser = User.objects.create_superuser(
            username='admin_ta_api',
            email='admin_ta_api@example.com',
            password='Password123!'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.superuser)

        self.teacher = User.objects.create_user(
            username='t_neha',
            first_name='Neha',
            last_name='Kapoor',
            email='neha@school.edu',
            user_type=UserType.TEACHER,
            is_staff=False
        )

        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.superuser)
        self.sec10_a = SectionService.create_section(school_class_id=self.c10.pk, name='A', actor=self.superuser)
        self.sub_sci = SubjectService.create_subject(name='Physics', code='PHY-10', actor=self.superuser)
        self.mapping = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_sci.pk, actor=self.superuser)

    def test_assign_class_teacher_api(self):
        url = f"/api/v1/academics/sections/{self.sec10_a.pk}/assign-class-teacher/"
        response = self.client.post(url, {'teacher_id': self.teacher.pk})
        self.assertEqual(response.status_code, 200)
        self.sec10_a.refresh_from_db()
        self.assertEqual(self.sec10_a.class_teacher, self.teacher)

    def test_remove_class_teacher_api(self):
        self.sec10_a.class_teacher = self.teacher
        self.sec10_a.save()
        url = f"/api/v1/academics/sections/{self.sec10_a.pk}/remove-class-teacher/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.sec10_a.refresh_from_db()
        self.assertIsNone(self.sec10_a.class_teacher)

    def test_assign_subject_teacher_api(self):
        url = f"/api/v1/academics/class-subjects/{self.mapping.pk}/assign-teacher/"
        response = self.client.post(url, {'teacher_id': self.teacher.pk})
        self.assertEqual(response.status_code, 200)
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.teacher, self.teacher)

    def test_remove_subject_teacher_api(self):
        self.mapping.teacher = self.teacher
        self.mapping.save()
        url = f"/api/v1/academics/class-subjects/{self.mapping.pk}/remove-teacher/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.mapping.refresh_from_db()
        self.assertIsNone(self.mapping.teacher)

    def test_bulk_assign_subject_teacher_api(self):
        url = "/api/v1/academics/class-subjects/bulk-assign-teacher/"
        response = self.client.post(url, {
            'class_subjects': [self.mapping.pk],
            'teacher_id': self.teacher.pk,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['assigned_count'], 1)
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.teacher, self.teacher)

    def test_faculty_workload_api(self):
        url = "/api/v1/academics/class-subjects/faculty-workload/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        teacher_entry = [w for w in response.data if w['teacher_id'] == self.teacher.pk]
        self.assertEqual(len(teacher_entry), 1)

    def test_eligible_faculty_api(self):
        url = "/api/v1/academics/class-subjects/eligible-faculty/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        faculty_ids = [f['id'] for f in response.data]
        self.assertIn(self.teacher.pk, faculty_ids)


# ==============================================================================
# Phase 4: Timetable & Scheduling Tests
# ==============================================================================

class TimetablePeriodServiceTests(TestCase):
    """
    Unit tests for TimetablePeriodService (Bell schedule, time validation, overlap detection).
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin_period_svc',
            email='admin_p_svc@example.com',
            password='Password123!'
        )

    def test_create_period_success_and_audit(self):
        p = TimetablePeriodService.create_period(
            period_number=1,
            name="Period 1",
            start_time="08:30:00",
            end_time="09:15:00",
            is_break=False,
            actor=self.admin,
            reason="Morning initial period"
        )
        self.assertEqual(p.period_number, 1)
        self.assertEqual(p.name, "Period 1")
        self.assertFalse(p.is_break)

        log = AuditLog.objects.filter(entity_type='TimetablePeriod', entity_id=str(p.pk), action=AuditAction.CREATE).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.admin)

    def test_create_period_end_before_start_fails(self):
        with self.assertRaises(ValidationError) as ctx:
            TimetablePeriodService.create_period(
                period_number=1,
                name="Invalid Period",
                start_time="10:00:00",
                end_time="09:00:00",
                actor=self.admin
            )
        self.assertIn('end_time', ctx.exception.message_dict)

    def test_create_period_time_overlap_fails(self):
        TimetablePeriodService.create_period(
            period_number=1,
            name="Period 1",
            start_time="08:30:00",
            end_time="09:15:00",
            actor=self.admin
        )
        # Overlapping period (09:00 to 09:45 overlaps with 08:30 to 09:15)
        with self.assertRaises(ValidationError) as ctx:
            TimetablePeriodService.create_period(
                period_number=2,
                name="Period 2",
                start_time="09:00:00",
                end_time="09:45:00",
                actor=self.admin
            )
        self.assertIn('start_time', ctx.exception.message_dict)

    def test_create_period_duplicate_number_fails(self):
        TimetablePeriodService.create_period(
            period_number=1,
            name="Period 1",
            start_time="08:30:00",
            end_time="09:15:00",
            actor=self.admin
        )
        with self.assertRaises(ValidationError) as ctx:
            TimetablePeriodService.create_period(
                period_number=1,
                name="Period 1 Duplicate",
                start_time="09:20:00",
                end_time="10:05:00",
                actor=self.admin
            )
        self.assertIn('period_number', ctx.exception.message_dict)

    def test_update_period_success_and_audit(self):
        p = TimetablePeriodService.create_period(
            period_number=1,
            name="Period 1",
            start_time="08:30:00",
            end_time="09:15:00",
            actor=self.admin
        )
        updated = TimetablePeriodService.update_period(
            period_id=p.pk,
            name="Assembly / Period 1",
            is_break=True,
            actor=self.admin,
            reason="Combined session"
        )
        self.assertEqual(updated.name, "Assembly / Period 1")
        self.assertTrue(updated.is_break)

        log = AuditLog.objects.filter(entity_type='TimetablePeriod', entity_id=str(p.pk), action=AuditAction.UPDATE).first()
        self.assertIsNotNone(log)

    def test_bulk_create_cbse_standard_periods(self):
        periods = TimetablePeriodService.bulk_create_cbse_standard_periods(actor=self.admin)
        self.assertEqual(len(periods), 11)
        # Should fail if called again
        with self.assertRaises(BusinessConflictError):
            TimetablePeriodService.bulk_create_cbse_standard_periods(actor=self.admin)


class TimetableServiceTests(TestCase):
    """
    Unit tests for TimetableService & Collision Detection Engine (Section, Teacher, Room clashes).
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin_tt_svc',
            email='admin_tt_svc@example.com',
            password='Password123!'
        )
        self.academic_year = AcademicYearService.create_academic_year(
            name="2026-2027",
            start_date_val="2026-04-01",
            end_date_val="2027-03-31",
            is_current=True,
            is_active=True,
            actor=self.admin
        )

        # Teachers
        self.teacher_math = User.objects.create_user(
            username='t_math', email='t_math@school.edu', first_name='Ramanujan', user_type=UserType.TEACHER, is_staff=True
        )
        self.teacher_sci = User.objects.create_user(
            username='t_sci', email='t_sci@school.edu', first_name='Curie', user_type=UserType.TEACHER, is_staff=True
        )

        # Classes & Sections
        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.admin)
        self.sec10_a = SectionService.create_section(school_class_id=self.c10.pk, name='A', room_number='Room 101', actor=self.admin)
        self.sec10_b = SectionService.create_section(school_class_id=self.c10.pk, name='B', room_number='Room 102', actor=self.admin)

        # Subjects & Mappings
        self.sub_math = SubjectService.create_subject(name='Mathematics', code='M-10', actor=self.admin)
        self.sub_sci = SubjectService.create_subject(name='Science', code='S-10', actor=self.admin)

        self.map_math_a = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_math.pk, actor=self.admin)
        self.map_sci_a = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_sci.pk, actor=self.admin)
        self.map_math_b = ClassSubjectService.create_mapping(section_id=self.sec10_b.pk, subject_id=self.sub_math.pk, actor=self.admin)

        TeacherAssignmentService.assign_subject_teacher(self.map_math_a.pk, self.teacher_math.pk, actor=self.admin)
        TeacherAssignmentService.assign_subject_teacher(self.map_math_b.pk, self.teacher_math.pk, actor=self.admin)
        TeacherAssignmentService.assign_subject_teacher(self.map_sci_a.pk, self.teacher_sci.pk, actor=self.admin)

        # Periods
        self.period_1 = TimetablePeriodService.create_period(period_number=1, name="Period 1", start_time="08:30", end_time="09:15", is_break=False, actor=self.admin)
        self.period_2 = TimetablePeriodService.create_period(period_number=2, name="Period 2", start_time="09:15", end_time="10:00", is_break=False, actor=self.admin)
        self.break_period = TimetablePeriodService.create_period(period_number=3, name="Recess", start_time="10:00", end_time="10:30", is_break=True, actor=self.admin)

    def test_create_slot_success_and_audit(self):
        slot = TimetableService.create_slot(
            day_of_week=1,  # Monday
            period_id=self.period_1.pk,
            class_subject_id=self.map_math_a.pk,
            room="Room 101",
            actor=self.admin
        )
        self.assertEqual(slot.day_of_week, 1)
        self.assertEqual(slot.period, self.period_1)
        self.assertEqual(slot.class_subject, self.map_math_a)
        self.assertEqual(slot.room, "Room 101")

        log = AuditLog.objects.filter(entity_type='TimetableSlot', entity_id=str(slot.pk), action=AuditAction.CREATE).first()
        self.assertIsNotNone(log)

    def test_schedule_during_break_period_fails(self):
        with self.assertRaises(ValidationError) as ctx:
            TimetableService.create_slot(
                day_of_week=1,
                period_id=self.break_period.pk,
                class_subject_id=self.map_math_a.pk,
                actor=self.admin
            )
        self.assertIn('period', ctx.exception.message_dict)

    def test_section_collision_prevention(self):
        # 10-A has Math on Monday Period 1
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math_a.pk,
            actor=self.admin
        )
        # Attempting to also schedule Science for 10-A on Monday Period 1 must raise BusinessConflictError
        with self.assertRaises(BusinessConflictError) as ctx:
            TimetableService.create_slot(
                day_of_week=1,
                period_id=self.period_1.pk,
                class_subject_id=self.map_sci_a.pk,
                actor=self.admin
            )
        self.assertIn("already has", str(ctx.exception))

    def test_teacher_collision_prevention(self):
        # Teacher Ramanujan teaches 10-A on Monday Period 1
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math_a.pk,
            room="Room 101",
            actor=self.admin
        )
        # Attempting to assign Ramanujan to 10-B also on Monday Period 1 must fail
        with self.assertRaises(BusinessConflictError) as ctx:
            TimetableService.create_slot(
                day_of_week=1,
                period_id=self.period_1.pk,
                class_subject_id=self.map_math_b.pk,
                room="Room 102",
                actor=self.admin
            )
        self.assertIn("already assigned to teach", str(ctx.exception))

    def test_room_collision_prevention(self):
        # 10-A is in Room 101 on Monday Period 1
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_sci_a.pk,
            room="Room 101",
            actor=self.admin
        )
        # 10-B with different teacher also booking Room 101 at the same period must fail
        with self.assertRaises(BusinessConflictError) as ctx:
            TimetableService.create_slot(
                day_of_week=1,
                period_id=self.period_1.pk,
                class_subject_id=self.map_math_b.pk,
                room="Room 101",
                actor=self.admin
            )
        self.assertIn("already booked", str(ctx.exception))

    def test_update_slot_collision_exclusion(self):
        slot = TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math_a.pk,
            room="Room 101",
            actor=self.admin
        )
        updated = TimetableService.update_slot(
            slot_id=slot.pk,
            room="Lab 1",
            actor=self.admin
        )
        self.assertEqual(updated.room, "Lab 1")

    def test_delete_period_with_slots_fails(self):
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math_a.pk,
            actor=self.admin
        )
        with self.assertRaises(BusinessConflictError):
            TimetablePeriodService.delete_period(self.period_1.pk, actor=self.admin)

    def test_get_section_timetable_matrix(self):
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math_a.pk,
            actor=self.admin
        )
        matrix = TimetableService.get_section_timetable_matrix(self.sec10_a.pk)
        self.assertEqual(matrix['total_scheduled'], 1)
        self.assertIn('Mathematics', matrix['subject_counts'])
        self.assertEqual(matrix['subject_counts']['Mathematics'], 1)

    def test_get_teacher_timetable_matrix(self):
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math_a.pk,
            actor=self.admin
        )
        matrix = TimetableService.get_teacher_timetable_matrix(self.teacher_math.pk)
        self.assertEqual(matrix['total_scheduled'], 1)
        self.assertIn(str(self.sec10_a), matrix['sections_covered'])


class TimetableWebViewsTests(TestCase):
    """
    Unit & Integration tests for Timetable dashboard web views and modal workflows.
    """

    def setUp(self):
        call_command('sync_permissions', verbosity=0)
        self.superuser = User.objects.create_superuser(
            username='admin_tt_web',
            email='admin_tt_web@example.com',
            password='Password123!'
        )
        self.academic_year = AcademicYearService.create_academic_year(
            name="2026-2027",
            start_date_val="2026-04-01",
            end_date_val="2027-03-31",
            is_current=True,
            is_active=True,
            actor=self.superuser
        )

        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.superuser)
        self.sec = SectionService.create_section(school_class_id=self.c10.pk, name='A', actor=self.superuser)
        self.sub = SubjectService.create_subject(name='English', code='ENG-10', actor=self.superuser)
        self.map = ClassSubjectService.create_mapping(section_id=self.sec.pk, subject_id=self.sub.pk, actor=self.superuser)

        self.period = TimetablePeriodService.create_period(
            period_number=1, name="Period 1", start_time="08:30", end_time="09:15", is_break=False, actor=self.superuser
        )

        self.client.force_login(self.superuser)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('academics-timetable-list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_superuser_has_full_access(self):
        response = self.client.get(reverse('academics-timetable-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/academics/timetable.html')
        self.assertTrue(response.context['can_manage'])

    def test_create_slot_post_action(self):
        post_data = {
            'action': 'create_slot',
            'day_of_week': 1,
            'period_id': self.period.pk,
            'class_subject_id': self.map.pk,
            'room': 'Room 101',
            'section_id': self.sec.pk,
        }
        response = self.client.post(reverse('academics-timetable-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(TimetableSlot.objects.filter(class_subject=self.map, day_of_week=1).exists())

    def test_delete_slot_post_action(self):
        slot = TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period.pk,
            class_subject_id=self.map.pk,
            actor=self.superuser
        )
        post_data = {
            'action': 'delete_slot',
            'slot_id': slot.pk,
            'section_id': self.sec.pk,
        }
        response = self.client.post(reverse('academics-timetable-list'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TimetableSlot.objects.filter(pk=slot.pk).exists())

    def test_tabs_rendering(self):
        for tab_name in ('section_matrix', 'teacher_matrix', 'periods'):
            response = self.client.get(f"{reverse('academics-timetable-list')}?tab={tab_name}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['active_tab'], tab_name)


class TimetableAPITests(TestCase):
    """
    Unit & Integration tests for Timetable DRF REST API endpoints (/api/v1/academics/periods/, /api/v1/academics/timetable-slots/).
    """

    def setUp(self):
        call_command('sync_permissions', verbosity=0)
        self.superuser = User.objects.create_superuser(
            username='admin_tt_api',
            email='admin_tt_api@example.com',
            password='Password123!'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.superuser)

        self.academic_year = AcademicYearService.create_academic_year(
            name="2026-2027",
            start_date_val="2026-04-01",
            end_date_val="2027-03-31",
            is_current=True,
            is_active=True,
            actor=self.superuser
        )

        self.teacher = User.objects.create_user(
            username='t_hindi', email='t_hindi@school.edu', first_name='Premchand', user_type=UserType.TEACHER, is_staff=True
        )

        self.c10 = SchoolClassService.create_class(name='Class 10', code='CL-10', order=10, actor=self.superuser)
        self.sec = SectionService.create_section(school_class_id=self.c10.pk, name='A', actor=self.superuser)
        self.sub = SubjectService.create_subject(name='Hindi', code='HIN-10', actor=self.superuser)
        self.map = ClassSubjectService.create_mapping(section_id=self.sec.pk, subject_id=self.sub.pk, actor=self.superuser)
        TeacherAssignmentService.assign_subject_teacher(self.map.pk, self.teacher.pk, actor=self.superuser)

        self.period = TimetablePeriodService.create_period(
            period_number=1, name="Period 1", start_time="08:30", end_time="09:15", is_break=False, actor=self.superuser
        )

    def test_period_viewset_list(self):
        url = "/api/v1/academics/periods/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_slot_viewset_create_and_conflict_check(self):
        url = "/api/v1/academics/timetable-slots/"
        post_data = {
            'academic_year': self.academic_year.pk,
            'day_of_week': 1,
            'period': self.period.pk,
            'class_subject': self.map.pk,
            'room': 'Room 101'
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 201)

        # Attempting duplicate slot on same section/day/period must fail with 400
        response_dup = self.client.post(url, post_data)
        self.assertEqual(response_dup.status_code, 400)

    def test_section_matrix_api(self):
        # Create a slot first
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period.pk,
            class_subject_id=self.map.pk,
            actor=self.superuser
        )
        url = f"/api/v1/academics/timetable-slots/section-matrix/?section_id={self.sec.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('grid', response.data)
        self.assertEqual(response.data['total_scheduled'], 1)

    def test_teacher_matrix_api(self):
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period.pk,
            class_subject_id=self.map.pk,
            actor=self.superuser
        )
        url = f"/api/v1/academics/timetable-slots/teacher-matrix/?teacher_id={self.teacher.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('grid', response.data)
        self.assertEqual(response.data['total_scheduled'], 1)


class RoomModelAndServiceTests(TestCase):
    def setUp(self):
        call_command('sync_permissions')
        self.admin = User.objects.create_superuser(
            username='admin_rooms',
            email='admin_rooms@school.edu',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.current_year = AcademicYearService.create_academic_year(
            name="2026-2027",
            start_date_val="2026-04-01",
            end_date_val="2027-03-31",
            is_current=True,
            is_active=True,
            actor=self.admin
        )
        self.c10 = SchoolClassService.create_class(name="Class 10", code="CL-10", order=10, actor=self.admin)
        self.sec10_a = SectionService.create_section(
            school_class_id=self.c10.pk,
            name="A",
            capacity=40,
            room_number="Room 101",
            actor=self.admin
        )
        self.sub_math = SubjectService.create_subject(name="Mathematics", code="MATH-10", actor=self.admin)
        self.map_math = ClassSubjectService.create_mapping(section_id=self.sec10_a.pk, subject_id=self.sub_math.pk, actor=self.admin)
        self.period_1 = TimetablePeriodService.create_period(period_number=1, name="Period 1", start_time="08:30", end_time="09:15", is_break=False, actor=self.admin)
        self.period_2 = TimetablePeriodService.create_period(period_number=2, name="Period 2", start_time="09:15", end_time="10:00", is_break=False, actor=self.admin)

    def test_create_room_success(self):
        room = RoomService.create_room(
            room_number="Room 101",
            room_type=RoomType.CLASSROOM,
            capacity=45,
            name="Primary Section 10-A Classroom",
            floor="Ground Floor",
            description="Equipped with smart board",
            is_active=True,
            actor=self.admin
        )
        self.assertEqual(room.room_number, "Room 101")
        self.assertEqual(room.capacity, 45)
        self.assertEqual(room.room_type, RoomType.CLASSROOM)
        self.assertTrue(room.is_active)

        log = AuditLog.objects.filter(entity_type='Room', entity_id=str(room.pk), action=AuditAction.CREATE).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes['room_number'], "Room 101")

    def test_duplicate_room_number_rejected_case_insensitive(self):
        RoomService.create_room(room_number="Lab 1", room_type=RoomType.SCIENCE_LAB, capacity=30, actor=self.admin)
        with self.assertRaises(BusinessConflictError):
            RoomService.create_room(room_number="lab 1", room_type=RoomType.SCIENCE_LAB, capacity=25, actor=self.admin)

    def test_invalid_capacity_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            RoomService.create_room(room_number="Room 99", capacity=0, actor=self.admin)
        self.assertIn('capacity', ctx.exception.message_dict)

    def test_update_room(self):
        room = RoomService.create_room(room_number="Room 201", capacity=35, actor=self.admin)
        updated = RoomService.update_room(
            room_id=room.pk,
            capacity=40,
            name="Updated Name",
            actor=self.admin
        )
        self.assertEqual(updated.capacity, 40)
        self.assertEqual(updated.name, "Updated Name")

        log = AuditLog.objects.filter(entity_type='Room', entity_id=str(room.pk), action=AuditAction.UPDATE).first()
        self.assertIsNotNone(log)
        self.assertIn('capacity', log.changes)

    def test_toggle_room_status(self):
        room = RoomService.create_room(room_number="Room 301", is_active=True, actor=self.admin)
        deactivated = RoomService.toggle_room_status(room_id=room.pk, is_active=False, actor=self.admin)
        self.assertFalse(deactivated.is_active)
        reactivated = RoomService.toggle_room_status(room_id=room.pk, is_active=True, actor=self.admin)
        self.assertTrue(reactivated.is_active)

    def test_inactive_room_cannot_be_scheduled(self):
        room = RoomService.create_room(room_number="Room 401", is_active=False, actor=self.admin)
        with self.assertRaises(ValidationError) as ctx:
            TimetableService.create_slot(
                day_of_week=1,
                period_id=self.period_1.pk,
                class_subject_id=self.map_math.pk,
                room_id=room.pk,
                actor=self.admin
            )
        self.assertIn('room', ctx.exception.message_dict)

    def test_delete_room_with_slots_rejected(self):
        room = RoomService.create_room(room_number="Room 501", is_active=True, actor=self.admin)
        slot = TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math.pk,
            room_id=room.pk,
            actor=self.admin
        )
        with self.assertRaises(BusinessConflictError):
            RoomService.delete_room(room_id=room.pk, actor=self.admin)

    def test_delete_room_with_section_rejected(self):
        room = RoomService.create_room(room_number="Room 101", is_active=True, actor=self.admin)
        with self.assertRaises(BusinessConflictError):
            RoomService.delete_room(room_id=room.pk, actor=self.admin)

    def test_delete_unreferenced_room_success(self):
        room = RoomService.create_room(room_number="Room 999", is_active=True, actor=self.admin)
        res = RoomService.delete_room(room_id=room.pk, actor=self.admin)
        self.assertTrue(res)
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())

    def test_room_collision_prevention_managed_and_text(self):
        room = RoomService.create_room(room_number="Room 101", is_active=True, actor=self.admin)
        TimetableService.create_slot(
            day_of_week=1,
            period_id=self.period_1.pk,
            class_subject_id=self.map_math.pk,
            room_id=room.pk,
            actor=self.admin
        )

        sec10_b = SectionService.create_section(school_class_id=self.c10.pk, name="B", actor=self.admin)
        sub_sci = SubjectService.create_subject(name="Science", code="SCI-10", actor=self.admin)
        map_sci = ClassSubjectService.create_mapping(section_id=sec10_b.pk, subject_id=sub_sci.pk, actor=self.admin)

        with self.assertRaises(BusinessConflictError):
            TimetableService.create_slot(
                day_of_week=1,
                period_id=self.period_1.pk,
                class_subject_id=map_sci.pk,
                room="room 101",
                actor=self.admin
            )


class RoomAPITests(TestCase):
    def setUp(self):
        call_command('sync_permissions')
        self.superuser = User.objects.create_superuser(
            username='api_super_rooms',
            email='api_super_rooms@school.edu',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.superuser)

    def test_create_room_response_structure_and_values(self):
        url = "/api/v1/academics/rooms/"
        post_data = {
            "room_number": "Auditorium-1",
            "name": "Main Auditorium",
            "room_type": "AUDITORIUM",
            "capacity": 250,
            "floor": "Floor 1",
            "description": "Main campus auditorium with AV facilities.",
            "is_active": True,
        }
        res_create = self.client.post(url, post_data)
        self.assertEqual(res_create.status_code, 201)

        # Verify response contains all required fields
        self.assertIn('id', res_create.data)
        self.assertIsNotNone(res_create.data['id'])
        self.assertEqual(res_create.data['room_number'], "Auditorium-1")
        self.assertEqual(res_create.data['name'], "Main Auditorium")
        self.assertEqual(res_create.data['room_type'], "AUDITORIUM")
        self.assertEqual(res_create.data['room_type_display'], "Auditorium / Hall")
        self.assertEqual(res_create.data['capacity'], 250)
        self.assertEqual(res_create.data['floor'], "Floor 1")
        self.assertEqual(res_create.data['description'], "Main campus auditorium with AV facilities.")
        self.assertTrue(res_create.data['is_active'])
        self.assertIn('created_at', res_create.data)
        self.assertIn('updated_at', res_create.data)
        self.assertIsNotNone(res_create.data['created_at'])
        self.assertIsNotNone(res_create.data['updated_at'])

        # Verify DB values match API response
        db_room = Room.objects.get(pk=res_create.data['id'])
        self.assertEqual(db_room.room_number, "Auditorium-1")
        self.assertEqual(db_room.capacity, 250)
        self.assertEqual(db_room.room_type, "AUDITORIUM")
        self.assertTrue(db_room.is_active)

    def test_update_room_put_response(self):
        room = RoomService.create_room(
            room_number="Room 101",
            room_type=RoomType.CLASSROOM,
            capacity=40,
            name="General Class 101",
            floor="Ground Floor",
            actor=self.superuser
        )
        url = f"/api/v1/academics/rooms/{room.pk}/"
        put_data = {
            "room_number": "Room 101-Renovated",
            "name": "Upgraded Class 101",
            "room_type": "SCIENCE_LAB",
            "capacity": 55,
            "floor": "Ground Floor West",
            "description": "Now converted to Science Lab",
            "is_active": True,
        }
        res_update = self.client.put(url, put_data)
        self.assertEqual(res_update.status_code, 200)

        # Verify response has updated values
        self.assertEqual(res_update.data['id'], room.pk)
        self.assertEqual(res_update.data['room_number'], "Room 101-Renovated")
        self.assertEqual(res_update.data['name'], "Upgraded Class 101")
        self.assertEqual(res_update.data['room_type'], "SCIENCE_LAB")
        self.assertEqual(res_update.data['room_type_display'], "Science Laboratory")
        self.assertEqual(res_update.data['capacity'], 55)
        self.assertEqual(res_update.data['floor'], "Ground Floor West")
        self.assertEqual(res_update.data['description'], "Now converted to Science Lab")
        self.assertIn('updated_at', res_update.data)

        # Verify database matches
        room.refresh_from_db()
        self.assertEqual(room.room_number, "Room 101-Renovated")
        self.assertEqual(room.name, "Upgraded Class 101")
        self.assertEqual(room.room_type, "SCIENCE_LAB")
        self.assertEqual(room.capacity, 55)

    def test_partial_update_room_patch_response(self):
        room = RoomService.create_room(
            room_number="Room 202",
            room_type=RoomType.CLASSROOM,
            capacity=35,
            name="Junior Section Room",
            actor=self.superuser
        )
        url = f"/api/v1/academics/rooms/{room.pk}/"
        patch_data = {"capacity": 48}
        res_patch = self.client.patch(url, patch_data)
        self.assertEqual(res_patch.status_code, 200)

        self.assertEqual(res_patch.data['id'], room.pk)
        self.assertEqual(res_patch.data['capacity'], 48)
        self.assertEqual(res_patch.data['room_number'], "Room 202")
        self.assertEqual(res_patch.data['name'], "Junior Section Room")

        room.refresh_from_db()
        self.assertEqual(room.capacity, 48)
        self.assertEqual(room.room_number, "Room 202")

    def test_toggle_room_status_api(self):
        room = RoomService.create_room(room_number="Room 102", is_active=True, actor=self.superuser)
        url = f"/api/v1/academics/rooms/{room.pk}/toggle-status/"

        # Deactivate
        res_deact = self.client.post(url, {"is_active": False}, format='json')
        self.assertEqual(res_deact.status_code, 200)
        self.assertFalse(res_deact.data['is_active'])
        room.refresh_from_db()
        self.assertFalse(room.is_active)

        # Reactivate
        res_act = self.client.post(url, {"is_active": True}, format='json')
        self.assertEqual(res_act.status_code, 200)
        self.assertTrue(res_act.data['is_active'])
        room.refresh_from_db()
        self.assertTrue(room.is_active)


class RoomRBACAPITests(TestCase):
    def setUp(self):
        call_command('sync_permissions')

        # Create roles
        perm_view = Permission.objects.get(codename='academics.room.view')
        perm_manage = Permission.objects.get(codename='academics.room.manage')
        perm_class_view = Permission.objects.get(codename='academics.class.view')
        perm_class_manage = Permission.objects.get(codename='academics.class.manage')

        view_role = Role.objects.create(name='Room Viewer Role', code='ROLE_ROOM_VIEWER')
        view_role.permissions.add(perm_view)

        manage_role = Role.objects.create(name='Room Manager Role', code='ROLE_ROOM_MANAGER')
        manage_role.permissions.add(perm_manage)

        full_role = Role.objects.create(name='Room Full Role', code='ROLE_ROOM_FULL')
        full_role.permissions.add(perm_view, perm_manage)

        unrelated_role = Role.objects.create(name='Class Manager Role', code='ROLE_CLASS_MANAGER')
        unrelated_role.permissions.add(perm_class_view, perm_class_manage)

        # Users
        self.view_user = User.objects.create_user(
            username='room_view_only',
            email='view_only@school.edu',
            password='Password123!',
            user_type=UserType.STAFF
        )
        UserRoleAssignment.objects.create(
            user=self.view_user, role=view_role, scope_type=ScopeType.GLOBAL, is_active=True
        )

        self.manage_user = User.objects.create_user(
            username='room_manage_only',
            email='manage_only@school.edu',
            password='Password123!',
            user_type=UserType.STAFF
        )
        UserRoleAssignment.objects.create(
            user=self.manage_user, role=manage_role, scope_type=ScopeType.GLOBAL, is_active=True
        )

        self.full_user = User.objects.create_user(
            username='room_full_access',
            email='full_access@school.edu',
            password='Password123!',
            user_type=UserType.STAFF
        )
        UserRoleAssignment.objects.create(
            user=self.full_user, role=full_role, scope_type=ScopeType.GLOBAL, is_active=True
        )

        self.unrelated_user = User.objects.create_user(
            username='room_unrelated_perm',
            email='unrelated@school.edu',
            password='Password123!',
            user_type=UserType.STAFF
        )
        UserRoleAssignment.objects.create(
            user=self.unrelated_user, role=unrelated_role, scope_type=ScopeType.GLOBAL, is_active=True
        )

        self.no_perm_user = User.objects.create_user(
            username='room_no_perm',
            email='no_perm@school.edu',
            password='Password123!',
            user_type=UserType.STAFF
        )

        self.inactive_user = User.objects.create_user(
            username='room_inactive_user',
            email='inactive@school.edu',
            password='Password123!',
            user_type=UserType.STAFF,
            is_active=False
        )
        UserRoleAssignment.objects.create(
            user=self.inactive_user, role=full_role, scope_type=ScopeType.GLOBAL, is_active=True
        )

        self.superuser = User.objects.create_superuser(
            username='room_superuser',
            email='superuser@school.edu',
            password='Password123!',
            user_type=UserType.ADMIN
        )

        self.client = APIClient()
        self.room = RoomService.create_room(
            room_number="Room 301",
            room_type=RoomType.CLASSROOM,
            capacity=40,
            actor=self.superuser
        )

    def test_view_only_user_can_list_and_retrieve_rooms(self):
        self.client.force_authenticate(user=self.view_user)
        res_list = self.client.get("/api/v1/academics/rooms/")
        self.assertEqual(res_list.status_code, 200)

        res_detail = self.client.get(f"/api/v1/academics/rooms/{self.room.pk}/")
        self.assertEqual(res_detail.status_code, 200)
        self.assertEqual(res_detail.data['id'], self.room.pk)

    def test_view_only_user_cannot_mutate_rooms(self):
        self.client.force_authenticate(user=self.view_user)

        # Create
        res_create = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room 999", "room_type": "CLASSROOM", "capacity": 30
        })
        self.assertEqual(res_create.status_code, 403)

        # Update
        res_update = self.client.put(f"/api/v1/academics/rooms/{self.room.pk}/", {
            "room_number": "Room 301-Mutated", "room_type": "CLASSROOM", "capacity": 50
        })
        self.assertEqual(res_update.status_code, 403)

        # Partial update
        res_patch = self.client.patch(f"/api/v1/academics/rooms/{self.room.pk}/", {"capacity": 55})
        self.assertEqual(res_patch.status_code, 403)

        # Toggle status
        res_toggle = self.client.post(f"/api/v1/academics/rooms/{self.room.pk}/toggle-status/", {"is_active": False})
        self.assertEqual(res_toggle.status_code, 403)

        # Delete
        res_delete = self.client.delete(f"/api/v1/academics/rooms/{self.room.pk}/")
        self.assertEqual(res_delete.status_code, 403)

        # Verify DB unmutated
        self.room.refresh_from_db()
        self.assertEqual(self.room.capacity, 40)
        self.assertTrue(self.room.is_active)

    def test_manage_only_user_cannot_read_rooms(self):
        self.client.force_authenticate(user=self.manage_user)

        res_list = self.client.get("/api/v1/academics/rooms/")
        self.assertEqual(res_list.status_code, 403)

        res_detail = self.client.get(f"/api/v1/academics/rooms/{self.room.pk}/")
        self.assertEqual(res_detail.status_code, 403)

    def test_manage_only_user_can_create_update_toggle_delete_rooms(self):
        self.client.force_authenticate(user=self.manage_user)

        # Create
        res_create = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room 302-Manage",
            "room_type": "CLASSROOM",
            "capacity": 32,
            "is_active": True
        })
        self.assertEqual(res_create.status_code, 201)
        created_id = res_create.data['id']

        # Update
        res_update = self.client.put(f"/api/v1/academics/rooms/{created_id}/", {
            "room_number": "Room 302-Managed-Updated",
            "room_type": "CLASSROOM",
            "capacity": 42,
            "is_active": True
        })
        self.assertEqual(res_update.status_code, 200)

        # Partial update
        res_patch = self.client.patch(f"/api/v1/academics/rooms/{created_id}/", {"capacity": 45})
        self.assertEqual(res_patch.status_code, 200)

        # Toggle status
        res_toggle = self.client.post(f"/api/v1/academics/rooms/{created_id}/toggle-status/", {"is_active": False})
        self.assertEqual(res_toggle.status_code, 200)

        # Delete
        res_delete = self.client.delete(f"/api/v1/academics/rooms/{created_id}/")
        self.assertEqual(res_delete.status_code, 204)
        self.assertFalse(Room.objects.filter(pk=created_id).exists())

    def test_full_access_user_can_perform_all_operations(self):
        self.client.force_authenticate(user=self.full_user)

        # List
        res_list = self.client.get("/api/v1/academics/rooms/")
        self.assertEqual(res_list.status_code, 200)

        # Create
        res_create = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room 401-Full",
            "room_type": "COMPUTER_LAB",
            "capacity": 30
        })
        self.assertEqual(res_create.status_code, 201)
        created_id = res_create.data['id']

        # Retrieve
        res_get = self.client.get(f"/api/v1/academics/rooms/{created_id}/")
        self.assertEqual(res_get.status_code, 200)

        # Update
        res_update = self.client.put(f"/api/v1/academics/rooms/{created_id}/", {
            "room_number": "Room 401-Updated",
            "room_type": "COMPUTER_LAB",
            "capacity": 35
        })
        self.assertEqual(res_update.status_code, 200)

        # Partial update
        res_patch = self.client.patch(f"/api/v1/academics/rooms/{created_id}/", {"capacity": 38})
        self.assertEqual(res_patch.status_code, 200)

        # Toggle status
        res_toggle = self.client.post(f"/api/v1/academics/rooms/{created_id}/toggle-status/", {"is_active": False})
        self.assertEqual(res_toggle.status_code, 200)

        # Delete
        res_delete = self.client.delete(f"/api/v1/academics/rooms/{created_id}/")
        self.assertEqual(res_delete.status_code, 204)

    def test_unrelated_academics_permission_user_denied(self):
        self.client.force_authenticate(user=self.unrelated_user)

        self.assertEqual(self.client.get("/api/v1/academics/rooms/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/academics/rooms/{self.room.pk}/").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/academics/rooms/", {"room_number": "R-1"}).status_code, 403)
        self.assertEqual(self.client.put(f"/api/v1/academics/rooms/{self.room.pk}/", {"room_number": "R-1"}).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/v1/academics/rooms/{self.room.pk}/", {"capacity": 50}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/academics/rooms/{self.room.pk}/toggle-status/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/academics/rooms/{self.room.pk}/").status_code, 403)

    def test_unauthenticated_user_denied(self):
        anon_client = APIClient()

        self.assertIn(anon_client.get("/api/v1/academics/rooms/").status_code, (401, 403))
        self.assertIn(anon_client.get(f"/api/v1/academics/rooms/{self.room.pk}/").status_code, (401, 403))
        self.assertIn(anon_client.post("/api/v1/academics/rooms/", {"room_number": "R-Anon"}).status_code, (401, 403))
        self.assertIn(anon_client.put(f"/api/v1/academics/rooms/{self.room.pk}/", {"room_number": "R-Anon"}).status_code, (401, 403))
        self.assertIn(anon_client.patch(f"/api/v1/academics/rooms/{self.room.pk}/", {"capacity": 50}).status_code, (401, 403))
        self.assertIn(anon_client.post(f"/api/v1/academics/rooms/{self.room.pk}/toggle-status/").status_code, (401, 403))
        self.assertIn(anon_client.delete(f"/api/v1/academics/rooms/{self.room.pk}/").status_code, (401, 403))

    def test_inactive_user_denied(self):
        self.client.force_authenticate(user=self.inactive_user)

        self.assertEqual(self.client.get("/api/v1/academics/rooms/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/academics/rooms/{self.room.pk}/").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/academics/rooms/", {"room_number": "R-Inact"}).status_code, 403)
        self.assertEqual(self.client.put(f"/api/v1/academics/rooms/{self.room.pk}/", {"room_number": "R-Inact"}).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/v1/academics/rooms/{self.room.pk}/", {"capacity": 50}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/academics/rooms/{self.room.pk}/toggle-status/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/academics/rooms/{self.room.pk}/").status_code, 403)

    def test_superuser_permanent_full_access(self):
        self.client.force_authenticate(user=self.superuser)

        self.assertEqual(self.client.get("/api/v1/academics/rooms/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/academics/rooms/{self.room.pk}/").status_code, 200)

        res_create = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room Super", "room_type": "CLASSROOM", "capacity": 30
        })
        self.assertEqual(res_create.status_code, 201)
        s_id = res_create.data['id']

        self.assertEqual(self.client.put(f"/api/v1/academics/rooms/{s_id}/", {
            "room_number": "Room Super-U", "room_type": "CLASSROOM", "capacity": 35
        }).status_code, 200)
        self.assertEqual(self.client.patch(f"/api/v1/academics/rooms/{s_id}/", {"capacity": 40}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/academics/rooms/{s_id}/toggle-status/").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/academics/rooms/{s_id}/").status_code, 204)


class RoomRegressionAPITests(TestCase):
    def setUp(self):
        call_command('sync_permissions')
        self.superuser = User.objects.create_superuser(
            username='room_reg_super',
            email='room_reg@school.edu',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.superuser)
        self.room = RoomService.create_room(
            room_number="Room 501",
            room_type=RoomType.CLASSROOM,
            capacity=40,
            actor=self.superuser
        )

    def test_service_validation_errors_handled_cleanly(self):
        # Capacity <= 0
        res_cap = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room BadCap", "room_type": "CLASSROOM", "capacity": 0
        })
        self.assertEqual(res_cap.status_code, 400)

        # Invalid room type
        res_type = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room BadType", "room_type": "INVALID_TYPE", "capacity": 30
        })
        self.assertEqual(res_type.status_code, 400)

        # Duplicate room number
        res_dup = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room 501", "room_type": "CLASSROOM", "capacity": 30
        })
        self.assertIn(res_dup.status_code, (400, 409))

    def test_audit_logging_generated_for_room_operations(self):
        res = self.client.post("/api/v1/academics/rooms/", {
            "room_number": "Room Audit-Test", "room_type": "CLASSROOM", "capacity": 30
        })
        self.assertEqual(res.status_code, 201)
        created_id = str(res.data['id'])
        self.assertTrue(
            AuditLog.objects.filter(entity_type='Room', action=AuditAction.CREATE, entity_id=created_id).exists()
        )

        res_patch = self.client.patch(f"/api/v1/academics/rooms/{created_id}/", {"capacity": 35})
        self.assertEqual(res_patch.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(entity_type='Room', action=AuditAction.UPDATE, entity_id=created_id).exists()
        )

    def test_delete_room_protection_when_referenced_in_section(self):
        school_class = SchoolClass.objects.create(name="Class 10-Reg", code="10-REG", order=10)
        section = Section.objects.create(
            school_class=school_class,
            name="A",
            capacity=40,
            room_ref=self.room,
            room_number=self.room.room_number
        )
        res_del = self.client.delete(f"/api/v1/academics/rooms/{self.room.pk}/")
        self.assertEqual(res_del.status_code, 409)
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())

    def test_delete_room_protection_when_referenced_in_timetable(self):
        from datetime import date
        academic_year = AcademicYear.objects.create(
            name="2027-2028",
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31)
        )
        school_class = SchoolClass.objects.create(name="Class 9-Reg", code="9-REG", order=9)
        section = Section.objects.create(school_class=school_class, name="B", capacity=30)
        subject = Subject.objects.create(name="Physics-Reg", code="PHY-REG")
        class_subject = ClassSubject.objects.create(
            section=section,
            subject=subject,
            academic_year=academic_year
        )
        period = TimetablePeriod.objects.create(
            period_number=1,
            name="Period 1-Reg",
            start_time="08:00:00",
            end_time="08:45:00"
        )
        slot = TimetableSlot.objects.create(
            academic_year=academic_year,
            day_of_week=1,
            period=period,
            class_subject=class_subject,
            room_ref=self.room,
            room=self.room.room_number
        )
        res_del = self.client.delete(f"/api/v1/academics/rooms/{self.room.pk}/")
        self.assertEqual(res_del.status_code, 409)
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())


class RoomWebViewsTests(TestCase):
    def setUp(self):
        call_command('sync_permissions')
        self.superuser = User.objects.create_superuser(
            username='web_admin_rooms',
            email='web_admin_rooms@school.edu',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        self.client.login(username='web_admin_rooms', password='Password123!')

    def test_rooms_tab_renders(self):
        RoomService.create_room(room_number="Lab-1", room_type=RoomType.COMPUTER_LAB, capacity=25, actor=self.superuser)
        url = reverse('academics-timetable-list') + '?tab=rooms'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Lab-1")
        self.assertContains(res, "Computer Lab")

    def test_create_and_delete_room_web_action(self):
        url = reverse('academics-timetable-list')
        post_data = {
            'action': 'create_room',
            'room_number': 'Room 105',
            'name': 'Classroom 105',
            'room_type': 'CLASSROOM',
            'capacity': '35',
            'floor': 'Floor 2',
            'is_active': 'on'
        }
        res = self.client.post(url, post_data)
        self.assertEqual(res.status_code, 302)
        room = Room.objects.filter(room_number='Room 105').first()
        self.assertIsNotNone(room)

        del_data = {
            'action': 'delete_room',
            'room_id': room.pk
        }
        res_del = self.client.post(url, del_data)
        self.assertEqual(res_del.status_code, 302)
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())





