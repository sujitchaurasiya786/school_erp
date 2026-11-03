"""
Django Admin registration for the rbac app.
"""
from django.contrib import admin

from apps.rbac.models import Permission, Role, UserRoleAssignment


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("codename", "module", "action", "name", "is_active")
    list_filter = ("module", "action", "is_active")
    search_fields = ("codename", "name", "description")
    ordering = ("module", "action", "name")
    readonly_fields = ("created_at", "updated_at")


class PermissionInline(admin.TabularInline):
    model = Role.permissions.through
    extra = 0
    verbose_name = "Permission"
    verbose_name_plural = "Permissions"


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_system_role", "is_active", "created_at")
    list_filter = ("is_system_role", "is_active")
    search_fields = ("name", "code", "description")
    readonly_fields = ("created_at", "updated_at")
    filter_horizontal = ("permissions",)

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of system roles from Django Admin too."""
        if obj and obj.is_system_role:
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        """System roles are changed only through the protected RBAC workflow."""
        if obj and obj.is_system_role:
            return False
        return super().has_change_permission(request, obj)


@admin.register(UserRoleAssignment)
class UserRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "scope_type", "scope_id", "is_active", "created_at")
    list_filter = ("scope_type", "is_active", "role")
    search_fields = ("user__username", "user__email", "role__name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user", "role")

