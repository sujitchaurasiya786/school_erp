from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditAction(models.TextChoices):
    CREATE = 'CREATE', _('Create')
    UPDATE = 'UPDATE', _('Update')
    DELETE = 'DELETE', _('Delete')
    APPROVE = 'APPROVE', _('Approve')
    REJECT = 'REJECT', _('Reject')
    EXPORT = 'EXPORT', _('Export')
    LOGIN = 'LOGIN', _('Login')
    LOGOUT = 'LOGOUT', _('Logout')
    MARK_CORRECTION = 'MARK_CORRECTION', _('Mark Correction')
    PAYMENT_REVERSAL = 'PAYMENT_REVERSAL', _('Payment Reversal')
    PROMOTION = 'PROMOTION', _('Promotion')
    TC_ISSUE = 'TC_ISSUE', _('TC Issue')


class AuditLog(models.Model):
    """
    Immutable audit log for security compliance and tracking sensitive actions.
    """
    id = models.BigAutoField(primary_key=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        db_index=True
    )
    actor_username = models.CharField(max_length=150, blank=True, default='')
    action = models.CharField(max_length=30, choices=AuditAction.choices, db_index=True)
    module = models.CharField(max_length=50, db_index=True)
    entity_type = models.CharField(max_length=100, db_index=True)
    entity_id = models.CharField(max_length=100, blank=True, default='', db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    reason = models.TextField(blank=True, default='')
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['module', 'entity_type', 'entity_id']),
            models.Index(fields=['actor', 'action']),
        ]

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.actor_username or 'System'} - {self.action} on {self.module}:{self.entity_type} ({self.entity_id})"
