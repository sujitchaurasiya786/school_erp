"""
Django Admin registration for the audit app.
"""
from django.contrib import admin

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "actor_username",
        "action",
        "module",
        "entity_type",
        "entity_id",
        "ip_address",
    )
    list_filter = ("action", "module", "entity_type")
    search_fields = ("actor_username", "entity_id", "reason")
    readonly_fields = (
        "actor",
        "actor_username",
        "action",
        "module",
        "entity_type",
        "entity_id",
        "ip_address",
        "user_agent",
        "reason",
        "changes",
        "created_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        """Audit logs are immutable — never allow manual creation via admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Audit logs are immutable — never allow editing."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Audit logs are immutable — never allow deletion."""
        return False

