"""
Comprehensive tests for:
  1. Custom User model (creation, password hashing, inactive users)
  2. RBAC models (Role, Permission, UserRoleAssignment)
  3. RBACService (permission evaluation, scope, inactive assignments)
  4. HasDynamicPermission (DRF permission class)
  5. Login / Logout / Password-change views
  6. Permission registry sync (sync_permissions command)
  7. Protected system-role rules
  8. Audit log creation for RBAC mutations
"""
from io import StringIO

from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.test import TestCase, RequestFactory
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.identity.models import User, UserType
from apps.rbac.models import Permission, Role, ScopeType, UserRoleAssignment
from apps.rbac.permissions import HasDynamicPermission
from apps.rbac.registry import PERMISSION_REGISTRY
from apps.rbac.services import RBACService


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_user(username, user_type=UserType.STAFF, is_active=True, **kwargs):
    return User.objects.create_user(
        username=username,
        email=f"{username}@school.test",
        password="TestPass@999",
        user_type=user_type,
        is_active=is_active,
        **kwargs,
    )


def make_permission(codename, module="students", action="view"):
    return Permission.objects.create(
        codename=codename,
        module=module,
        action=action,
        name=f"Test perm {codename}",
    )


def make_role(code, name=None, is_system_role=False, permissions=None):
    role = Role.objects.create(
        code=code,
        name=name or code.replace("-", " ").title(),
        is_system_role=is_system_role,
    )
    if permissions:
        role.permissions.set(permissions)
    return role


def assign_role(user, role, scope_type=ScopeType.GLOBAL, scope_id=None, is_active=True):
    return UserRoleAssignment.objects.create(
        user=user,
        role=role,
        scope_type=scope_type,
        scope_id=scope_id or '',
        is_active=is_active,
    )


# ─── 1. Custom User Model ─────────────────────────────────────────────────────

class UserModelTests(TestCase):

    def test_create_user_sets_password_hash(self):
        user = make_user("teacher1", user_type=UserType.TEACHER)
        self.assertTrue(check_password("TestPass@999", user.password))

    def test_create_user_does_not_store_plain_password(self):
        user = make_user("teacher2")
        self.assertNotEqual(user.password, "TestPass@999")

    def test_create_superuser(self):
        admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@school.test",
            password="Admin@Pass1",
            user_type=UserType.ADMIN,
        )
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_user_str_representation(self):
        user = make_user("janedoe")
        # No first/last name set: str falls back to username
        self.assertIn("janedoe", str(user))

    def test_user_str_with_full_name(self):
        user = make_user("johndoe", first_name="John", last_name="Doe")
        self.assertIn("John Doe", str(user))

    def test_inactive_user_flag(self):
        user = make_user("inactive_user", is_active=False)
        self.assertFalse(user.is_active)

    def test_must_change_password_default_is_false(self):
        user = make_user("newstaff")
        self.assertFalse(user.must_change_password)

    def test_email_uniqueness(self):
        from django.db import IntegrityError
        make_user("user_a")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username="user_b",
                email="user_a@school.test",  # same email
                password="Pass@123",
            )

    def test_username_uniqueness(self):
        from django.db import IntegrityError
        make_user("unique_user")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username="unique_user",  # same username
                email="different@school.test",
                password="Pass@123",
            )

    def test_full_name_property(self):
        user = make_user("fn_test", first_name="Priya", last_name="Sharma")
        self.assertEqual(user.full_name, "Priya Sharma")

    def test_full_name_property_fallback(self):
        user = make_user("nofullname")
        self.assertEqual(user.full_name, "nofullname")


# ─── 2. RBAC Models ───────────────────────────────────────────────────────────

