from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.examinations.models import ExamTerm, Assessment, ExamMark, ResultStatus
from apps.examinations.serializers import (
    ExamTermSerializer, AssessmentSerializer, ExamMarkSerializer
)
from apps.rbac.permissions import HasDynamicPermission
from apps.audit.services import AuditService
from apps.audit.models import AuditAction


class ExamTermViewSet(viewsets.ModelViewSet):
    queryset = ExamTerm.objects.select_related('academic_year').all()
    serializer_class = ExamTermSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'examinations'
    filterset_fields = ['academic_year', 'exam_type', 'status']

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        term = self.get_object()
        term.status = ResultStatus.APPROVED
        term.save()
        AuditService.log(AuditAction.APPROVE, 'examinations', 'ExamTerm', term.id, request.user, 'Term results approved')
        return Response({'status': 'success', 'term_status': term.status})

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        term = self.get_object()
        term.status = ResultStatus.PUBLISHED
        term.save()
        AuditService.log(AuditAction.APPROVE, 'examinations', 'ExamTerm', term.id, request.user, 'Term results published to parents/students')
        return Response({'status': 'success', 'term_status': term.status})


class AssessmentViewSet(viewsets.ModelViewSet):
    queryset = Assessment.objects.select_related('term', 'subject').all()
    serializer_class = AssessmentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'examinations'
    filterset_fields = ['term', 'class_name', 'subject']


class ExamMarkViewSet(viewsets.ModelViewSet):
    queryset = ExamMark.objects.select_related('assessment', 'student').all()
    serializer_class = ExamMarkSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'examinations'
    filterset_fields = ['assessment', 'is_absent']

    def perform_create(self, serializer):
        serializer.save(entered_by=self.request.user)
