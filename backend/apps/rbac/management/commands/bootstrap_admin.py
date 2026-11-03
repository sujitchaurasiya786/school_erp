"""
Bootstrap the first School ERP System Administrator.

Usage:
    python manage.py bootstrap_admin --username admin --email admin@school.edu

The password is requested interactively and is never written to stdout.
"""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.identity.models import UserType
from apps.rbac.models import Permission, Role, ScopeType, UserRoleAssignment
from apps.rbac.registry import PERMISSION_REGISTRY
from apps.rbac.services import RBACService


SYSTEM_ROLE_CODE = "system-administrator"
SYSTEM_ROLE_NAME = "System Administrator"
REQUIRED_ADMIN_PERMISSIONS = {
    "rbac.role.view",
    "rbac.role.manage",
    "rbac.role.change",
    "rbac.role.delete",
    "users.membership.manage",
}


class Command(BaseCommand):
    help = "Create or attach the initial System Administrator account and role."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the password from stdin (useful for controlled local automation).",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        email = options["email"].strip().lower()
        if not username or not email:
            raise CommandError("Both --username and --email must be non-empty.")

        User = get_user_model()
        existing = User.objects.filter(username=username).first()
        if existing and existing.email.lower() != email:
            raise CommandError(
                "That username already belongs to a different email address. "
                "No account was changed."
            )

        if existing:
            password = None
            if not existing.is_active:
                raise CommandError("The existing account is inactive. Activate it manually first.")
        else:
            if User.objects.filter(email__iexact=email).exists():
                raise CommandError("That email address is already attached to another account.")
            if options["password_stdin"]:
                import sys
                password = sys.stdin.readline().rstrip("\r\n")
            else:
                password = getpass("Set System Administrator password: ")
                confirm = getpass("Confirm password: ")
                if password != confirm:
                    raise CommandError("Passwords do not match.")
            if not password:
                raise CommandError("Password cannot be empty.")
            try:
                validate_password(password)
            except Exception as exc:
                raise CommandError(str(exc)) from exc

        registry_codes = {entry["codename"] for entry in PERMISSION_REGISTRY}
        missing_required = REQUIRED_ADMIN_PERMISSIONS - registry_codes
        if missing_required:
            raise CommandError(
                "Permission registry is missing required bootstrap permissions: "
                + ", ".join(sorted(missing_required))
            )

        with transaction.atomic():
            # Keep registry records current before building the protected role.
            from django.core.management import call_command
            call_command("sync_permissions", verbosity=0)

            role, _ = Role.objects.get_or_create(
                code=SYSTEM_ROLE_CODE,
                defaults={
                    "name": SYSTEM_ROLE_NAME,
                    "description": (
                        "Protected technical administrator role. It excludes "
                        "finance, payroll, and confidential grievance access."
                    ),
                    "is_system_role": True,
                    "is_active": True,
                },
            )
            if role.name != SYSTEM_ROLE_NAME:
                raise CommandError(
                    f"Role code '{SYSTEM_ROLE_CODE}' is already used by '{role.name}'."
                )

            role.name = SYSTEM_ROLE_NAME
            role.is_system_role = True
            role.is_active = True
            role.save(update_fields=["name", "is_system_role", "is_active", "updated_at"])

            # The System Administrator receives technical permissions only.
            # Finance is excluded in full; payroll and confidential grievances
            # are excluded explicitly. These permissions require separate grants.
            safe_permissions = Permission.objects.filter(is_active=True).exclude(
                module="finance"
            ).exclude(
                codename__in={
                    "hrms.payroll.view",
                    "hrms.payroll.run",
                    "helpdesk.grievance.view",
                }
            )
            role.permissions.set(safe_permissions)

            if existing:
                user = existing
                user.user_type = UserType.ADMIN
                user.save(update_fields=["user_type", "updated_at"])
                created_user = False
            else:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=options["first_name"].strip(),
                    last_name=options["last_name"].strip(),
                    user_type=UserType.ADMIN,
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                )
                created_user = True

            assignment, assignment_created = UserRoleAssignment.objects.get_or_create(
                user=user,
                role=role,
                scope_type=ScopeType.GLOBAL,
                scope_id="",
                defaults={"is_active": True},
            )
            if not assignment.is_active:
                assignment.is_active = True
                assignment.save(update_fields=["is_active", "updated_at"])

        self.stdout.write(self.style.SUCCESS(
            f"System Administrator {'created' if created_user else 'linked'} successfully."
        ))
        self.stdout.write(f"Username: {user.username}")
        self.stdout.write(f"Role: {role.name}")
        self.stdout.write(
            f"Role assignment: {'created' if assignment_created else 'already existed'}"
        )
        self.stdout.write(
            "Sensitive access excluded: finance, payroll, confidential grievances."
        )
        self.stdout.write(
            "Django is_superuser remains False; access is controlled by ERP RBAC."
        )
