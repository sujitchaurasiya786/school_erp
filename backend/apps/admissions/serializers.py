from rest_framework import serializers
from apps.admissions.models import (
    AdmissionApplication, ApplicationDocument, SeatQuota,
    ApplicationStatus, DocumentStatus
)


class ApplicationDocumentSerializer(serializers.ModelSerializer):
    verification_status_display = serializers.CharField(source='get_verification_status_display', read_only=True)

    class Meta:
        model = ApplicationDocument
        fields = [
            'id', 'application', 'doc_type', 'document_file',
            'verification_status', 'verification_status_display',
            'verification_notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AdmissionApplicationSerializer(serializers.ModelSerializer):
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)
    documents = ApplicationDocumentSerializer(many=True, read_only=True)

    class Meta:
        model = AdmissionApplication
        fields = [
            'id', 'application_number', 'academic_year', 'academic_year_name',
            'applying_class', 'first_name', 'last_name',
            'date_of_birth', 'gender', 'gender_display',
            'father_name', 'mother_name', 'guardian_phone', 'guardian_email',
            'address', 'previous_school', 'status', 'status_display',
            'test_score', 'interview_remarks', 'rejection_reason',
            'converted_student_id', 'documents', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'application_number', 'status', 'converted_student_id', 'created_at', 'updated_at']


class SeatQuotaSerializer(serializers.ModelSerializer):
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')
    filled_seats = serializers.SerializerMethodField()
    available_seats = serializers.SerializerMethodField()

    class Meta:
        model = SeatQuota
        fields = [
            'id', 'academic_year', 'academic_year_name', 'class_name',
            'total_seats', 'waitlist_limit', 'filled_seats', 'available_seats',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_filled_seats(self, obj):
        return AdmissionApplication.objects.filter(
            academic_year=obj.academic_year,
            applying_class=obj.class_name,
            status__in=[ApplicationStatus.APPROVED, ApplicationStatus.ADMITTED]
        ).count()

    def get_available_seats(self, obj):
        filled = self.get_filled_seats(obj)
        return max(0, obj.total_seats - filled)


class ApplicationApprovalSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, default='')


class ApplicationRejectionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True)


class DocumentVerificationSerializer(serializers.Serializer):
    verification_status = serializers.ChoiceField(choices=DocumentStatus.choices)
    verification_notes = serializers.CharField(required=False, allow_blank=True, default='')


class EvaluationSerializer(serializers.Serializer):
    test_score = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    interview_remarks = serializers.CharField(required=False, allow_blank=True, default='')
    status = serializers.ChoiceField(choices=ApplicationStatus.choices, required=False)


class StudentConversionSerializer(serializers.Serializer):
    section_name = serializers.CharField(default='A', max_length=10)
    roll_number = serializers.IntegerField(required=False, allow_null=True)
    admission_date = serializers.DateField(required=False, allow_null=True)
