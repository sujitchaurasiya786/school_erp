from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.identity.models import User, UserType
from apps.rbac.forms import RoleForm, UserRoleAssignmentForm
from apps.rbac.models import Permission, Role, ScopeType, UserRoleAssignment
from apps.rbac.services import RBACService


def make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@school.test",
        password="TestPass@999",
    )


def make_permission(code, module, action):
    return Permission.objects.create(
        codename=code, module=module, action=action, name=code
    )


class BootstrapAdminCommandTests(TestCase):
    def test_bootstrap_creates_non_superuser_with_safe_system_role(self):
        password = "Long-Unique-Admin-Password-987!"
        with patch("sys.stdin", StringIO(password + "\n")):
            call_command(
                "bootstrap_admin",
                username="erp-admin",
                email="erp-admin@school.test",
                password_stdin=True,
                stdout=StringIO(),
            )

        admin = User.objects.get(username="erp-admin")
        self.assertEqual(admin.user_type, UserType.ADMIN)
        self.assertFalse(admin.is_superuser)
        self.assertFalse(admin.is_staff)
        self.assertTrue(RBACService.is_system_administrator(admin))
        self.assertTrue(RBACService.has_permission(admin, "rbac.role.manage"))
        self.assertTrue(RBACService.has_permission(admin, "users.membership.manage"))
        self.assertFalse(RBACService.has_permission(admin, "finance.invoice.view"))
        self.assertFalse(RBACService.has_permission(admin, "hrms.payroll.view"))
        self.assertFalse(RBACService.has_permission(admin, "helpdesk.grievance.view"))

    def test_bootstrap_is_idempotent_for_existing_account(self):
        admin = make_user("existing-admin")
        call_command(
            "bootstrap_admin",
            username="existing-admin",
            email=admin.email,
            stdout=StringIO(),
        )
        original_id = admin.pk
        call_command(
            "bootstrap_admin",
            username="existing-admin",
            email=admin.email,
            stdout=StringIO(),
        )
        self.assertEqual(User.objects.filter(username="existing-admin").count(), 1)
        self.assertEqual(User.objects.get(username="existing-admin").pk, original_id)


class RBACScopeAndGrantTests(TestCase):
    def setUp(self):
        self.user = make_user("scoped-user")
        self.permission = make_permission(
            "attendance.record.view", "attendance", "view"
        )

    def test_scope_filters_global_and_matching_assignment_only(self):
        global_role = Role.objects.create(code="global-view", name="Global View")
        matching_role = Role.objects.create(code="section-a", name="Section A")
        other_role = Role.objects.create(code="section-b", name="Section B")
        for role in (global_role, matching_role, other_role):
            role.permissions.add(self.permission)

        UserRoleAssignment.objects.create(
            user=self.user, role=global_role, scope_type=ScopeType.GLOBAL, scope_id=""
        )
        UserRoleAssignment.objects.create(
            user=self.user, role=matching_role, scope_type=ScopeType.SECTION, scope_id="A"
        )
        UserRoleAssignment.objects.create(
            user=self.user, role=other_role, scope_type=ScopeType.SECTION, scope_id="B"
        )

        permissions = RBACService.get_user_permissions(
            self.user, scope_type=ScopeType.SECTION, scope_id="A"
        )
        self.assertIn("attendance.record.view", permissions)

        # Deactivate global and matching assignments: section B must not leak in.
        UserRoleAssignment.objects.filter(
            user=self.user, role__in=[global_role, matching_role]
        ).update(is_active=False)
        self.assertNotIn(
            "attendance.record.view",
            RBACService.get_user_permissions(
                self.user, scope_type=ScopeType.SECTION, scope_id="A"
            ),
        )

    def test_actor_cannot_assign_role_with_unheld_permission(self):
        actor = make_user("role-manager")
        manage = make_permission("users.membership.manage", "rbac", "approve")
        held_role = Role.objects.create(code="membership-manager", name="Membership Manager")
        held_role.permissions.add(manage)
        UserRoleAssignment.objects.create(user=actor, role=held_role)

        restricted = make_permission("finance.payment.record", "finance", "add")
        target_role = Role.objects.create(code="finance-role", name="Finance Role")
        target_role.permissions.add(restricted)
        target = make_user("new-staff")

        form = UserRoleAssignmentForm(
            data={
                "user": target.pk,
                "role": target_role.pk,
                "scope_type": "GLOBAL",
                "scope_id": "",
            },
            actor=actor,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("role", form.errors)

    def test_actor_cannot_grant_permission_they_do_not_hold(self):
        actor = make_user("limited-manager")
        manage = make_permission("rbac.role.manage", "rbac", "add")
        view = make_permission("rbac.role.view", "rbac", "view")
        held_role = Role.objects.create(code="limited-rbac", name="Limited RBAC")
        held_role.permissions.add(manage, view)
        UserRoleAssignment.objects.create(user=actor, role=held_role)

        finance = make_permission("finance.payment.record", "finance", "add")
        form = RoleForm(
            data={
                "name": "New Finance Role",
                "code": "new-finance-role",
                "description": "",
                "permissions": [finance.pk],
            },
            actor=actor,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("permissions", form.errors)
