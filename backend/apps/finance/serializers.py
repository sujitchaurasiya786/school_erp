from rest_framework import serializers
from apps.finance.models import FeeHead, FeeStructure, StudentInvoice, PaymentTransaction


class FeeHeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeHead
        fields = ['id', 'name', 'code', 'description']


class FeeStructureSerializer(serializers.ModelSerializer):
    fee_head_name = serializers.ReadOnlyField(source='fee_head.name')

    class Meta:
        model = FeeStructure
        fields = ['id', 'academic_year', 'class_name', 'fee_head', 'fee_head_name', 'amount', 'frequency']


class PaymentTransactionSerializer(serializers.ModelSerializer):
    collected_by_name = serializers.ReadOnlyField(source='collected_by.get_full_name')

    class Meta:
        model = PaymentTransaction
        fields = [
            'id', 'receipt_number', 'invoice', 'amount_paid', 'payment_mode',
            'reference_number', 'collected_by', 'collected_by_name',
            'is_reversed', 'reversal_reason', 'created_at'
        ]
        read_only_fields = ['id', 'receipt_number', 'is_reversed', 'created_at']


class StudentInvoiceSerializer(serializers.ModelSerializer):
    payments = PaymentTransactionSerializer(many=True, read_only=True)
    student_name = serializers.ReadOnlyField(source='student.full_name')
    admission_number = serializers.ReadOnlyField(source='student.admission_number')
    balance_due = serializers.ReadOnlyField()

    class Meta:
        model = StudentInvoice
        fields = [
            'id', 'invoice_number', 'student', 'student_name', 'admission_number',
            'academic_year', 'title', 'total_amount', 'concession_amount',
            'fine_amount', 'paid_amount', 'balance_due', 'due_date', 'status',
            'payments', 'created_at'
        ]
        read_only_fields = ['id', 'paid_amount', 'created_at']
