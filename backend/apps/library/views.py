from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.library.models import BookCatalog, BookCopy, BookIssueRecord, BookCopyStatus
from apps.library.serializers import (
    BookCatalogSerializer, BookCopySerializer, BookIssueRecordSerializer
)
from apps.rbac.permissions import HasDynamicPermission
from common.exceptions import SchoolERPException


class BookCatalogViewSet(viewsets.ModelViewSet):
    queryset = BookCatalog.objects.prefetch_related('copies').all()
    serializer_class = BookCatalogSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'library'
    search_fields = ['title', 'author', 'isbn', 'category']


class BookCopyViewSet(viewsets.ModelViewSet):
    queryset = BookCopy.objects.select_related('catalog').all()
    serializer_class = BookCopySerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'library'
    filterset_fields = ['status', 'catalog']
    search_fields = ['accession_number']


class BookIssueRecordViewSet(viewsets.ModelViewSet):
    queryset = BookIssueRecord.objects.select_related('book_copy__catalog', 'borrower').all()
    serializer_class = BookIssueRecordSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'library'
    filterset_fields = ['borrower', 'fine_paid']

    @action(detail=False, methods=['post'])
    def issue_book(self, request):
        copy_id = request.data.get('book_copy_id')
        borrower_id = request.data.get('borrower_id')
        due_date = request.data.get('due_date')

        copy = BookCopy.objects.get(id=copy_id)
        if copy.status != BookCopyStatus.AVAILABLE:
            raise SchoolERPException("This book copy is currently not available for issue.")

        copy.status = BookCopyStatus.ISSUED
        copy.save()

        record = BookIssueRecord.objects.create(
            book_copy=copy,
            borrower_id=borrower_id,
            due_date=due_date
        )
        return Response(self.get_serializer(record).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def return_book(self, request, pk=None):
        record = self.get_object()
        if record.return_date:
            raise SchoolERPException("This book has already been returned.")

        record.return_date = timezone.now().date()
        record.save()

        copy = record.book_copy
        copy.status = BookCopyStatus.AVAILABLE
        copy.save()

        return Response(self.get_serializer(record).data)
