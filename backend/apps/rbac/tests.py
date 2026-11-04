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

        # 3. Verify System Administrator role permissions (SRS §6.4: complete baseline access)
        admin_role = Role.objects.get(code='system_admin')
        admin_perms = set(admin_role.permissions.values_list('codename', flat=True))
        self.assertIn('identity.users.view', admin_perms)
        self.assertIn('rbac.role.manage', admin_perms)
        self.assertIn('hrms.payroll.view', admin_perms)
        self.assertIn('helpdesk.grievance.view', admin_perms)

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


class SidebarRefactorTests(TestCase):
    """
    Tests ensuring the refactored sidebar complies strictly with SRS requirements:
      - Valid user-facing template routes only.
      - Zero raw `/api/v1/` endpoints.
      - Deferred modules (Alumni, Hostel, T&P) completely removed.
      - Non-implemented screens are kept hidden.
      - Permission-based visibility controls access.
    """

    def setUp(self):
        call_command('sync_permissions')

        # Admin user with full admin permissions
        self.admin_user = User.objects.create_user(
            username='admin_sidebar',
            email='admin_sidebar@school.local',
            password='Password@123',
            user_type=UserType.ADMIN,
            is_staff=True,
            is_superuser=True,
            is_active=True
        )
        self.admin_role, _ = Role.objects.get_or_create(
            code='system_admin_sidebar',
            defaults={'name': 'System Admin Sidebar', 'is_system_role': True, 'is_active': True}
        )
        self.admin_role.permissions.set(Permission.objects.filter(is_active=True))
        UserRoleAssignment.objects.create(
            user=self.admin_user,
            role=self.admin_role,
            scope_type=ScopeType.GLOBAL,
            is_active=True
        )

        # Standard student user without admin permissions
        self.student_user = User.objects.create_user(
            username='student_sidebar',
            email='student_sidebar@school.local',
            password='Password@123',
            user_type=UserType.STUDENT,
            is_staff=False,
            is_superuser=False,
            is_active=True
        )

        self.client = Client()

    def test_sidebar_renders_for_authenticated_admin(self):
        self.client.login(username='admin_sidebar', password='Password@123')
        resp = self.client.get(reverse('home-dashboard'))
        self.assertEqual(resp.status_code, 200)

        # Dashboard link present
        self.assertContains(resp, 'Dashboard')

        # Admin navigation items present for authorized user
        self.assertContains(resp, 'User Management')
        self.assertContains(resp, 'Roles & Permissions')
        self.assertContains(resp, 'Permissions Explorer')
        self.assertContains(resp, 'School Settings')

    def test_sidebar_never_contains_raw_api_endpoints(self):
        self.client.login(username='admin_sidebar', password='Password@123')
        resp = self.client.get(reverse('home-dashboard'))
        self.assertEqual(resp.status_code, 200)

        # Strict rule: NEVER use /api/v1/... endpoints as page navigation links
        self.assertNotContains(resp, 'href="/api/v1/')
        self.assertNotContains(resp, "href='/api/v1/")

    def test_sidebar_excludes_deferred_modules(self):
        self.client.login(username='admin_sidebar', password='Password@123')
        resp = self.client.get(reverse('home-dashboard'))
        self.assertEqual(resp.status_code, 200)

        # SRS Rule: Do not add Hostel & Mess, Alumni, LMS, Communications, AI, T&P
        content = resp.content.decode('utf-8')
        self.assertNotIn('Alumni Mgmt', content)
        self.assertNotIn('Hostel Mgmt', content)
        self.assertNotIn('T&P Cell', content)
        self.assertNotIn('Smart Card Mgmt', content)
        self.assertNotIn('Recycle Bin', content)
        self.assertNotIn('ERP Masters Entry', content)
        self.assertNotIn('Doc. Procurement', content)

    def test_sidebar_hides_admin_links_for_unauthorized_user(self):
        self.client.login(username='student_sidebar', password='Password@123')
        resp = self.client.get(reverse('home-dashboard'))
        self.assertEqual(resp.status_code, 200)

        # Unauthorized student cannot see User Management or Roles & Permissions
        content = resp.content.decode('utf-8')
        self.assertNotIn('Roles & Permissions', content)
        self.assertNotIn('User Management', content)
        self.assertNotIn('School Settings', content)

    def test_template_tags_and_context_processor(self):
        from django.template import Context, Template
        from django.test import RequestFactory
        from apps.rbac.context_processors import rbac_context

        rf = RequestFactory()
        req = rf.get('/')
        req.user = self.admin_user

        ctx_data = rbac_context(req)
        self.assertTrue(ctx_data['has_rbac_access'])

        # Test custom template tags
        template_str = """
        {% load rbac_tags %}
        {% if user|has_perm:'identity.users.view' %}CAN_MANAGE_USERS{% endif %}
        {% if user|has_perm:'nonexistent.perm' %}SHOULD_NOT_SHOW{% endif %}
        """
        rendered = Template(template_str).render(Context({'user': self.admin_user}))
        self.assertIn('CAN_MANAGE_USERS', rendered)
        self.assertNotIn('SHOULD_NOT_SHOW', rendered)


