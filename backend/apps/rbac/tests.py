"""
Tests for RBAC Management Dashboard and Bootstrap System Admin command.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, Client
from django.urls import reverse

from apps.rbac.models import Role, Permission, UserRoleAssignment, ScopeType
from apps.identity.models import UserType

User = get_user_model()


class BootstrapSystemAdminCommandTests(TestCase):
    """
    Tests for `bootstrap_system_admin` management command.
    """

    def test_bootstrap_command_execution(self):
        # Run bootstrap command non-interactively
        call_command(
            'bootstrap_system_admin',
            username='sysadmin',
            email='sysadmin@school.local',
            password='TestPassword@123',
            no_input=True
        )

        # 1. Verify permissions synced
        self.assertGreaterEqual(Permission.objects.filter(is_active=True).count(), 58)

        # 2. Verify system roles created
        system_roles = Role.objects.filter(is_system_role=True)
        role_codes = set(system_roles.values_list('code', flat=True))
        expected_codes = {'system_admin', 'principal', 'teacher', 'accountant', 'parent', 'student'}
        self.assertTrue(expected_codes.issubset(role_codes))

        # 3. Verify System Administrator role permissions (SRS §3.7: no payroll or confidential grievance)
        admin_role = Role.objects.get(code='system_admin')
        admin_perms = set(admin_role.permissions.values_list('codename', flat=True))
        self.assertNotIn('hrms.payroll.view', admin_perms)
        self.assertNotIn('hrms.payroll.run', admin_perms)
        self.assertNotIn('helpdesk.grievance.view', admin_perms)
        self.assertIn('identity.users.view', admin_perms)
        self.assertIn('rbac.role.manage', admin_perms)

        # 4. Verify admin user
        admin_user = User.objects.get(username='sysadmin')
        self.assertEqual(admin_user.email, 'sysadmin@school.local')
        self.assertEqual(admin_user.user_type, UserType.ADMIN)
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_superuser)
        self.assertTrue(admin_user.check_password('TestPassword@123'))

        # 5. Verify assignment
        assignment = UserRoleAssignment.objects.get(user=admin_user, role=admin_role)
        self.assertEqual(assignment.scope_type, ScopeType.GLOBAL)
        self.assertTrue(assignment.is_active)


class RBACDashboardViewTests(TestCase):
    """
    Tests for the RBAC web dashboard views (Roles, Permissions, Assignments, Users).
    """

    def setUp(self):
        call_command('sync_permissions')

        # Create admin user and assign system_admin role
        self.admin_user = User.objects.create_user(
            username='admin_tester',
            email='admin_tester@school.local',
            password='Password@123',
            user_type=UserType.ADMIN,
            is_staff=True,
            is_superuser=True,
            is_active=True
        )

        # Setup roles
        self.admin_role = Role.objects.create(
            code='system_admin',
            name='System Administrator',
            is_system_role=True,
            is_active=True
        )
        UserRoleAssignment.objects.create(
            user=self.admin_user,
            role=self.admin_role,
            scope_type=ScopeType.GLOBAL,
            is_active=True
        )

        # Regular user without permissions
        self.regular_user = User.objects.create_user(
            username='student1',
            email='student1@school.local',
            password='Password@123',
            user_type=UserType.STUDENT,
            is_active=True
        )

        self.client = Client()

    def test_unauthenticated_user_redirected_to_login(self):
        resp = self.client.get(reverse('rbac-roles-list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_roles_list_view_for_admin(self):
        self.client.login(username='admin_tester', password='Password@123')
        resp = self.client.get(reverse('rbac-roles-list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Dynamic Role Management')
        self.assertContains(resp, 'System Administrator')

    def test_role_create_view_post(self):
        self.client.login(username='admin_tester', password='Password@123')
        perm1 = Permission.objects.filter(is_active=True).first()

        data = {
            'name': 'Exam Invigilator',
            'code': 'exam_invigilator',
            'description': 'Temporary staff for exam halls',
            'is_active': 'on',
            'permissions': [perm1.id],
        }
        resp = self.client.post(reverse('rbac-role-create'), data=data)
        self.assertEqual(resp.status_code, 302)

        new_role = Role.objects.get(code='exam_invigilator')
        self.assertEqual(new_role.name, 'Exam Invigilator')
        self.assertFalse(new_role.is_system_role)
        self.assertTrue(new_role.permissions.filter(id=perm1.id).exists())

    def test_role_detail_and_assign_user_with_scope(self):
        self.client.login(username='admin_tester', password='Password@123')

        custom_role = Role.objects.create(
            code='class_teacher_10a',
            name='Class Teacher 10A',
            is_system_role=False,
            is_active=True
        )

        # GET detail view
        resp = self.client.get(reverse('rbac-role-detail', kwargs={'pk': custom_role.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Class Teacher 10A')

        # POST assign regular_user with SECTION scope
        assign_data = {
            'assign_user': '1',
            'user_id': self.regular_user.id,
            'scope_type': ScopeType.SECTION,
            'scope_id': '10-A',
        }
        post_resp = self.client.post(reverse('rbac-role-detail', kwargs={'pk': custom_role.pk}), data=assign_data)
        self.assertEqual(post_resp.status_code, 302)

        assignment = UserRoleAssignment.objects.get(user=self.regular_user, role=custom_role)
        self.assertEqual(assignment.scope_type, ScopeType.SECTION)
        self.assertEqual(assignment.scope_id, '10-A')
        self.assertTrue(assignment.is_active)

    def test_system_role_cannot_be_deleted(self):
        self.client.login(username='admin_tester', password='Password@123')
        resp = self.client.post(reverse('rbac-role-delete', kwargs={'pk': self.admin_role.pk}))
        self.assertEqual(resp.status_code, 302)
        # Verify it was NOT deleted
        self.assertTrue(Role.objects.filter(pk=self.admin_role.pk).exists())

    def test_permissions_explorer_view(self):
        self.client.login(username='admin_tester', password='Password@123')
        resp = self.client.get(reverse('rbac-permissions-list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Permissions Registry Explorer')

    def test_users_list_view(self):
        self.client.login(username='admin_tester', password='Password@123')
        resp = self.client.get(reverse('identity-users-list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'User Account Directory')
        self.assertContains(resp, 'admin_tester')
        self.assertContains(resp, 'student1')
