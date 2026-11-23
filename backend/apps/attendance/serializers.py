from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from apps.attendance.models import (
    Holiday, HolidayType, SaturdayRule, OverrideType,
    WorkingDayConfig, SpecialDayOverride,
    StudentDailyAttendance, AttendanceCorrectionRequest,
    WEEKDAY_CHOICES
)
from apps.school_config.models import AcademicYear


class HolidaySerializer(serializers.ModelSerializer):
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')
    holiday_type_display = serializers.CharField(source='get_holiday_type_display', read_only=True)

    class Meta:
        model = Holiday
        fields = [
            'id', 'academic_year', 'academic_year_name', 'title', 'date',
            'is_national_holiday', 'holiday_type', 'holiday_type_display',
            'description', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        instance = Holiday(**attrs)
        if self.instance:
            instance.id = self.instance.id
            if 'academic_year' not in attrs:
                instance.academic_year = self.instance.academic_year
            if 'date' not in attrs:
                instance.date = self.instance.date

        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, 'message_dict') else e.messages)
        return attrs


class VacationBreakSerializer(serializers.Serializer):
    academic_year = serializers.PrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    title = serializers.CharField(max_length=100)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    holiday_type = serializers.ChoiceField(choices=HolidayType.choices, default=HolidayType.VACATION)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    is_national_holiday = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs['start_date'] > attrs['end_date']:
            raise serializers.ValidationError({'end_date': _("End date must be on or after start date.")})
        ay = attrs['academic_year']
        if ay.start_date and ay.end_date:
            if not (ay.start_date <= attrs['start_date'] <= ay.end_date and ay.start_date <= attrs['end_date'] <= ay.end_date):
                raise serializers.ValidationError({
                    'date': _(f"Vacation break dates fall outside Academic Year '{ay.name}' ({ay.start_date} to {ay.end_date}).")
                })
        return attrs


class WorkingDayConfigSerializer(serializers.ModelSerializer):
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')
    saturday_rule_display = serializers.CharField(source='get_saturday_rule_display', read_only=True)

    class Meta:
        model = WorkingDayConfig
        fields = [
            'id', 'academic_year', 'academic_year_name',
            'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'sunday',
            'saturday_rule', 'saturday_rule_display', 'notes',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SpecialDayOverrideSerializer(serializers.ModelSerializer):
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')
    override_type_display = serializers.CharField(source='get_override_type_display', read_only=True)
    timetable_weekday_display = serializers.CharField(source='get_timetable_weekday_display', read_only=True)
    created_by_name = serializers.ReadOnlyField(source='created_by.get_full_name')

    class Meta:
        model = SpecialDayOverride
        fields = [
            'id', 'academic_year', 'academic_year_name', 'date', 'is_working',
            'override_type', 'override_type_display',
            'timetable_weekday', 'timetable_weekday_display',
            'reason', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        is_working = attrs.get('is_working', getattr(self.instance, 'is_working', True))
        timetable_weekday = attrs.get('timetable_weekday', getattr(self.instance, 'timetable_weekday', None))

        if is_working and not timetable_weekday:
            raise serializers.ValidationError({
                'timetable_weekday': _("A regular weekday timetable schedule must be selected for special working days.")
            })
        return attrs


class DayStatusSerializer(serializers.Serializer):
    date = serializers.DateField()
    is_working = serializers.BooleanField()
    reason = serializers.CharField()
    type = serializers.CharField()
    timetable_weekday = serializers.IntegerField(allow_null=True)
    timetable_weekday_display = serializers.CharField(allow_null=True)


class StudentDailyAttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.ReadOnlyField(source='student.full_name')
    admission_number = serializers.ReadOnlyField(source='student.admission_number')
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')

    class Meta:
        model = StudentDailyAttendance
        fields = [
            'id', 'student', 'student_name', 'admission_number', 'academic_year', 'academic_year_name',
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