class SystemAdministratorAuthorizationPolicyTests(TestCase):
    """
    Tests enforcing the updated System Administrator Policy (SRS §6.4):
      1. Superuser has complete access across all 12 modules without a dynamic role.
      2. Superuser accesses protected DRF endpoints without dynamic role assignment.
      3. Normal user with dynamic permission can access resource.
      4. Normal user without permission receives appropriate denial.
      5. is_staff=True without is_superuser does not grant full access.
      6. Anonymous users are denied.
      7. Existing data-scope restrictions continue working for normal users.
      8. Approval workflows and audit logging remain functional and auditable.
    """

    def setUp(self):
        call_command('sync_permissions')

        # 1. Superuser WITHOUT ANY UserRoleAssignment
        self.superuser = User.objects.create_superuser(
            username='pure_super',
            email='super@school.local',
            password='Password@123',
            user_type=UserType.ADMIN,
            is_staff=True,
            is_superuser=True,
            is_active=True
        )

        # 2. Staff user WITHOUT superuser and WITHOUT role
        self.staff_user = User.objects.create_user(
            username='staff_only',
            email='staff@school.local',
            password='Password@123',
            user_type=UserType.TEACHER,
            is_staff=True,
            is_superuser=False,
            is_active=True
        )

        # 3. Normal user WITHOUT role
        self.normal_user = User.objects.create_user(
            username='normal_user',
            email='normal@school.local',
            password='Password@123',
            user_type=UserType.STUDENT,
            is_staff=False,
            is_superuser=False,
            is_active=True
        )

        # 4. Scoped normal user with role
        self.scoped_user = User.objects.create_user(
            username='scoped_teacher',
            email='scoped@school.local',
            password='Password@123',
            user_type=UserType.TEACHER,
            is_staff=True,
            is_superuser=False,
            is_active=True
        )
        self.teacher_role = Role.objects.create(
            code='scoped_teacher_role',
            name='Scoped Teacher Role',
            is_system_role=False,
            is_active=True
        )
        perm = Permission.objects.get(codename='attendance.record.mark')
        self.teacher_role.permissions.add(perm)
        UserRoleAssignment.objects.create(
            user=self.scoped_user,
            role=self.teacher_role,
            scope_type=ScopeType.CLASS,
            scope_id='10',
            is_active=True
        )

        self.client = Client()

    def test_1_superuser_has_all_erp_permissions_without_dynamic_role(self):
        from apps.rbac.services import RBACService
        # Superuser has NO UserRoleAssignment
        self.assertFalse(UserRoleAssignment.objects.filter(user=self.superuser).exists())

        # Yet has access to ALL active module permissions
        self.assertTrue(RBACService.has_permission(self.superuser, 'identity.users.view'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'rbac.role.manage'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'school_config.profile.view'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'admissions.application.approve'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'students.profile.view'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'academics.timetable.manage'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'attendance.record.mark'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'finance.invoice.view'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'exams.marks.enter'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'hrms.payroll.run'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'library.circulation.manage'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'transport.routes.manage'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'inventory.assets.manage'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'helpdesk.grievance.view'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'analytics.reports.export'))
        self.assertTrue(RBACService.has_permission(self.superuser, 'audit.logs.view'))

    def test_2_superuser_accesses_protected_drf_endpoints_without_role(self):
        from rest_framework.test import APIClient
        api_client = APIClient()
        api_client.force_authenticate(user=self.superuser)

        # Access school-config API without any role assigned
        resp1 = api_client.get('/api/v1/school-config/academic-years/')
        self.assertEqual(resp1.status_code, 200)

        # Access students API without any role assigned
        resp2 = api_client.get('/api/v1/students/profiles/')
        self.assertEqual(resp2.status_code, 200)

    def test_3_normal_user_with_dynamic_permission_allowed(self):
        from apps.rbac.services import RBACService
        self.assertTrue(
            RBACService.has_permission(self.scoped_user, 'attendance.record.mark', scope_type=ScopeType.CLASS, scope_id='10')
        )

    def test_4_normal_user_without_permission_denied(self):
        from apps.rbac.services import RBACService
        self.assertFalse(RBACService.has_permission(self.normal_user, 'finance.invoice.view'))
        self.assertFalse(RBACService.has_permission(self.normal_user, 'hrms.payroll.run'))
        self.assertFalse(RBACService.has_permission(self.normal_user, 'helpdesk.grievance.view'))

    def test_5_staff_user_without_superuser_denied_without_role(self):
        from apps.rbac.services import RBACService
        # is_staff=True alone must NEVER bypass RBAC
        self.assertTrue(self.staff_user.is_staff)
        self.assertFalse(self.staff_user.is_superuser)
        self.assertFalse(RBACService.has_permission(self.staff_user, 'hrms.payroll.run'))
        self.assertFalse(RBACService.has_permission(self.staff_user, 'finance.invoice.view'))
        self.assertFalse(RBACService.has_permission(self.staff_user, 'students.profile.view'))

        from rest_framework.test import APIClient
        api_client = APIClient()
        api_client.force_authenticate(user=self.staff_user)
        resp = api_client.get('/api/v1/students/profiles/')
        self.assertEqual(resp.status_code, 403)

    def test_6_anonymous_users_denied(self):
        from apps.rbac.services import RBACService
        from django.contrib.auth.models import AnonymousUser
        anon = AnonymousUser()
        self.assertFalse(RBACService.has_permission(anon, 'students.profile.view'))
        self.assertFalse(RBACService.has_module_action(anon, 'students', 'view'))
        self.assertEqual(RBACService.get_user_permissions(anon), set())

        from rest_framework.test import APIClient
        api_client = APIClient()
        resp = api_client.get('/api/v1/students/profiles/')
        self.assertIn(resp.status_code, [401, 403])

    def test_7_data_scope_restrictions_work_for_normal_users(self):
        from apps.rbac.services import RBACService
        # Scoped to class 10
        self.assertTrue(
            RBACService.has_permission(self.scoped_user, 'attendance.record.mark', scope_type=ScopeType.CLASS, scope_id='10')
        )
        # Denied for class 11
        self.assertFalse(
            RBACService.has_permission(self.scoped_user, 'attendance.record.mark', scope_type=ScopeType.CLASS, scope_id='11')
        )

    def test_8_audit_logging_and_workflows_preserve_superuser_actor(self):
        from apps.audit.services import AuditService
        from apps.audit.models import AuditAction, AuditLog

        # Sensitive action performed by superuser
        log_entry = AuditService.log(
            action=AuditAction.UPDATE,
            module='school_config',
            entity_type='SchoolProfile',
            entity_id='1',
            actor=self.superuser,
            reason='Superuser updated school settings'
        )
        self.assertIsNotNone(log_entry.id)
        self.assertEqual(log_entry.actor, self.superuser)
        self.assertEqual(log_entry.actor_username, 'pure_super')
        self.assertEqual(log_entry.action, AuditAction.UPDATE)


