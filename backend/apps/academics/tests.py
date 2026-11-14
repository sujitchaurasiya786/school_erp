from datetime import date
import threading
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import OperationalError, connections
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model

from apps.academics.models import ClassSubject, SchoolClass, Section, Subject, TimetablePeriod, TimetableSlot
from apps.academics.services import AcademicYearService
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
        ]
        for codename in expected_codenames:
            perm = Permission.objects.filter(codename=codename, is_active=True).first()
            self.assertIsNotNone(perm, f"Permission {codename} not found in database!")
            self.assertEqual(perm.module, 'academics')

    def test_migration_consistency_dry_run(self):
        # Asserts no ungenerated migrations exist across school_config
        from io import StringIO
        out = StringIO()
        call_command('makemigrations', 'school_config', check=True, dry_run=True, stdout=out)
        self.assertIn("No changes detected", out.getvalue())
