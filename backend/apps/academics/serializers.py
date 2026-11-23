from rest_framework import serializers
from apps.academics.models import (
    AcademicStream, ClassSubject, Room, RoomType, SchoolClass, Section, Subject, TimetablePeriod, TimetableSlot
)


class RoomSerializer(serializers.ModelSerializer):
    room_type_display = serializers.CharField(source='get_room_type_display', read_only=True)

    class Meta:
        model = Room
        fields = [
            'id', 'room_number', 'name', 'room_type', 'room_type_display',
            'capacity', 'floor', 'description', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'room_type_display']


class SchoolClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolClass
        fields = ['id', 'name', 'code', 'order', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class SectionSerializer(serializers.ModelSerializer):
    class_name = serializers.ReadOnlyField(source='school_class.name')
    teacher_name = serializers.ReadOnlyField(source='class_teacher.get_full_name')
    room_detail = RoomSerializer(source='room_ref', read_only=True)

    class Meta:
        model = Section
        fields = [
            'id', 'school_class', 'class_name', 'name', 'capacity',
            'stream', 'class_teacher', 'teacher_name', 'room_number', 'room_ref', 'room_detail',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AcademicStreamSerializer(serializers.ModelSerializer):
    class_name = serializers.ReadOnlyField(source='school_class.name')

    class Meta:
        model = AcademicStream
        fields = [
            'id', 'school_class', 'class_name', 'name', 'code',
            'description', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SubjectSerializer(serializers.ModelSerializer):
    subject_type_display = serializers.CharField(source='get_subject_type_display', read_only=True)

    class Meta:
        model = Subject
        fields = [
            'id', 'name', 'code', 'subject_type', 'subject_type_display',
            'is_theory', 'is_practical', 'description', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ClassSubjectSerializer(serializers.ModelSerializer):
    class_id = serializers.ReadOnlyField(source='section.school_class.id')
    class_name = serializers.ReadOnlyField(source='section.school_class.name')
    section_name = serializers.ReadOnlyField(source='section.name')
    full_section_name = serializers.ReadOnlyField(source='section.__str__')
    stream = serializers.ReadOnlyField(source='section.stream')
    stream_display = serializers.ReadOnlyField(source='section.get_stream_display')
    subject_name = serializers.ReadOnlyField(source='subject.name')
    subject_code = serializers.ReadOnlyField(source='subject.code')
    is_theory = serializers.ReadOnlyField(source='subject.is_theory')
    is_practical = serializers.ReadOnlyField(source='subject.is_practical')
    subject_type_display = serializers.CharField(source='get_subject_type_display', read_only=True)
    teacher_name = serializers.ReadOnlyField(source='teacher.get_full_name')
    academic_year_name = serializers.ReadOnlyField(source='academic_year.name')

    class Meta:
        model = ClassSubject
        fields = [
            'id', 'section', 'full_section_name', 'class_id', 'class_name',
            'section_name', 'stream', 'stream_display', 'subject', 'subject_name',
            'subject_code', 'is_theory', 'is_practical', 'subject_type',
            'subject_type_display', 'is_elective', 'academic_year',
            'academic_year_name', 'teacher', 'teacher_name', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class TimetablePeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimetablePeriod
        fields = ['id', 'period_number', 'name', 'start_time', 'end_time', 'is_break']


class TimetableSlotSerializer(serializers.ModelSerializer):
    period_detail = TimetablePeriodSerializer(source='period', read_only=True)
    class_subject_detail = ClassSubjectSerializer(source='class_subject', read_only=True)
    room_detail = RoomSerializer(source='room_ref', read_only=True)

    class Meta:
        model = TimetableSlot
        fields = [
            'id', 'academic_year', 'day_of_week', 'period', 'period_detail',
            'class_subject', 'class_subject_detail', 'room', 'room_ref', 'room_detail'
        ]
