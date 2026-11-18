from datetime import date
import threading
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import OperationalError, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.academics.models import AcademicStream, ClassSubject, SchoolClass, Section, Stream, Subject, SubjectType, TimetablePeriod, TimetableSlot
from apps.academics.services import (
    AcademicYearService, ClassSubjectService, SchoolClassService, SectionService, StreamService, SubjectService
)
from apps.audit.models import AuditAction, AuditLog
from apps.rbac.models import Permission
from apps.rbac.registry import PERMISSION_REGISTRY
from apps.school_config.models import AcademicYear, SchoolProfile
from common.exceptions import BusinessConflictError, ResourceNotFoundError

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

