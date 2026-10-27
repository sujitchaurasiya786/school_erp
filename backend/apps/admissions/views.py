import uuid
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.admissions.models import AdmissionApplication, ApplicationDocument, SeatQuota, ApplicationStatus
from apps.admissions.serializers import AdmissionApplicationSerializer, ApplicationDocumentSerializer, SeatQuotaSerializer
from apps.admissions.services import AdmissionService
from apps.rbac.permissions import HasDynamicPermission


class AdmissionApplicationViewSet(viewsets.ModelViewSet):
    """
    Manage admission applications, approvals, and conversions.
    """
    queryset = AdmissionApplication.objects.select_related('academic_year').prefetch_related('documents').all()
    serializer_class = AdmissionApplicationSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'admissions'
    filterset_fields = ['status', 'applying_class', 'academic_year']
    search_fields = ['application_number', 'first_name', 'last_name', 'guardian_phone']

    def perform_create(self, serializer):
        # Auto-generate unique application number
        app_num = f"APP-{uuid.uuid4().hex[:8].upper()}"
        serializer.save(application_number=app_num, status=ApplicationStatus.SUBMITTED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        app = self.get_object()
        remarks = request.data.get('remarks', '')
        updated_app = AdmissionService.approve_application(app, request.user, remarks)
        return Response(self.get_serializer(updated_app).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        app = self.get_object()
        reason = request.data.get('reason', '')
        updated_app = AdmissionService.reject_application(app, request.user, reason)
        return Response(self.get_serializer(updated_app).data)


class SeatQuotaViewSet(viewsets.ModelViewSet):
    queryset = SeatQuota.objects.select_related('academic_year').all()
    serializer_class = SeatQuotaSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'admissions'
    filterset_fields = ['academic_year', 'class_name']


class ApplicationDocumentViewSet(viewsets.ModelViewSet):
    queryset = ApplicationDocument.objects.all()
    serializer_class = ApplicationDocumentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'admissions'
