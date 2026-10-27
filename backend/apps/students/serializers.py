from rest_framework import serializers
from apps.students.models import Student, Guardian, ClassEnrollment, TransferCertificate


class GuardianSerializer(serializers.ModelSerializer):
    class Meta:
        model = Guardian
        fields = ['id', 'first_name', 'last_name', 'relationship', 'phone', 'email', 'occupation']
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


class StudentSerializer(serializers.ModelSerializer):
    guardian = GuardianSerializer(source='primary_guardian', read_only=True)
    full_name = serializers.ReadOnlyField()

    class Meta:
        model = Student
        fields = [
            'id', 'admission_number', 'first_name', 'last_name', 'full_name',
            'date_of_birth', 'gender', 'blood_group', 'address', 'city',
            'state', 'pincode', 'photo', 'primary_guardian', 'guardian',
            'status', 'admission_date', 'previous_school', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
