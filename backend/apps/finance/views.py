from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.finance.models import FeeHead, FeeStructure, StudentInvoice, PaymentTransaction
from apps.finance.serializers import (
    FeeHeadSerializer, FeeStructureSerializer, StudentInvoiceSerializer, PaymentTransactionSerializer
)
from apps.finance.services import FinanceService
from apps.rbac.permissions import HasDynamicPermission


class FeeHeadViewSet(viewsets.ModelViewSet):
    queryset = FeeHead.objects.all()
    serializer_class = FeeHeadSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'finance'


class FeeStructureViewSet(viewsets.ModelViewSet):
    queryset = FeeStructure.objects.select_related('fee_head', 'academic_year').all()
    serializer_class = FeeStructureSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'finance'
    filterset_fields = ['academic_year', 'class_name', 'fee_head']


class StudentInvoiceViewSet(viewsets.ModelViewSet):
    queryset = StudentInvoice.objects.select_related('student', 'academic_year').prefetch_related('payments').all()
    serializer_class = StudentInvoiceSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'finance'
    filterset_fields = ['status', 'academic_year']
    search_fields = ['invoice_number', 'student__admission_number', 'student__first_name']

    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        amount = request.data.get('amount')
        mode = request.data.get('payment_mode', 'CASH')
        ref = request.data.get('reference_number', '')
        payment = FinanceService.record_payment(pk, amount, mode, ref, request.user)
        return Response(PaymentTransactionSerializer(payment).data, status=status.HTTP_201_CREATED)


class PaymentTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PaymentTransaction.objects.select_related('invoice', 'collected_by').all()
    serializer_class = PaymentTransactionSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'finance'
    filterset_fields = ['payment_mode', 'is_reversed']
    search_fields = ['receipt_number', 'reference_number']

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        reason = request.data.get('reason')
        reversed_payment = FinanceService.reverse_payment(pk, reason, request.user)
        return Response(PaymentTransactionSerializer(reversed_payment).data)
