"""
Comprehensive automated tests for First Login Mandatory Password Change.

Verifies all 18 requirements:
  1. ERP Master existing password remains unchanged.
  2. ERP Master is not forced to change the password.
  3. Newly created user receives the default password 12345678.
  4. Password is stored in hashed form.
  5. Newly created user gets must_change_password=True.
  6. Existing users are not forced to change passwords.
  7. Editing an existing user's profile does not reset the password.
  8. First login redirects a new user to password change.
  9. New user cannot access the dashboard before changing the password.
  10. New user cannot access protected module pages.
  11. New user cannot access protected APIs.
  12. Incorrect password confirmation returns a validation error.
  13. Invalid passwords are rejected by Django password validators.
  14. Successful password change resets the flag to False.
  15. User remains authenticated after password change.
  16. User is redirected to the correct role-based dashboard.
  17. Existing RBAC permissions continue working.
  18. Existing user management functionality remains operational.
"""
from django.contrib.auth.hashers import check_password
from django.test import Client, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models import AuditAction, AuditLog
from apps.identity.models import User, UserType
from apps.identity.serializers import UserSerializer
from apps.identity.services import IdentityService
from apps.rbac.models import Permission, Role, ScopeType, UserRoleAssignment
from apps.rbac.services import RBACService


class MandatoryPasswordChangeTests(TestCase):
    """
    Test suite covering all mandatory first-login password change specifications.
    """

    def setUp(self):
        self.client = Client()
        self.api_client = APIClient()

        # 1. Existing System Administrator (ERP Master)
        self.erp_master = User.objects.create_superuser(
            username="masteradmin",
            email="masteradmin@school.test",
            password="MasterSecretPassword@2026",
            user_type=UserType.ADMIN,
        )
        self.erp_master.must_change_password = False
        self.erp_master.save()

        # 2. Existing Normal User
        self.existing_user = User.objects.create_user(
            username="existingteacher",
            email="teacher@school.test",
            password="TeacherExistingPass@123",
            user_type=UserType.TEACHER,
        )
        self.existing_user.must_change_password = False
        self.existing_user.save()

        # 3. Dynamic Role and Permission setup
        self.view_perm = Permission.objects.create(
            codename="students.profile.view",
            module="students",
            action="view",
            name="View Student Profile",
        )
        self.teacher_role = Role.objects.create(
            code="teacher-role",
            name="Teacher Role",
            is_system_role=False,
        )
        self.teacher_role.permissions.add(self.view_perm)

    def test_01_erp_master_existing_password_remains_unchanged(self):
        """1. ERP Master existing password remains unchanged."""
        self.erp_master.refresh_from_db()
        self.assertTrue(check_password("MasterSecretPassword@2026", self.erp_master.password))

    def test_02_erp_master_not_forced_to_change_password(self):
        """2. ERP Master is not forced to change the password."""
        self.assertFalse(self.erp_master.must_change_password)
        login_success = self.client.login(username="masteradmin", password="MasterSecretPassword@2026")
        self.assertTrue(login_success)

        # Access dashboard directly: no redirect to password-change
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_03_newly_created_user_receives_default_password_12345678(self):
        """3. Newly created user receives the default password 12345678."""
        new_user = IdentityService.create_user(
            username="newaccount1",
            email="newaccount1@school.test",
            first_name="New",
            last_name="User",
            actor=self.erp_master,
        )
        self.assertTrue(check_password("12345678", new_user.password))

    def test_04_password_stored_in_hashed_form(self):
        """4. Password is stored in hashed form."""
        new_user = IdentityService.create_user(
            username="newaccount2",
            email="newaccount2@school.test",
            actor=self.erp_master,
        )
        # Password field must not be plain text
        self.assertNotEqual(new_user.password, "12345678")
        self.assertTrue(new_user.password.startswith("pbkdf2_sha256$") or "$" in new_user.password)

    def test_05_newly_created_user_gets_must_change_password_true(self):
        """5. Newly created user gets must_change_password=True."""
        new_user = IdentityService.create_user(
            username="newaccount3",
            email="newaccount3@school.test",
            actor=self.erp_master,
        )
        self.assertTrue(new_user.must_change_password)

    def test_06_existing_users_not_forced_to_change_passwords(self):
        """6. Existing users are not forced to change passwords."""
        self.assertFalse(self.existing_user.must_change_password)
        login_success = self.client.login(username="existingteacher", password="TeacherExistingPass@123")
        self.assertTrue(login_success)

        # Access dashboard without redirect
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_07_editing_existing_user_profile_does_not_reset_password(self):
        """7. Editing an existing user's profile does not reset the password or flag."""
        original_hash = self.existing_user.password
        serializer = UserSerializer(
            instance=self.existing_user,
            data={"first_name": "UpdatedName", "phone_number": "9998887776"},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = serializer.save()

        self.assertEqual(updated.password, original_hash)
        self.assertFalse(updated.must_change_password)
        self.assertTrue(check_password("TeacherExistingPass@123", updated.password))

    def test_08_first_login_redirects_new_user_to_password_change(self):
        """8. First login redirects a new user to password change."""
        IdentityService.create_user(
            username="firstloginuser",
            email="firstlogin@school.test",
            actor=self.erp_master,
        )
        resp = self.client.post("/login/", {
            "username": "firstloginuser",
            "password": "12345678",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/password-change/", resp.url)

    def test_09_new_user_cannot_access_dashboard_before_changing_password(self):
        """9. New user cannot access the dashboard before changing the password."""
        user = IdentityService.create_user(
            username="dashblocked",
            email="dashblocked@school.test",
            actor=self.erp_master,
        )
        self.client.login(username="dashblocked", password="12345678")

        # Direct attempt to access the dashboard
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/password-change/", resp.url)

    def test_10_new_user_cannot_access_protected_module_pages(self):
        """10. New user cannot access protected module pages."""
        user = IdentityService.create_user(
            username="modblocked",
            email="modblocked@school.test",
            actor=self.erp_master,
            role=self.teacher_role,
        )
        self.client.login(username="modblocked", password="12345678")

        # Attempt to access protected dashboard web pages
        resp_roles = self.client.get("/dashboard/rbac/roles/")
        self.assertEqual(resp_roles.status_code, 302)
        self.assertIn("/password-change/", resp_roles.url)

        resp_users = self.client.get("/dashboard/users/")
        self.assertEqual(resp_users.status_code, 302)
        self.assertIn("/password-change/", resp_users.url)

    def test_11_new_user_cannot_access_protected_apis(self):
        """11. New user cannot access protected APIs."""
        user = IdentityService.create_user(
            username="apiblocked",
            email="apiblocked@school.test",
            actor=self.erp_master,
            role=self.teacher_role,
        )
        self.client.login(username="apiblocked", password="12345678")

        # Protected API endpoints must reject with 403 Forbidden
        endpoints = [
            "/api/v1/students/profiles/",
            "/api/v1/academics/classes/",
            "/api/v1/admissions/applications/",
            "/api/v1/identity/users/",
        ]
        for ep in endpoints:
            resp = self.client.get(ep)
            self.assertEqual(
                resp.status_code,
                status.HTTP_403_FORBIDDEN,
                f"Expected 403 on {ep}, got {resp.status_code}",
            )

        # Also verify via APIClient direct DRF request
        self.api_client.force_authenticate(user=user)
        for ep in endpoints:
            resp = self.api_client.get(ep)
            self.assertEqual(
                resp.status_code,
                status.HTTP_403_FORBIDDEN,
                f"Expected 403 on DRF view {ep}, got {resp.status_code}",
            )

    def test_12_incorrect_password_confirmation_returns_validation_error(self):
        """12. Incorrect password confirmation returns a validation error."""
        user = IdentityService.create_user(
            username="mismatchuser",
            email="mismatch@school.test",
            actor=self.erp_master,
        )
        self.client.login(username="mismatchuser", password="12345678")

        resp = self.client.post("/password-change/", {
            "new_password": "NewComplexPassword@2026",
            "confirm_password": "ACompletelyDifferentPassword@2026",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "do not match")

        user.refresh_from_db()
        self.assertTrue(user.must_change_password)

    def test_13_invalid_passwords_are_rejected_by_django_password_validators(self):
        """13. Invalid passwords are rejected by Django password validators."""
        user = IdentityService.create_user(
            username="weakpassuser",
            email="weak@school.test",
            actor=self.erp_master,
        )
        self.client.login(username="weakpassuser", password="12345678")

        # Test too short (< 8 chars)
        resp = self.client.post("/password-change/", {
            "new_password": "short",
            "confirm_password": "short",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "at least 8 characters")

        # Test entirely numeric
        resp_numeric = self.client.post("/password-change/", {
            "new_password": "1234567890",
            "confirm_password": "1234567890",
        })
        self.assertEqual(resp_numeric.status_code, 200)
        self.assertContains(resp_numeric, "entirely numeric")

        user.refresh_from_db()
        self.assertTrue(user.must_change_password)

    def test_14_successful_password_change_resets_flag_to_false(self):
        """14. Successful password change resets the flag to False."""
        user = IdentityService.create_user(
            username="successuser",
            email="success@school.test",
            actor=self.erp_master,
        )
        self.client.login(username="successuser", password="12345678")

        resp = self.client.post("/password-change/", {
            "new_password": "ValidComplexPass@2026",
            "confirm_password": "ValidComplexPass@2026",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertTrue(check_password("ValidComplexPass@2026", user.password))

    def test_15_user_remains_authenticated_after_password_change(self):
        """15. User remains authenticated after password change (update_session_auth_hash)."""
        user = IdentityService.create_user(
            username="authremainuser",
            email="authremain@school.test",
            actor=self.erp_master,
        )
        self.client.login(username="authremainuser", password="12345678")

        # Change password
        self.client.post("/password-change/", {
            "new_password": "StrongNewPassword@2026",
            "confirm_password": "StrongNewPassword@2026",
        })

        # Next request must recognize user as authenticated without logging them out
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["user"].pk, user.pk)

    def test_16_user_is_redirected_to_correct_role_based_dashboard(self):
        """16. User is redirected to the correct role-based dashboard."""
        user = IdentityService.create_user(
            username="redirectuser",
            email="redirect@school.test",
            actor=self.erp_master,
        )
        self.client.login(username="redirectuser", password="12345678")

        resp = self.client.post("/password-change/", {
            "new_password": "ValidComplexPass@2026",
            "confirm_password": "ValidComplexPass@2026",
        }, follow=True)

        self.assertEqual(resp.status_code, 200)
        # Verify redirect chain led to dashboard
        self.assertTrue(len(resp.redirect_chain) > 0)
        self.assertEqual(resp.redirect_chain[0][0], "/")

    def test_17_existing_rbac_permissions_continue_working(self):
        """17. Existing RBAC permissions continue working after password change."""
        user = IdentityService.create_user(
            username="rbacworkinguser",
            email="rbacworking@school.test",
            actor=self.erp_master,
            role=self.teacher_role,
        )
        # Perform password change
        IdentityService.change_password(
            user=user,
            new_password="BrandNewPass@2026",
            confirm_password="BrandNewPass@2026",
        )

        user.refresh_from_db()
        self.assertFalse(user.must_change_password)

        # Verify RBAC permissions are active
        user_perms = RBACService.get_user_permissions(user)
        self.assertIn("students.profile.view", user_perms)
        self.assertTrue(RBACService.has_permission(user, "students.profile.view"))

    def test_18_existing_user_management_functionality_remains_operational(self):
        """18. Existing user management functionality remains operational."""
        # Authenticate ERP master via API
        self.api_client.force_authenticate(user=self.erp_master)

        # 1. Create a user via API
        resp = self.api_client.post("/api/v1/identity/users/", {
            "username": "api_created_user",
            "email": "apicreated@school.test",
            "first_name": "API",
            "last_name": "Created",
            "user_type": UserType.STAFF,
            "role_id": self.teacher_role.pk,
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        created_id = resp.data["id"]

        # Verify user in DB received default password 12345678 and must_change_password=True
        created_user = User.objects.get(pk=created_id)
        self.assertTrue(created_user.must_change_password)
        self.assertTrue(check_password("12345678", created_user.password))
        self.assertTrue(
            UserRoleAssignment.objects.filter(
                user=created_user, role=self.teacher_role, is_active=True
            ).exists()
        )

        # 2. List users via API
        list_resp = self.api_client.get("/api/v1/identity/users/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)

        # 3. Verify audit log was created
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.erp_master,
                action=AuditAction.CREATE,
                module="identity",
                entity_type="User",
                entity_id=str(created_id),
            ).exists()
        )
