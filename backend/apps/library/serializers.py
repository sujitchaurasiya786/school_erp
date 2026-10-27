from rest_framework import serializers
from apps.library.models import BookCatalog, BookCopy, BookIssueRecord


class BookCatalogSerializer(serializers.ModelSerializer):
    available_copies_count = serializers.SerializerMethodField()

    class Meta:
        model = BookCatalog
        fields = ['id', 'title', 'author', 'isbn', 'publisher', 'edition', 'category', 'available_copies_count']

    def get_available_copies_count(self, obj):
        return obj.copies.filter(status='AVAILABLE').count()


class BookCopySerializer(serializers.ModelSerializer):
    book_title = serializers.ReadOnlyField(source='catalog.title')

    class Meta:
        model = BookCopy
        fields = ['id', 'catalog', 'book_title', 'accession_number', 'status', 'shelf_location']


class BookIssueRecordSerializer(serializers.ModelSerializer):
    book_title = serializers.ReadOnlyField(source='book_copy.catalog.title')
    borrower_name = serializers.ReadOnlyField(source='borrower.get_full_name')

    class Meta:
        model = BookIssueRecord
        fields = [
            'id', 'book_copy', 'book_title', 'borrower', 'borrower_name',
            'issue_date', 'due_date', 'return_date', 'fine_amount', 'fine_paid'
        ]
        read_only_fields = ['id', 'issue_date']
