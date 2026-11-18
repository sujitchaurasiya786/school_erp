from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.academics.models import (
    SchoolClass, Section, Subject, ClassSubject, TimetablePeriod, TimetableSlot
)
from apps.academics.serializers import (
    SchoolClassSerializer, SectionSerializer, SubjectSerializer,
    ClassSubjectSerializer, TimetablePeriodSerializer, TimetableSlotSerializer
)
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from apps.academics.services import AcademicsService, ClassSubjectService
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
    queryset = ClassSubject.objects.select_related(
        'section', 'section__school_class', 'subject', 'teacher', 'academic_year'
    ).all()
    serializer_class = ClassSubjectSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'
    filterset_fields = ['section', 'subject', 'teacher', 'subject_type', 'is_elective', 'is_active', 'academic_year']
    search_fields = ['subject__name', 'subject__code', 'section__name', 'section__school_class__name']

    @action(detail=False, methods=['post'], url_path='bulk-map')
    def bulk_map(self, request):
        school_class_id = request.data.get('school_class')
        subject_ids = request.data.get('subjects', [])
        stream = request.data.get('stream')
        section_id = request.data.get('section')
        subject_type = request.data.get('subject_type')
        academic_year_id = request.data.get('academic_year')

        result = ClassSubjectService.bulk_map_subjects_to_class(
            school_class_id=school_class_id,
            subject_ids=subject_ids,
            stream=stream,
            section_id=section_id,
            subject_type=subject_type,
            academic_year_id=academic_year_id,
            actor=request.user,
            reason=f"API bulk mapping to class {school_class_id}",
            request=request,
        )
        serializer = self.get_serializer(result['created_mappings'], many=True)
        return Response({
            'created_count': result['created_count'],
            'skipped_count': result['skipped_count'],
            'created_mappings': serializer.data
        }, status=status.HTTP_201_CREATED)


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
