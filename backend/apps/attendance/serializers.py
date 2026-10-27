from rest_framework import serializers
from apps.attendance.models import Holiday, StudentDailyAttendance, AttendanceCorrectionRequest


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ['id', 'academic_year', 'title', 'date', 'is_national_holiday']


class StudentDailyAttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.ReadOnlyField(source='student.full_name')
    admission_number = serializers.ReadOnlyField(source='student.admission_number')

    class Meta:
        model = StudentDailyAttendance
        fields = [
            'id', 'student', 'student_name', 'admission_number', 'academic_year',
            'class_name', 'section_name', 'date', 'status', 'remarks', 'marked_by'
        ]
        read_only_fields = ['id', 'marked_by']


class AttendanceCorrectionRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceCorrectionRequest
        fields = [
            'id', 'attendance', 'requested_by', 'new_status', 'reason',
            'is_approved', 'reviewed_by', 'review_notes', 'created_at'
        ]
        read_only_fields = ['id', 'requested_by', 'reviewed_by', 'created_at']