class RoleModelTests(TestCase):

    def test_create_role(self):
        role = make_role("class-teacher", "Class Teacher")
        self.assertEqual(role.name, "Class Teacher")
        self.assertFalse(role.is_system_role)

    def test_system_role_flag(self):
        role = make_role("principal", "Principal", is_system_role=True)
        self.assertTrue(role.is_system_role)

    def test_role_code_unique(self):
        from django.db import IntegrityError
        make_role("unique-code")
        with self.assertRaises(IntegrityError):
            Role.objects.create(code="unique-code", name="Different Name")

    def test_role_name_unique(self):
        from django.db import IntegrityError
        make_role("code-a", "Unique Role Name")
        with self.assertRaises(IntegrityError):
            Role.objects.create(code="code-b", name="Unique Role Name")


class PermissionModelTests(TestCase):

    def test_create_permission(self):
        perm = make_permission("students.profile.view")
        self.assertEqual(perm.codename, "students.profile.view")

    def test_permission_codename_unique(self):
        from django.db import IntegrityError
        make_permission("finance.payment.record", module="finance", action="add")
        with self.assertRaises(IntegrityError):
            Permission.objects.create(
                codename="finance.payment.record",
                module="finance",
                action="view",
                name="Duplicate",
            )

    def test_permission_str(self):
        perm = make_permission("audit.logs.view")
        self.assertIn("audit.logs.view", str(perm))


class UserRoleAssignmentTests(TestCase):

    def setUp(self):
        self.user = make_user("assignee")
        self.role = make_role("viewer")

    def test_unique_together_constraint(self):
        from django.db import IntegrityError
        assign_role(self.user, self.role)
        with self.assertRaises(IntegrityError):
            UserRoleAssignment.objects.create(
                user=self.user,
                role=self.role,
                scope_type=ScopeType.GLOBAL,
                scope_id=None,
            )

    def test_same_role_different_scope_allowed(self):
        assign_role(self.user, self.role, scope_type=ScopeType.GLOBAL)
        # Section-scoped assignment for same user+role should be allowed
        assign_role(self.user, self.role, scope_type=ScopeType.SECTION, scope_id="101")
        self.assertEqual(
            UserRoleAssignment.objects.filter(user=self.user, role=self.role).count(), 2
        )


# ─── 3. RBACService ───────────────────────────────────────────────────────────

