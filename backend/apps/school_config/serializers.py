from rest_framework import serializers
from apps.school_config.models import AcademicYear, SchoolProfile


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ['id', 'name', 'start_date', 'end_date', 'is_current', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class SchoolProfileSerializer(serializers.ModelSerializer):
    current_academic_year_name = serializers.ReadOnlyField(source='current_academic_year.name')

    class Meta:
        model = SchoolProfile
        fields = [
            'id', 'school_name', 'affiliation_number', 'school_code',
            'board', 'address', 'city', 'state', 'pincode', 'phone',
            'email', 'website', 'logo', 'current_academic_year',
            'current_academic_year_name'
        ]
        read_only_fields = ['id']
