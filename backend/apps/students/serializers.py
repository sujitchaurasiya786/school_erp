from rest_framework import serializers

from apps.students.models import (
    BonafideCertificate, ClassEnrollment, Guardian, Student,
    StudentDocument, TransferCertificate
)


class GuardianSerializer(serializers.ModelSerializer):
    class Meta:
        model = Guardian
        fields = ['id', 'first_name', 'last_name', 'relationship', 'phone', 'email', 'occupation', 'annual_income']
        read_only_fields = ['id']


class ClassEnrollmentSerializer(serializers.ModelSerializer):
    student_name = serializers.ReadOnlyField(source='student.full_name')
    admission_number = serializers.ReadOnlyField(source='student.admission_number')
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')

    class Meta:
        model = ClassEnrollment
        fields = [
            'id', 'student', 'student_name', 'admission_number',
            'academic_year', 'academic_year_name', 'class_name',
            'section_name', 'roll_number'
        ]
        read_only_fields = ['id']


class TransferCertificateSerializer(serializers.ModelSerializer):
    student_name = serializers.ReadOnlyField(source='student.full_name')
    admission_number = serializers.ReadOnlyField(source='student.admission_number')

    class Meta:
        model = TransferCertificate
        fields = [
            'id', 'tc_number', 'student', 'student_name', 'admission_number',
            'issue_date', 'reason_for_leaving', 'dues_cleared', 'conduct_remarks'
        ]
        read_only_fields = ['id', 'tc_number', 'issue_date']


class BonafideCertificateSerializer(serializers.ModelSerializer):
    student_name = serializers.ReadOnlyField(source='student.full_name')
    admission_number = serializers.ReadOnlyField(source='student.admission_number')
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')

    class Meta:
        model = BonafideCertificate
        fields = [
            'id', 'certificate_number', 'student', 'student_name', 'admission_number',
            'academic_year', 'academic_year_name', 'issue_date', 'purpose', 'conduct_remarks'
        ]
        read_only_fields = ['id', 'certificate_number', 'issue_date']


class StudentDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentDocument
        fields = ['id', 'student', 'doc_type', 'title', 'document_file', 'created_at']
        read_only_fields = ['id', 'created_at']


class StudentSerializer(serializers.ModelSerializer):
    guardian = GuardianSerializer(source='primary_guardian', read_only=True)
    full_name = serializers.ReadOnlyField()
    current_class_name = serializers.ReadOnlyField()
    current_section_name = serializers.ReadOnlyField()
    current_roll_number = serializers.ReadOnlyField()
    siblings_count = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            'id', 'admission_number', 'first_name', 'last_name', 'full_name',
            'date_of_birth', 'gender', 'blood_group', 'address', 'city',
            'state', 'pincode', 'photo', 'primary_guardian', 'guardian',
            'secondary_guardian', 'status', 'admission_date', 'previous_school',
            'current_class_name', 'current_section_name', 'current_roll_number',
            'siblings_count', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def get_siblings_count(self, obj):
        return obj.siblings.count()


class IssueTCSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=True)
    dues_cleared = serializers.BooleanField(default=True)
    conduct_remarks = serializers.CharField(max_length=200, default='Good', required=False)


class IssueBonafideSerializer(serializers.Serializer):
    academic_year_id = serializers.IntegerField(required=True)
    purpose = serializers.CharField(max_length=200, required=True)
    conduct_remarks = serializers.CharField(max_length=100, default='Good', required=False)


class SinglePromotionItemSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(required=True)
    action = serializers.ChoiceField(choices=['PROMOTE', 'DETAIN', 'PASSED_OUT', 'WITHDRAWN'], default='PROMOTE')
    target_class = serializers.CharField(max_length=50, required=False, allow_blank=True)
    target_section = serializers.CharField(max_length=10, default='A', required=False)
    roll_number = serializers.IntegerField(required=False, allow_null=True)


class BatchPromotionSerializer(serializers.Serializer):
    source_academic_year_id = serializers.IntegerField(required=True)
    target_academic_year_id = serializers.IntegerField(required=True)
    promotions = SinglePromotionItemSerializer(many=True, required=True)
