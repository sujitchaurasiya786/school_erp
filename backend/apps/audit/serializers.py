from rest_framework import serializers
from apps.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            'id', 'actor', 'actor_username', 'action', 'module',
            'entity_type', 'entity_id', 'ip_address', 'user_agent',
            'reason', 'changes', 'created_at'
        ]
        read_only_fields = fields
