"""
Management command: sync_permissions

Synchronises the code-level PERMISSION_REGISTRY into the database.

Design decisions:
  - Idempotent: safe to run on every deploy or at startup.
  - Uses update_or_create keyed on `codename` so descriptions/names are refreshed.
  - Never deletes permissions that are NOT in the registry (to preserve existing
    role-permission assignments made by admins for custom permissions).
  - Prints a clear summary of created, updated, and already-current records.
  - Does NOT touch Role or UserRoleAssignment tables.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.rbac.models import Permission, ModuleCode, ActionCode
from apps.rbac.registry import PERMISSION_REGISTRY


class Command(BaseCommand):
    help = "Synchronise the PERMISSION_REGISTRY into the database (idempotent)."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        skipped_count = 0

        # Validate registry entries against allowed choices before touching DB
        valid_modules = {c[0] for c in ModuleCode.choices}
        valid_actions = {c[0] for c in ActionCode.choices}

        errors = []
        for entry in PERMISSION_REGISTRY:
            if entry["module"] not in valid_modules:
                errors.append(
                    f"  ✗ '{entry['codename']}': module '{entry['module']}' is not a valid ModuleCode."
                )
            if entry["action"] not in valid_actions:
                errors.append(
                    f"  ✗ '{entry['codename']}': action '{entry['action']}' is not a valid ActionCode."
                )

        if errors:
            self.stderr.write(self.style.ERROR("Registry validation failed:"))
            for err in errors:
                self.stderr.write(err)
            raise SystemExit(1)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Syncing {len(PERMISSION_REGISTRY)} permissions from registry..."
            )
        )

        with transaction.atomic():
            for entry in PERMISSION_REGISTRY:
                codename = entry["codename"]
                defaults = {
                    "module": entry["module"],
                    "action": entry["action"],
                    "name": entry["name"],
                    "description": entry.get("description", ""),
                    "is_active": True,
                }

                obj, created = Permission.objects.update_or_create(
                    codename=codename,
                    defaults=defaults,
                )

                if created:
                    created_count += 1
                    self.stdout.write(
                        f"  {self.style.SUCCESS('+ Created')}  [{obj.module}] {codename}"
                    )
                else:
                    # Check if anything actually changed
                    changed = any(
                        getattr(obj, field) != value
                        for field, value in defaults.items()
                    )
                    if changed:
                        updated_count += 1
                        self.stdout.write(
                            f"  {self.style.WARNING('~ Updated')}  [{obj.module}] {codename}"
                        )
                    else:
                        skipped_count += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {created_count} | Updated: {updated_count} | Unchanged: {skipped_count}"
            )
        )

