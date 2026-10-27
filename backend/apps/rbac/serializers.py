from rest_framework import serializers
from apps.rbac.models import Role, Permission, UserRoleAssignment


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['id', 'module', 'action', 'codename', 'name', 'description']


class RoleSerializer(serializers.ModelSerializer):
    permissions_count = serializers.IntegerField(source='permissions.count', read_only=True)
    permission_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Permission.objects.all(),
        source='permissions',
        write_only=True,
        required=False
    )
    permissions = PermissionSerializer(many=True, read_only=True)

    class Meta:
        model = Role
        fields = [
            'id', 'code', 'name', 'description', 'is_system_role',
            'is_active', 'permissions_count', 'permissions', 'permission_ids',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserRoleAssignmentSerializer(serializers.ModelSerializer):
    role_name = serializers.ReadOnlyField(source='role.name')
    username = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = UserRoleAssignment
        fields = [
            'id', 'user', 'username', 'role', 'role_name',
            'scope_type', 'scope_id', 'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
