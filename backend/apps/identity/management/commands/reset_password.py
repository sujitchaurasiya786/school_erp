"""
Management command: reset_password

Allows setting or resetting a user's password directly from the command line.
Usage:
    python manage.py reset_password <username> <new_password>
"""
from django.core.management.base import BaseCommand, CommandError
from apps.identity.models import User


class Command(BaseCommand):
    help = "Reset or set password for a specific user."

    def add_arguments(self, parser):
        parser.add_argument("username", type=str, help="Username or email of the user")
        parser.add_argument("password", type=str, help="New password to set")

    def handle(self, *args, **options):
        identifier = options["username"].strip()
        new_password = options["password"]

        from django.db.models import Q
        user = User.objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)
        ).first()

        if not user:
            raise CommandError(f"User with username or email '{identifier}' does not exist.")

        user.set_password(new_password)
        user.is_active = True
        user.save(update_fields=["password", "is_active", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully updated password for user '{user.username}' (ID: {user.id}, Type: {user.user_type}). Account is active."
            )
        )