class RBACServiceTests(TestCase):

    def setUp(self):
        self.user = make_user("rbac_user")
        self.perm_view = make_permission("students.profile.view", "students", "view")
        self.perm_add = make_permission("students.profile.add", "students", "add")
        self.perm_payroll = make_permission("hrms.payroll.view", "hrms", "view")
        self.role = make_role("teacher", permissions=[self.perm_view, self.perm_add])

    def test_no_permissions_before_assignment(self):
        self.assertFalse(RBACService.has_permission(self.user, "students.profile.view"))

    def test_permission_granted_after_assignment(self):
        assign_role(self.user, self.role)
        self.assertTrue(RBACService.has_permission(self.user, "students.profile.view"))

    def test_permission_denied_for_unpossessed_codename(self):
        assign_role(self.user, self.role)
        self.assertFalse(RBACService.has_permission(self.user, "finance.payment.record"))

    def test_has_module_action_dot_notation(self):
        assign_role(self.user, self.role)
        self.assertTrue(RBACService.has_module_action(self.user, "students", "view"))

    def test_has_module_action_denied(self):
        assign_role(self.user, self.role)
        self.assertFalse(RBACService.has_module_action(self.user, "students", "delete"))

    def test_unauthenticated_user_denied(self):
        from unittest.mock import MagicMock
        anon = MagicMock()
        anon.is_authenticated = False
        self.assertFalse(RBACService.has_permission(anon, "students.profile.view"))

    def test_inactive_user_denied(self):
        inactive = make_user("inactive_rbac", is_active=False)
        assign_role(inactive, self.role)
        # Even with an assignment, inactive user must be denied
        self.assertFalse(RBACService.has_permission(inactive, "students.profile.view"))

    def test_inactive_role_assignment_denied(self):
        assign_role(self.user, self.role, is_active=False)
        self.assertFalse(RBACService.has_permission(self.user, "students.profile.view"))

    def test_inactive_role_denied(self):
        self.role.is_active = False
        self.role.save()
        assign_role(self.user, self.role)
        self.assertFalse(RBACService.has_permission(self.user, "students.profile.view"))

    def test_multiple_roles_combined_permissions(self):
        perm_finance = make_permission("finance.invoice.view", "finance", "view")
        finance_role = make_role("accountant", permissions=[perm_finance])
        assign_role(self.user, self.role)
        assign_role(self.user, finance_role)
        self.assertTrue(RBACService.has_permission(self.user, "students.profile.view"))
        self.assertTrue(RBACService.has_permission(self.user, "finance.invoice.view"))

    def test_superuser_automatically_granted_all_permissions_without_role(self):
        """
        SRS §6.4: System Administrators (is_superuser=True) automatically receive
        complete ERP access across all modules without requiring role assignments.
        """
        admin = User.objects.create_superuser(
            username="sys_admin",
            email="sysadmin@school.test",
            password="Admin@Pass1",
            user_type=UserType.ADMIN,
        )
        # Admin has no role assignments — automatically granted permissions per SRS §6.4
        self.assertTrue(RBACService.has_permission(admin, "hrms.payroll.view"))
        self.assertTrue(RBACService.has_permission(admin, "hrms.payroll.run"))
        self.assertTrue(RBACService.has_permission(admin, "finance.invoice.view"))
        self.assertTrue(RBACService.has_permission(admin, "helpdesk.grievance.view"))
        self.assertTrue(RBACService.has_module_action(admin, "students", "view"))

    def test_staff_user_does_not_bypass_rbac_without_role(self):
        """
        SRS §6.3: is_staff=True alone must NEVER grant full ERP access or bypass RBAC.
        """
        staff = User.objects.create_user(
            username="staff_only",
            email="staff_only@school.test",
            password="Staff@Pass1",
            user_type=UserType.TEACHER,
            is_staff=True,
            is_superuser=False,
            is_active=True,
        )
        self.assertFalse(RBACService.has_permission(staff, "hrms.payroll.view"))
        self.assertFalse(RBACService.has_permission(staff, "students.profile.view"))
        self.assertFalse(RBACService.has_module_action(staff, "students", "view"))

    def test_superuser_gets_permission_via_explicit_assignment(self):
        admin = User.objects.create_superuser(
            username="sys_admin2",
            email="sysadmin2@school.test",
            password="Admin@Pass1",
            user_type=UserType.ADMIN,
        )
        payroll_role = make_role("payroll-manager", permissions=[self.perm_payroll])
        assign_role(admin, payroll_role)
        self.assertTrue(RBACService.has_permission(admin, "hrms.payroll.view"))

    def test_get_user_permissions_returns_set(self):
        assign_role(self.user, self.role)
        perms = RBACService.get_user_permissions(self.user)
        self.assertIsInstance(perms, set)
        self.assertIn("students.profile.view", perms)
        self.assertIn("students.profile.add", perms)

    def test_has_any_permission(self):
        assign_role(self.user, self.role)
        self.assertTrue(
            RBACService.has_any_permission(
                self.user, ["students.profile.view", "finance.payment.record"]
            )
        )

    def test_has_all_permissions(self):
        assign_role(self.user, self.role)
        self.assertTrue(
            RBACService.has_all_permissions(
                self.user, ["students.profile.view", "students.profile.add"]
            )
        )
        self.assertFalse(
            RBACService.has_all_permissions(
                self.user, ["students.profile.view", "finance.payment.record"]
            )
        )

    def test_scope_section_respects_global_assignment(self):
        """A GLOBAL-scoped assignment should satisfy a section-level query."""
        assign_role(self.user, self.role, scope_type=ScopeType.GLOBAL)
        self.assertTrue(
            RBACService.has_permission(
                self.user,
                "students.profile.view",
                scope_type=ScopeType.SECTION,
                scope_id="10A",
            )
        )

    def test_scope_section_specific_assignment(self):
        """A SECTION-scoped assignment should only match that section."""
        assign_role(
            self.user, self.role, scope_type=ScopeType.SECTION, scope_id="10A"
        )
        # Correct section: allowed
        self.assertTrue(
            RBACService.has_permission(
                self.user,
                "students.profile.view",
                scope_type=ScopeType.SECTION,
                scope_id="10A",
            )
        )
        # Wrong section: denied
        self.assertFalse(
            RBACService.has_permission(
                self.user,
                "students.profile.view",
                scope_type=ScopeType.SECTION,
                scope_id="10B",
            )
        )


