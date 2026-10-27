from apps.audit.models import AuditLog, AuditAction
from apps.audit.middleware import get_current_request


class AuditService:
    """
    Central service for creating immutable audit records.
    """

    @classmethod
    def log(cls, action, module, entity_type, entity_id='', actor=None, reason='', changes=None, request=None):
        if request is None:
            request = get_current_request()

        ip_address = None
        user_agent = ''
        actor_username = ''

        if request:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip_address = x_forwarded_for.split(',')[0].strip()
            else:
                ip_address = request.META.get('REMOTE_ADDR')
            user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

            if actor is None and hasattr(request, 'user') and request.user.is_authenticated:
                actor = request.user

        if actor:
            actor_username = getattr(actor, 'username', str(actor))

        return AuditLog.objects.create(
            actor=actor if actor and getattr(actor, 'is_authenticated', False) else None,
            actor_username=actor_username,
            action=action,
            module=module,
            entity_type=entity_type,
            entity_id=str(entity_id),
            ip_address=ip_address,
            user_agent=user_agent,
            reason=reason,
            changes=changes or {}
        )
