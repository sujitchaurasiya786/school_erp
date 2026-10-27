from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer
from apps.rbac.permissions import HasDynamicPermission


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only audit log records for authorized auditors/administrators.
    """
    queryset = AuditLog.objects.select_related('actor').all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'audit'
    filterset_fields = ['module', 'action', 'entity_type', 'actor']
    search_fields = ['actor_username', 'entity_id', 'reason']
