from rest_framework import serializers
from apps.academics.models import (
    SchoolClass, Section, Subject, ClassSubject, TimetablePeriod, TimetableSlot
)


class SchoolClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolClass
        fields = ['id', 'name', 'code', 'order']


class SectionSerializer(serializers.ModelSerializer):
    class_name = serializers.ReadOnlyField(source='school_class.name')
    teacher_name = serializers.ReadOnlyField(source='class_teacher.get_full_name')

    class Meta:
        model = Section
        fields = ['id', 'school_class', 'class_name', 'name', 'stream', 'class_teacher', 'teacher_name', 'room_number']


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ['id', 'name', 'code', 'is_theory', 'is_practical']


class ClassSubjectSerializer(serializers.ModelSerializer):
    section_name = serializers.ReadOnlyField(source='section.__str__')
    subject_name = serializers.ReadOnlyField(source='subject.name')
    teacher_name = serializers.ReadOnlyField(source='teacher.get_full_name')

    class Meta:
        model = ClassSubject
        fields = ['id', 'section', 'section_name', 'subject', 'subject_name', 'teacher', 'teacher_name', 'is_elective']


class TimetablePeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimetablePeriod
        fields = ['id', 'period_number', 'name', 'start_time', 'end_time', 'is_break']


class TimetableSlotSerializer(serializers.ModelSerializer):
    period_detail = TimetablePeriodSerializer(source='period', read_only=True)
    class_subject_detail = ClassSubjectSerializer(source='class_subject', read_only=True)

    class Meta:
        model = TimetableSlot
        fields = [
            'id', 'academic_year', 'day_of_week', 'period', 'period_detail',
            'class_subject', 'class_subject_detail', 'room'
        ]
