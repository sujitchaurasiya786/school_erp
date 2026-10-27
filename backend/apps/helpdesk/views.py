import uuid
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.helpdesk.models import TicketCategory, Ticket, TicketComment, TicketStatus
from apps.helpdesk.serializers import (
    TicketCategorySerializer, TicketSerializer, TicketCommentSerializer
)
from apps.rbac.permissions import HasDynamicPermission


class TicketCategoryViewSet(viewsets.ModelViewSet):
    queryset = TicketCategory.objects.all()
    serializer_class = TicketCategorySerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'helpdesk'


class TicketViewSet(viewsets.ModelViewSet):
    queryset = Ticket.objects.select_related('creator', 'category', 'assigned_to').prefetch_related('comments').all()
    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'helpdesk'
    filterset_fields = ['status', 'priority', 'category']
    search_fields = ['ticket_number', 'title', 'description']

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.user_type in ('ADMIN', 'PRINCIPAL'):
            return self.queryset
        # Ordinary users only see their own tickets, excluding other people's confidential tickets
        return self.queryset.filter(creator=user)

    def perform_create(self, serializer):
        t_num = f"TKT-{uuid.uuid4().hex[:8].upper()}"
        serializer.save(creator=self.request.user, ticket_number=t_num)

    @action(detail=True, methods=['post'])
    def add_comment(self, request, pk=None):
        ticket = self.get_object()
        comment_text = request.data.get('comment')
        comment = TicketComment.objects.create(ticket=ticket, author=request.user, comment=comment_text)
        return Response(TicketCommentSerializer(comment).data, status=status.HTTP_201_CREATED)
