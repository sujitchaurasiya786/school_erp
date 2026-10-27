from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class TicketPriority(models.TextChoices):
    LOW = 'LOW', _('Low')
    MEDIUM = 'MEDIUM', _('Medium')
    HIGH = 'HIGH', _('High')
    URGENT = 'URGENT', _('Urgent')


class TicketStatus(models.TextChoices):
    OPEN = 'OPEN', _('Open')
    IN_PROGRESS = 'IN_PROGRESS', _('In Progress')
    RESOLVED = 'RESOLVED', _('Resolved')
    CLOSED = 'CLOSED', _('Closed')


class TicketCategory(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    is_confidential = models.BooleanField(
        default=False,
        help_text=_("Confidential grievances are visible only to authorized users/Principal.")
    )

    class Meta:
        db_table = 'helpdesk_categories'

    def __str__(self):
        return self.name


class Ticket(TimeStampedModel):
    ticket_number = models.CharField(max_length=50, unique=True, db_index=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_tickets')
    category = models.ForeignKey(TicketCategory, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    description = models.TextField()
    priority = models.CharField(max_length=20, choices=TicketPriority.choices, default=TicketPriority.MEDIUM)
    status = models.CharField(max_length=20, choices=TicketStatus.choices, default=TicketStatus.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tickets'
    )
    resolution_notes = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'helpdesk_tickets'
        ordering = ['-created_at']

    def __str__(self):
        return f"#{self.ticket_number} - {self.title} ({self.status})"


class TicketComment(TimeStampedModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    comment = models.TextField()

    class Meta:
        db_table = 'helpdesk_ticket_comments'