# ─── 4. HasDynamicPermission ──────────────────────────────────────────────────

class HasDynamicPermissionTests(TestCase):

    def setUp(self):
        self.perm = make_permission("students.profile.view", "students", "view")
        self.role = make_role("viewer-role", permissions=[self.perm])
        self.user = make_user("perm_user")
        assign_role(self.user, self.role)

        self.factory = RequestFactory()
        self.permission_class = HasDynamicPermission()

    def _make_view(self, module_name=None, required_permission=None):
        from unittest.mock import MagicMock
        view = MagicMock()
        view.module_name = module_name
        view.required_permission = required_permission
        view.action = "list"
        view.action_permission_map = {}
        return view

    def _make_request(self, user, method="GET"):
        request = self.factory.generic(method, "/")
        request.user = user
        return request

    def test_unauthenticated_denied(self):
        from unittest.mock import MagicMock
        anon = MagicMock()
        anon.is_authenticated = False
        request = self._make_request(anon)
        view = self._make_view(module_name="students")
        self.assertFalse(self.permission_class.has_permission(request, view))

    def test_inactive_user_denied(self):
        inactive = make_user("inactive_perm", is_active=False)
        request = self._make_request(inactive)
        view = self._make_view(module_name="students")
        self.assertFalse(self.permission_class.has_permission(request, view))

    def test_user_with_role_allowed(self):
        request = self._make_request(self.user)
        view = self._make_view(required_permission="students.profile.view")
        self.assertTrue(self.permission_class.has_permission(request, view))

    def test_user_without_required_permission_denied(self):
        request = self._make_request(self.user)
        view = self._make_view(required_permission="finance.payment.record")
        self.assertFalse(self.permission_class.has_permission(request, view))

    def test_no_module_or_permission_denies(self):
        request = self._make_request(self.user)
        view = self._make_view()  # no module_name or required_permission
        self.assertFalse(self.permission_class.has_permission(request, view))

    def test_must_change_password_blocks_access(self):
        user = make_user("forcepass")
        user.must_change_password = True
        user.save()
        assign_role(user, self.role)
        request = self._make_request(user, method="GET")
        view = self._make_view(module_name="students")
        self.assertFalse(self.permission_class.has_permission(request, view))

    def test_must_change_password_allows_me_patch(self):
        user = make_user("forcepass2")
        user.must_change_password = True
        user.save()
        request = self._make_request(user, method="PATCH")
        view = self._make_view()
        view.action = "me"
        self.assertTrue(self.permission_class.has_permission(request, view))

    def test_superuser_access_allowed_without_role_or_explicit_permission(self):
        """
        SRS §6.4: Superuser accesses DRF endpoints protected by HasDynamicPermission
        without requiring any dynamic role assignment.
        """
        admin = make_user("drf_super", is_superuser=True, is_staff=True)
        request = self._make_request(admin)
        view = self._make_view(required_permission="hrms.payroll.run")
        self.assertTrue(self.permission_class.has_permission(request, view))

    def test_staff_without_superuser_denied_without_role(self):
        """
        SRS §6.3: Staff without superuser status cannot access DRF endpoints without dynamic role.
        """
        staff = make_user("drf_staff", is_staff=True, is_superuser=False)
        request = self._make_request(staff)
        view = self._make_view(required_permission="hrms.payroll.run")
        self.assertFalse(self.permission_class.has_permission(request, view))


