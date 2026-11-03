"""
Management command: bootstrap_system_admin

Initialises the RBAC foundation for the School ERP System:
  1. Synchronises the code-defined PERMISSION_REGISTRY into the MySQL database.
  2. Bootstraps standard system roles (System Administrator, Principal, Teacher, Accountant, Parent, Student)
     with appropriate baseline permissions strictly respecting SRS §3.7 (no auto-payroll/grievance to admin).
  3. Creates or updates the primary System Administrator user account.
  4. Assigns the `system_admin` role to the admin user.

Usage:
  python manage.py bootstrap_system_admin
  python manage.py bootstrap_system_admin --username admin --email admin@school.local --password "Secret@123" --no-input
"""

import getpass
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.rbac.models import Role, Permission, UserRoleAssignment, ScopeType
from apps.rbac.registry import PERMISSION_REGISTRY
from apps.rbac.management.commands.sync_permissions import Command as SyncPermissionsCommand
from apps.identity.models import UserType

User = get_user_model()


class Command(BaseCommand):
    help = "Bootstrap RBAC permission registry, system roles, and initial System Administrator user."

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            default='admin',
            help='Username for the initial System Administrator account (default: admin)'
        )
        parser.add_argument(
            '--email',
            type=str,
            default='admin@school.local',
            help='Email for the initial System Administrator account (default: admin@school.local)'
        )
        parser.add_argument(
            '--password',
            type=str,
            default=None,
            help='Password for the initial System Administrator account'
        )
        parser.add_argument(
            '--first-name',
            type=str,
            default='System',
            help='First name for the administrator'
        )
        parser.add_argument(
            '--last-name',
            type=str,
            default='Administrator',
            help='Last name for the administrator'
        )
        parser.add_argument(
            '--no-input',
            action='store_true',
            help='Run non-interactively without prompting for password'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))
        self.stdout.write(self.style.MIGRATE_HEADING("   SCHOOL ERP — RBAC & SYSTEM ADMINISTRATOR BOOTSTRAP"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))

        # ------------------------------------------------------------------
        # Step 1: Synchronise Permissions Registry into Database
        # ------------------------------------------------------------------
        self.stdout.write("\n[1/4] Synchronising Permission Registry into MySQL...")
        sync_cmd = SyncPermissionsCommand()
        sync_cmd.stdout = self.stdout
        sync_cmd.stderr = self.stderr
        sync_cmd.handle()

        all_permissions = {p.codename: p for p in Permission.objects.filter(is_active=True)}
        self.stdout.write(self.style.SUCCESS(f"  ✓ {len(all_permissions)} active permissions loaded."))

        # ------------------------------------------------------------------
        # Step 2: Bootstrap Baseline System Roles
        # ------------------------------------------------------------------
        self.stdout.write("\n[2/4] Bootstrapping System Roles and Permissions...")

        # System roles definition per SRS specifications
        roles_config = [
            {
                "code": "system_admin",
                "name": "System Administrator",
                "description": (
                    "Super administrative role with access to identity, RBAC, school configuration, "
                    "audit logs, and general setup. Note: Per SRS §3.7, payroll and confidential grievances "
                    "are NOT granted to this role."
                ),
                # System Administrator gets almost all system permissions EXCEPT payroll & confidential grievances
                "perm_filter": lambda codename, p: (
                    not codename.startswith("hrms.payroll.")
                    and codename != "helpdesk.grievance.view"
                ),
            },
            {
                "code": "principal",
                "name": "Principal",
                "description": (
                    "Executive academic and administrative head. Oversees admissions, students, "
                    "curriculum, attendance, examinations, promotions, reports, and grievances."
                ),
                "perm_filter": lambda codename, p: (
                    p.module in [
                        "admissions", "students", "academics", "attendance",
                        "examinations", "analytics", "school_config", "audit"
                    ]
                    or codename in [
                        "hrms.staff.view", "hrms.staff.manage", "hrms.leave.approve",
                        "helpdesk.ticket.view", "helpdesk.ticket.manage", "helpdesk.grievance.view",
                        "identity.users.view", "rbac.role.view"
                    ]
                ),
            },
            {
                "code": "teacher",
                "name": "Teacher",
                "description": (
                    "Teaching faculty role. Permitted to mark attendance, enter examination marks, "
                    "view timetables, and access student basic profiles."
                ),
                "perm_filter": lambda codename, p: codename in [
                    "attendance.record.view",
                    "attendance.record.mark",
                    "exams.marks.view",
                    "exams.marks.enter",
                    "academics.timetable.view",
                    "students.profile.view",
                    "library.catalog.view",
                    "helpdesk.ticket.view",
                ],
            },
            {
                "code": "accountant",
                "name": "Accountant",
                "description": (
                    "Finance and accounts officer. Manages fee structures, student invoices, offline payments, "
                    "concessions, refunds, financial reports, and payroll runs."
                ),
                "perm_filter": lambda codename, p: (
                    p.module == "finance"
                    or codename in [
                        "hrms.payroll.view",
                        "hrms.payroll.run",
                        "analytics.reports.view",
                        "analytics.reports.export",
                        "students.profile.view",
                    ]
                ),
            },
            {
                "code": "parent",
                "name": "Parent",
                "description": (
                    "Parent/Guardian portal access for viewing ward profile, attendance, and fee invoices."
                ),
                "perm_filter": lambda codename, p: codename in [
                    "students.profile.view",
                    "attendance.record.view",
                    "finance.invoice.view",
                    "exams.marks.view",
                    "academics.timetable.view",
                    "helpdesk.ticket.view",
                ],
            },
            {
                "code": "student",
                "name": "Student",
                "description": (
                    "Student portal access for viewing timetable, daily attendance, and examination marks."
                ),
                "perm_filter": lambda codename, p: codename in [
                    "academics.timetable.view",
                    "attendance.record.view",
                    "exams.marks.view",
                    "library.catalog.view",
                ],
            },
        ]

        created_roles = {}
        with transaction.atomic():
            for rdata in roles_config:
                role, created = Role.objects.get_or_create(
                    code=rdata["code"],
                    defaults={
                        "name": rdata["name"],
                        "description": rdata["description"],
                        "is_system_role": True,
                        "is_active": True,
                    }
                )
                if not created:
                    role.name = rdata["name"]
                    role.description = rdata["description"]
                    role.is_system_role = True
                    role.is_active = True
                    role.save()

                # Calculate assigned permissions
                selected_perms = [
                    p for codename, p in all_permissions.items()
                    if rdata["perm_filter"](codename, p)
                ]
                role.permissions.set(selected_perms)
                created_roles[rdata["code"]] = role

                status_tag = self.style.SUCCESS("[CREATED]") if created else self.style.WARNING("[UPDATED]")
                self.stdout.write(
                    f"  {status_tag} Role: {role.name:<24} | Permissions: {len(selected_perms):>2} assigned"
                )

        # ------------------------------------------------------------------
        # Step 3: Create or Update System Administrator User Account
        # ------------------------------------------------------------------
        self.stdout.write("\n[3/4] Setting up Administrator user account...")

        username = options["username"].strip()
        email = options["email"].strip()
        first_name = options["first_name"].strip()
        last_name = options["last_name"].strip()
        password = options.get("password")

        if not password:
            if options.get("no_input"):
                password = "Admin@123456"
                self.stdout.write(
                    self.style.WARNING(f"  ! Non-interactive mode: Using default initial password: '{password}'")
                )
            else:
                try:
                    password = getpass.getpass(f"  Enter password for '{username}' (leave empty for default 'Admin@123456'): ")
                except Exception:
                    password = ""
                if not password:
                    password = "Admin@123456"

        with transaction.atomic():
            admin_user = User.objects.filter(username__iexact=username).first()
            user_created = False
            if not admin_user:
                # Also check by email
                admin_user = User.objects.filter(email__iexact=email).first()

            if not admin_user:
                admin_user = User.objects.create(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    user_type=UserType.ADMIN,
                    is_staff=True,
                    is_superuser=True,
                    is_active=True,
                )
                user_created = True
            else:
                admin_user.username = username
                admin_user.email = email
                admin_user.first_name = first_name
                admin_user.last_name = last_name
                admin_user.user_type = UserType.ADMIN
                admin_user.is_staff = True
                admin_user.is_superuser = True
                admin_user.is_active = True

            admin_user.set_password(password)
            admin_user.save()

            user_status = self.style.SUCCESS("[CREATED]") if user_created else self.style.WARNING("[UPDATED]")
            self.stdout.write(
                f"  {user_status} User: {admin_user.username} <{admin_user.email}> (Active, Staff, Superuser)"
            )

            # ------------------------------------------------------------------
            # Step 4: Assign `system_admin` role to user
            # ------------------------------------------------------------------
            self.stdout.write("\n[4/4] Assigning 'System Administrator' dynamic role to user...")
            sys_admin_role = created_roles["system_admin"]

            assignment, assign_created = UserRoleAssignment.objects.update_or_create(
                user=admin_user,
                role=sys_admin_role,
                scope_type=ScopeType.GLOBAL,
                defaults={
                    "scope_id": "",
                    "is_active": True,
                }
            )

            assign_status = self.style.SUCCESS("[ASSIGNED]") if assign_created else self.style.SUCCESS("[VERIFIED]")
            self.stdout.write(
                f"  {assign_status} {admin_user.username} -> {sys_admin_role.name} (Scope: GLOBAL, Active)"
            )

        # ------------------------------------------------------------------
        # Final Summary
        # ------------------------------------------------------------------
        self.stdout.write("\n" + self.style.MIGRATE_HEADING("=" * 70))
        self.stdout.write(self.style.SUCCESS("   RBAC & SYSTEM ADMINISTRATOR BOOTSTRAP COMPLETE!"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))
        self.stdout.write(f"  • Registered Permissions : {len(all_permissions)}")
        self.stdout.write(f"  • Configured System Roles: {len(created_roles)}")
        self.stdout.write(f"  • Administrator Username : {admin_user.username}")
        self.stdout.write(f"  • Administrator Email    : {admin_user.email}")
        self.stdout.write(f"  • Administrator Password : {password if options.get('password') or options.get('no_input') else '********'}")
        self.stdout.write(f"  • Web Login URL          : http://127.0.0.1:8000/login/")
        self.stdout.write(f"  • Roles Dashboard URL    : http://127.0.0.1:8000/dashboard/rbac/roles/")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70) + "\n")
