from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.academics.models import (
    SchoolClass, Section, Subject, ClassSubject, TimetablePeriod, TimetableSlot
)
from apps.academics.serializers import (
    SchoolClassSerializer, SectionSerializer, SubjectSerializer,
    ClassSubjectSerializer, TimetablePeriodSerializer, TimetableSlotSerializer
)
from apps.academics.services import AcademicsService
from apps.rbac.permissions import HasDynamicPermission


class SchoolClassViewSet(viewsets.ModelViewSet):
    queryset = SchoolClass.objects.all()
    serializer_class = SchoolClassSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'


class SectionViewSet(viewsets.ModelViewSet):
    queryset = Section.objects.select_related('school_class', 'class_teacher').all()
    serializer_class = SectionSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'
    filterset_fields = ['school_class', 'stream']


class SubjectViewSet(viewsets.ModelViewSet):
    queryset = Subject.objects.all()
    serializer_class = SubjectSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'


class ClassSubjectViewSet(viewsets.ModelViewSet):
    queryset = ClassSubject.objects.select_related('section', 'subject', 'teacher').all()
    serializer_class = ClassSubjectSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'
    filterset_fields = ['section', 'subject', 'teacher']


class TimetablePeriodViewSet(viewsets.ModelViewSet):
    queryset = TimetablePeriod.objects.all()
    serializer_class = TimetablePeriodSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'


class TimetableSlotViewSet(viewsets.ModelViewSet):
    queryset = TimetableSlot.objects.select_related('academic_year', 'period', 'class_subject').all()
    serializer_class = TimetableSlotSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'
    filterset_fields = ['academic_year', 'day_of_week']

    def perform_create(self, serializer):
        data = serializer.validated_data
        AcademicsService.validate_slot_conflicts(
            data['academic_year'], data['day_of_week'], data['period'],
            data['class_subject'], data.get('room')
        )
        serializer.save()