# ─── 5. Identity API Endpoints ────────────────────────────────────────────────

class LoginViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user("loginuser", user_type=UserType.TEACHER)

    def test_login_success(self):
        resp = self.client.post(
            "/api/v1/identity/login/",
            {"username": "loginuser", "password": "TestPass@999"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "ok")
        self.assertEqual(resp.data["user"]["username"], "loginuser")

    def test_login_wrong_password(self):
        resp = self.client.post(
            "/api/v1/identity/login/",
            {"username": "loginuser", "password": "WRONG"},
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_inactive_user(self):
        self.user.is_active = False
        self.user.save()
        resp = self.client.post(
            "/api/v1/identity/login/",
            {"username": "loginuser", "password": "TestPass@999"},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_login_missing_fields(self):
        resp = self.client.post("/api/v1/identity/login/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_creates_audit_log(self):
        self.client.post(
            "/api/v1/identity/login/",
            {"username": "loginuser", "password": "TestPass@999"},
        )
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.user, action="LOGIN"
            ).exists()
        )


class LogoutViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user("logoutuser")

    def test_logout_success(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post("/api/v1/identity/logout/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_logout_unauthenticated_denied(self):
        resp = self.client.post("/api/v1/identity/logout/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_creates_audit_log(self):
        self.client.force_authenticate(user=self.user)
        self.client.post("/api/v1/identity/logout/")
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.user, action="LOGOUT"
            ).exists()
        )


class PasswordChangeViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user("passchange_user")

    def test_password_change_success(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/api/v1/identity/change-password/",
            {"current_password": "TestPass@999", "new_password": "NewSecure@888"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(check_password("NewSecure@888", self.user.password))
        self.assertFalse(self.user.must_change_password)

    def test_wrong_current_password_rejected(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/api/v1/identity/change-password/",
            {"current_password": "WRONG_PASS", "new_password": "NewSecure@888"},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_same_password_rejected(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/api/v1/identity/change-password/",
            {"current_password": "TestPass@999", "new_password": "TestPass@999"},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_weak_password_rejected(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/api/v1/identity/change-password/",
            {"current_password": "TestPass@999", "new_password": "123"},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class UserMeEndpointTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user("me_user", first_name="Me", last_name="User")

    def test_me_get_returns_own_profile(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/v1/identity/users/me/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["username"], "me_user")

    def test_me_unauthenticated_denied(self):
        resp = self.client.get("/api/v1/identity/users/me/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ─── 6. Permission Registry & sync_permissions Command ───────────────────────

class SyncPermissionsCommandTests(TestCase):

    def test_sync_creates_all_registry_permissions(self):
        out = StringIO()
        call_command("sync_permissions", stdout=out)
        # Every registry entry must now exist in the database
        for entry in PERMISSION_REGISTRY:
            self.assertTrue(
                Permission.objects.filter(codename=entry["codename"]).exists(),
                f"Permission '{entry['codename']}' was not created by sync.",
            )

    def test_sync_is_idempotent(self):
        """Running twice must not raise errors or duplicate records."""
        call_command("sync_permissions", stdout=StringIO())
        call_command("sync_permissions", stdout=StringIO())
        for entry in PERMISSION_REGISTRY:
            count = Permission.objects.filter(codename=entry["codename"]).count()
            self.assertEqual(
                count,
                1,
                f"Duplicate permission '{entry['codename']}' after re-sync.",
            )

    def test_sync_does_not_delete_existing_custom_permissions(self):
        """Permissions not in the registry must NOT be deleted by sync."""
        custom_perm = make_permission("custom.my.perm", module="analytics", action="view")
        call_command("sync_permissions", stdout=StringIO())
        self.assertTrue(
            Permission.objects.filter(codename="custom.my.perm").exists(),
            "sync_permissions deleted a permission not in the registry.",
        )

    def test_sync_does_not_delete_role_assignments(self):
        """Syncing must not affect existing role assignments."""
        call_command("sync_permissions", stdout=StringIO())
        perm = Permission.objects.get(codename="students.profile.view")
        role = make_role("test-role", permissions=[perm])
        user = make_user("sync_test_user")
        assign_role(user, role)
        # Re-sync
        call_command("sync_permissions", stdout=StringIO())
        # The role assignment must still exist
        self.assertTrue(
            UserRoleAssignment.objects.filter(user=user, role=role, is_active=True).exists()
        )

    def test_registry_all_codenames_are_unique(self):
        codenames = [e["codename"] for e in PERMISSION_REGISTRY]
        self.assertEqual(len(codenames), len(set(codenames)), "Duplicate codenames in registry!")


# ─── 7. RBAC API Endpoint Tests ───────────────────────────────────────────────

class RBACAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        # Create a user with rbac management permissions
        self.manager_perm = make_permission("rbac.role.view", "rbac", "view")
        self.manage_perm = make_permission("rbac.role.manage", "rbac", "add")
        self.delete_perm = make_permission("rbac.role.delete", "rbac", "delete")
        self.assign_perm = make_permission("users.membership.manage", "rbac", "approve")
        self.manager_role = make_role(
            "rbac-manager",
            permissions=[self.manager_perm, self.manage_perm, self.delete_perm, self.assign_perm],
        )
        self.manager = make_user("rbac_manager")
        assign_role(self.manager, self.manager_role)
        self.system_role = make_role("system-principal", is_system_role=True)

    def test_system_role_cannot_be_deleted_via_api(self):
        self.client.force_authenticate(user=self.manager)
        resp = self.client.delete(f"/api/v1/rbac/roles/{self.system_role.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Role.objects.filter(pk=self.system_role.pk).exists())

    def test_list_roles_requires_auth(self):
        resp = self.client.get("/api/v1/rbac/roles/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_my_permissions_endpoint(self):
        self.client.force_authenticate(user=self.manager)
        resp = self.client.get("/api/v1/rbac/assignments/my-permissions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("permissions", resp.data)
        self.assertIn("rbac.role.view", resp.data["permissions"])

    def test_create_role_creates_audit_log(self):
        self.client.force_authenticate(user=self.manager)
        resp = self.client.post(
            "/api/v1/rbac/roles/",
            {"code": "test-audit-role", "name": "Test Audit Role", "description": ""},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.manager, action="CREATE", entity_type="Role"
            ).exists()
        )


# ─── 8. Web Auth View Tests (HTML Templates) ──────────────────────────────────

class WebAuthViewTests(TestCase):

    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.user = make_user("teacher_web", user_type=UserType.TEACHER)

    def test_login_page_renders_get(self):
        resp = self.client.get("/login/")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "auth/login.html")
        self.assertContains(resp, "Sign In to Portal")

    def test_login_post_success_redirects_to_dashboard(self):
        resp = self.client.post("/login/", {
            "username": "teacher_web",
            "password": "TestPass@999",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

    def test_login_post_with_email_success(self):
        resp = self.client.post("/login/", {
            "username": "teacher_web@school.test",
            "password": "TestPass@999",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

    def test_login_post_invalid_password_shows_error(self):
        resp = self.client.post("/login/", {
            "username": "teacher_web",
            "password": "WrongPassword",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid credentials")

    def test_login_page_redirects_if_already_authenticated(self):
        self.client.login(username="teacher_web", password="TestPass@999")
        resp = self.client.get("/login/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

    def test_login_redirects_to_next_url(self):
        resp = self.client.post("/login/?next=/api/v1/", {
            "username": "teacher_web",
            "password": "TestPass@999",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/api/v1/")

    def test_logout_redirects_to_login(self):
        self.client.login(username="teacher_web", password="TestPass@999")
        resp = self.client.get("/logout/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/login/")

    def test_dashboard_requires_login(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

