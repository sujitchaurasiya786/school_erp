"""
Management command: list_users

Lists all user accounts currently registered in the database,
displaying username, email, user_type, is_active, is_superuser, and password status.
"""
from django.core.management.base import BaseCommand
from apps.identity.models import User


class Command(BaseCommand):
    help = "Lists all registered users in the database."

    def handle(self, *args, **options):
        users = User.objects.all().order_by("-date_joined")
        if not users.exists():
            self.stdout.write(self.style.WARNING("No users found in the database."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"Found {users.count()} user(s) in the database:\n"))
        self.stdout.write(
            f"{'ID':<6} {'Username':<18} {'Email':<28} {'User Type':<16} {'Active':<8} {'Superuser':<10} {'Password Set'}"
        )
        self.stdout.write("-" * 100)

        for u in users:
            has_usable = "Yes" if u.has_usable_password() else "No (Unusable/Empty)"
            is_active_str = "Yes" if u.is_active else "NO"
            is_super_str = "Yes" if u.is_superuser else "No"
            self.stdout.write(
                f"{u.id:<6} {u.username:<18} {u.email:<28} {u.user_type:<16} {is_active_str:<8} {is_super_str:<10} {has_usable}"
            )
        self.stdout.write("-" * 100)
