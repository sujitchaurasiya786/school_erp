from rest_framework import serializers
from apps.admissions.models import AdmissionApplication, ApplicationDocument, SeatQuota


class ApplicationDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationDocument
        fields = ['id', 'application', 'doc_type', 'document_file', 'verification_status', 'verification_notes']
        read_only_fields = ['id']


class AdmissionApplicationSerializer(serializers.ModelSerializer):
    documents = ApplicationDocumentSerializer(many=True, read_only=True)

    class Meta:
        model = AdmissionApplication
        fields = [
            'id', 'application_number', 'academic_year', 'applying_class',
            'first_name', 'last_name', 'date_of_birth', 'gender',
            'father_name', 'mother_name', 'guardian_phone', 'guardian_email',
            'address', 'previous_school', 'status', 'test_score',
            'interview_remarks', 'rejection_reason', 'converted_student_id',
            'documents', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'status', 'converted_student_id', 'created_at', 'updated_at']


class SeatQuotaSerializer(serializers.ModelSerializer):
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')

    class Meta:
        model = SeatQuota
        fields = ['id', 'academic_year', 'academic_year_name', 'class_name', 'total_seats', 'waitlist_limit']
        read_only_fields = ['id']
