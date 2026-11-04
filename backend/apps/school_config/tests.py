from datetime import date
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from apps.school_config.models import AcademicYear, SchoolProfile
from apps.identity.models import User, UserType
from apps.rbac.models import Role, Permission, UserRoleAssignment, ScopeType


class SchoolConfigTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_superuser(
            username='admin_config',
            email='admin_config@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )
        # Assign dynamic RBAC permissions for school_config module
        call_command('sync_permissions')
        self.role = Role.objects.create(
            code='school_config_admin_role',
            name='School Config Admin Role',
            is_system_role=True,
            is_active=True
        )
        self.role.permissions.set(Permission.objects.filter(module='school_config'))
        UserRoleAssignment.objects.create(
            user=self.admin_user,
            role=self.role,
            scope_type=ScopeType.GLOBAL,
            is_active=True
        )

    def test_academic_year_current_toggle(self):
        year1 = AcademicYear.objects.create(
            name='2025-2026',
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
            is_current=True
        )
        self.assertTrue(year1.is_current)

        year2 = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        year1.refresh_from_db()
        self.assertFalse(year1.is_current)
        self.assertTrue(year2.is_current)

    def test_current_year_api(self):
        year = AcademicYear.objects.create(
            name='2026-2027',
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True
        )
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/v1/school-config/academic-years/current/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], '2026-2027')
