"""
RBAC views — Role, Permission, and UserRoleAssignment management.

Security notes:
  - System roles cannot be deleted or have permissions forcibly removed via this API.
  - All role mutations and assignment changes are audit-logged.
  - Access is controlled via `HasDynamicPermission` keyed to `rbac` module.
  - The `user_roles` action filters only ACTIVE assignments.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.rbac.models import Permission, Role, UserRoleAssignment
from apps.rbac.permissions import HasDynamicPermission
from apps.rbac.serializers import (
    PermissionSerializer,
    RoleSerializer,
    UserRoleAssignmentSerializer,
)
from apps.rbac.services import RBACService


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only list and detail view of all registered permissions.
    Only users with rbac.view access may browse these.
    """

    queryset = Permission.objects.filter(is_active=True)
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = "rbac"
    required_permission = "rbac.role.view"
    filterset_fields = ["module", "action"]
    search_fields = ["name", "codename", "description"]


class RoleViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for dynamic roles.
    Admin or Principal can create/edit roles and their permission sets.

    Protected rules:
      - System roles cannot be deleted.
      - Any mutation is audit-logged with before/after state.
    """

    queryset = Role.objects.all().prefetch_related("permissions")
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = "rbac"
    filterset_fields = ["is_system_role", "is_active"]
    search_fields = ["name", "code", "description"]

    # Map ViewSet actions to specific permission codenames
    action_permission_map = {
        "list": "rbac.role.view",
        "retrieve": "rbac.role.view",
        "create": "rbac.role.manage",
        "update": "rbac.role.change",
        "partial_update": "rbac.role.change",
        "destroy": "rbac.role.delete",
    }

    def perform_create(self, serializer):
        role = serializer.save()
        AuditService.log(
            action=AuditAction.CREATE,
            module="rbac",
            entity_type="Role",
            entity_id=str(role.pk),
            actor=self.request.user,
            changes={"name": role.name, "code": role.code},
        )

    def perform_update(self, serializer):
        if serializer.instance.is_system_role and not RBACService.is_system_administrator(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only a System Administrator can edit a protected system role.")
        old_perm_ids = set(
            serializer.instance.permissions.values_list("id", flat=True)
        )
        role = serializer.save()
        new_perm_ids = set(role.permissions.values_list("id", flat=True))
        AuditService.log(
            action=AuditAction.UPDATE,
            module="rbac",
            entity_type="Role",
            entity_id=str(role.pk),
            actor=self.request.user,
            changes={
                "permissions_added": list(new_perm_ids - old_perm_ids),
                "permissions_removed": list(old_perm_ids - new_perm_ids),
            },
        )

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system_role:
            return Response(
                {
                    "status": "error",
                    "message": "System roles cannot be deleted.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if role.permissions.filter(codename="users.membership.manage", is_active=True).exists():
            if not RBACService.active_role_manager_exists(exclude_role_id=role.pk):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("This action would remove the last active role manager.")
        AuditService.log(
            action=AuditAction.DELETE,
            module="rbac",
            entity_type="Role",
            entity_id=str(role.pk),
            actor=request.user,
            changes={"name": role.name, "code": role.code},
        )
        return super().destroy(request, *args, **kwargs)


class UserRoleAssignmentViewSet(viewsets.ModelViewSet):
    """
    Assign / revoke dynamic roles for users with optional scope.
    All mutations are audit-logged.

    Access requires: users.membership.manage permission.
    """

    queryset = UserRoleAssignment.objects.select_related("user", "role").all()
    serializer_class = UserRoleAssignmentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = "rbac"
    required_permission = "users.membership.manage"
    filterset_fields = ["role", "user", "scope_type", "is_active"]

    def perform_create(self, serializer):
        assignment = serializer.save()
        AuditService.log(
            action=AuditAction.CREATE,
            module="rbac",
            entity_type="UserRoleAssignment",
            entity_id=str(assignment.pk),
            actor=self.request.user,
            changes={
                "user": assignment.user.username,
                "role": assignment.role.name,
                "scope_type": assignment.scope_type,
                "scope_id": assignment.scope_id,
            },
        )

    def perform_update(self, serializer):
        from rest_framework.exceptions import PermissionDenied
        if serializer.instance.role.is_system_role and not RBACService.is_system_administrator(self.request.user):
            raise PermissionDenied("Protected system role assignments cannot be edited here.")
        old_active = serializer.instance.is_active
        new_role = serializer.validated_data.get("role", serializer.instance.role)
        new_active = serializer.validated_data.get("is_active", old_active)
        old_role_is_manager = serializer.instance.role.permissions.filter(
            codename="users.membership.manage", is_active=True
        ).exists()
        manager_assignment_removed = old_active and (
            not new_active or (
                new_role.pk != serializer.instance.role_id
                and old_role_is_manager
            )
        )
        if manager_assignment_removed and old_role_is_manager:
            if not RBACService.active_role_manager_exists(exclude_assignment_id=serializer.instance.pk):
                raise PermissionDenied("This action would remove the last active role manager.")
        assignment = serializer.save()
        AuditService.log(
            action=AuditAction.UPDATE,
            module="rbac",
            entity_type="UserRoleAssignment",
            entity_id=str(assignment.pk),
            actor=self.request.user,
            changes={
                "is_active": {"from": old_active, "to": assignment.is_active},
            },
        )

    def perform_destroy(self, instance):
        from rest_framework.exceptions import PermissionDenied
        if instance.role.is_system_role:
            raise PermissionDenied("Protected system role assignments cannot be deleted here.")
        if instance.role.permissions.filter(codename="users.membership.manage", is_active=True).exists():
            if not RBACService.active_role_manager_exists(exclude_assignment_id=instance.pk):
                raise PermissionDenied("This action would remove the last active role manager.")
        AuditService.log(
            action=AuditAction.DELETE,
            module="rbac",
            entity_type="UserRoleAssignment",
            entity_id=str(instance.pk),
            actor=self.request.user,
            changes={
                "user": instance.user.username,
                "role": instance.role.name,
            },
        )
        instance.delete()

    @action(detail=False, methods=["get"], url_path=r"user/(?P<user_id>\d+)")
    def user_roles(self, request, user_id=None):
        """Return all ACTIVE role assignments for a specific user."""
        assignments = (
            UserRoleAssignment.objects.filter(user_id=user_id, is_active=True)
            .select_related("role")
        )
        serializer = self.get_serializer(assignments, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="my-permissions")
    def my_permissions(self, request):
        """Return the requesting user's full active permission set."""
        perms = RBACService.get_user_permissions(request.user)
        return Response({"permissions": sorted(perms)})
