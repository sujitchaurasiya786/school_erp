from rest_framework import serializers
from apps.helpdesk.models import TicketCategory, Ticket, TicketComment


class TicketCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketCategory
        fields = ['id', 'name', 'is_confidential']


class TicketCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.ReadOnlyField(source='author.get_full_name')

    class Meta:
        model = TicketComment
        fields = ['id', 'ticket', 'author', 'author_name', 'comment', 'created_at']
        read_only_fields = ['id', 'author', 'created_at']


class TicketSerializer(serializers.ModelSerializer):
    creator_name = serializers.ReadOnlyField(source='creator.get_full_name')
    category_name = serializers.ReadOnlyField(source='category.name')
    assigned_to_name = serializers.ReadOnlyField(source='assigned_to.get_full_name')
    comments = TicketCommentSerializer(many=True, read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id', 'ticket_number', 'creator', 'creator_name', 'category',
            'category_name', 'title', 'description', 'priority', 'status',
            'assigned_to', 'assigned_to_name', 'resolution_notes', 'comments', 'created_at'
        ]
        read_only_fields = ['id', 'ticket_number', 'creator', 'created_at']
