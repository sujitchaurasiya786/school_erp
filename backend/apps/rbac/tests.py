from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from apps.identity.models import User, UserType
from apps.rbac.models import Role, Permission, UserRoleAssignment, ScopeType
from apps.rbac.services import RBACService


class RBACTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='staff_test',
            email='staff_test@test.com',
            password='Password123!',
            user_type=UserType.STAFF
        )
        self.perm = Permission.objects.create(
            module='students',
            action='view',
            codename='students.view',
            name='View Students'
        )
        self.role = Role.objects.create(
            code='staff_viewer',
            name='Staff Viewer'
        )
        self.role.permissions.add(self.perm)

    def test_permission_evaluation(self):
        # Before assignment
        self.assertFalse(RBACService.has_permission(self.user, 'students.view'))

        # Assign role
        UserRoleAssignment.objects.create(
            user=self.user,
            role=self.role,
            scope_type=ScopeType.GLOBAL
        )

        # After assignment
        self.assertTrue(RBACService.has_permission(self.user, 'students.view'))
        self.assertTrue(RBACService.has_module_action(self.user, 'students', 'view'))
        self.assertFalse(RBACService.has_module_action(self.user, 'students', 'delete'))
