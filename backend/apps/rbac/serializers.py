from rest_framework import serializers

from apps.rbac.models import Role, Permission, UserRoleAssignment
from apps.rbac.services import RBACService


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "module", "action", "codename", "name", "description"]


class RoleSerializer(serializers.ModelSerializer):
    permissions_count = serializers.IntegerField(source="permissions.count", read_only=True)
    permission_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Permission.objects.filter(is_active=True),
        source="permissions",
        write_only=True,
        required=False,
    )
    permissions = PermissionSerializer(many=True, read_only=True)

    class Meta:
        model = Role
        fields = [
            "id", "code", "name", "description", "is_system_role",
            "is_active", "permissions_count", "permissions", "permission_ids",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "is_system_role", "created_at", "updated_at"]

    def validate(self, attrs):
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        instance = self.instance

        if instance and instance.is_system_role and not RBACService.is_system_administrator(actor):
            raise serializers.ValidationError("Only a System Administrator can edit protected system roles.")
        if instance and instance.is_system_role and attrs.get("is_active") is False:
            raise serializers.ValidationError({"is_active": "Protected system roles cannot be deactivated."})

        selected = attrs.get("permissions")
        if selected is not None and actor and not RBACService.is_system_administrator(actor):
            held = RBACService.get_user_permissions(actor)
            existing_ids = set(instance.permissions.values_list("id", flat=True)) if instance else set()
            newly_granted = [permission for permission in selected if permission.pk not in existing_ids]
            if any(permission.codename not in held for permission in newly_granted):
                raise serializers.ValidationError(
                    {"permission_ids": "You cannot grant permissions that you do not currently hold."}
                )

        if instance and selected is not None:
            selected_codes = {permission.codename for permission in selected}
            if instance.code == "system-administrator":
                required = {
                    "rbac.role.view", "rbac.role.manage", "rbac.role.change",
                    "rbac.role.delete", "users.membership.manage",
                }
                if not required.issubset(selected_codes):
                    raise serializers.ValidationError(
                        {"permission_ids": "The System Administrator role must retain its role-management permissions."}
                    )

            currently_manager = instance.permissions.filter(
                codename="users.membership.manage", is_active=True
            ).exists()
            if currently_manager and "users.membership.manage" not in selected_codes:
                from apps.rbac.models import UserRoleAssignment
                another_manager = UserRoleAssignment.objects.filter(
                    is_active=True, role__is_active=True, user__is_active=True,
                    role__permissions__codename="users.membership.manage",
                ).exclude(role=instance).exists()
                if not another_manager:
                    raise serializers.ValidationError(
                        {"permission_ids": "This is the last active role manager; keep membership-management permission."}
                    )
        return attrs


class UserRoleAssignmentSerializer(serializers.ModelSerializer):
    role_name = serializers.ReadOnlyField(source="role.name")
    username = serializers.ReadOnlyField(source="user.username")

    class Meta:
        model = UserRoleAssignment
        fields = [
            "id", "user", "username", "role", "role_name",
            "scope_type", "scope_id", "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        role = attrs.get("role", getattr(self.instance, "role", None))
        scope_type = attrs.get("scope_type", getattr(self.instance, "scope_type", "GLOBAL"))
        scope_id = attrs.get("scope_id", getattr(self.instance, "scope_id", "")) or ""

        if scope_type in {"CLASS", "SECTION", "DEPARTMENT"} and not str(scope_id).strip():
            raise serializers.ValidationError({"scope_id": "A scope ID is required for this scope."})

        if role and role.is_system_role and not RBACService.is_system_administrator(actor):
            raise serializers.ValidationError({"role": "Only a System Administrator can assign a protected system role."})

        if role and actor and not RBACService.is_system_administrator(actor):
            held = RBACService.get_user_permissions(actor)
            missing = set(role.permissions.filter(is_active=True).values_list("codename", flat=True)) - held
            if missing:
                raise serializers.ValidationError(
                    {"role": "You cannot assign a role containing permissions you do not hold."}
                )
        return attrs
