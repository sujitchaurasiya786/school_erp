from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.rbac.models import Role, Permission, UserRoleAssignment
from apps.rbac.serializers import RoleSerializer, PermissionSerializer, UserRoleAssignmentSerializer
from apps.rbac.permissions import HasDynamicPermission
from apps.rbac.services import RBACService


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    List and retrieve available system permissions.
    """
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'rbac'
    filterset_fields = ['module', 'action']
    search_fields = ['name', 'codename', 'description']


class RoleViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for dynamic roles.
    Admin or Principal can create/edit roles and permissions without code changes.
    """
    queryset = Role.objects.all().prefetch_related('permissions')
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'rbac'
    filterset_fields = ['is_system_role', 'is_active']
    search_fields = ['name', 'code', 'description']

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system_role:
            return Response(
                {'status': 'error', 'message': 'System roles cannot be deleted.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)


class UserRoleAssignmentViewSet(viewsets.ModelViewSet):
    """
    Assign dynamic roles to users with optional scopes.
    """
    queryset = UserRoleAssignment.objects.select_related('user', 'role').all()
    serializer_class = UserRoleAssignmentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'rbac'
    filterset_fields = ['role', 'user', 'scope_type', 'is_active']

    @action(detail=False, methods=['get'], url_path=r'user/(?P<user_id>\d+)')
    def user_roles(self, request, user_id=None):
        assignments = self.queryset.filter(user_id=user_id, is_active=True)
        serializer = self.get_serializer(assignments, many=True)
        return Response(serializer.data)
