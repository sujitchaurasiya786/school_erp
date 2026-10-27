from rest_framework import serializers
from apps.examinations.models import ExamTerm, Assessment, ExamMark


class ExamTermSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamTerm
        fields = ['id', 'academic_year', 'title', 'exam_type', 'start_date', 'end_date', 'status']


class AssessmentSerializer(serializers.ModelSerializer):
    subject_name = serializers.ReadOnlyField(source='subject.name')

    class Meta:
        model = Assessment
        fields = ['id', 'term', 'class_name', 'subject', 'subject_name', 'maximum_marks', 'weightage_percent']


class ExamMarkSerializer(serializers.ModelSerializer):
    student_name = serializers.ReadOnlyField(source='student.full_name')
    admission_number = serializers.ReadOnlyField(source='student.admission_number')

    class Meta:
        model = ExamMark
        fields = [
            'id', 'assessment', 'student', 'student_name', 'admission_number',
            'marks_obtained', 'is_absent', 'is_exempt', 'entered_by'
        ]
        read_only_fields = ['id', 'entered_by']
