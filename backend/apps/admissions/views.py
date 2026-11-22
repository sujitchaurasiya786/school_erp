from rest_framework import viewsets, status, serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.admissions.models import (
    AdmissionApplication, ApplicationDocument, SeatQuota,
    ApplicationStatus, DocumentStatus
)
from apps.admissions.serializers import (
    AdmissionApplicationSerializer, ApplicationDocumentSerializer, SeatQuotaSerializer,
    ApplicationApprovalSerializer, ApplicationRejectionSerializer,
    DocumentVerificationSerializer, EvaluationSerializer, StudentConversionSerializer
)
from apps.admissions.services import AdmissionService
from apps.rbac.permissions import HasDynamicPermission
from common.exceptions import BusinessConflictError, ResourceNotFoundError


class AdmissionApplicationViewSet(viewsets.ModelViewSet):
    """
    Manage admission applications, evaluations, approvals, and conversions to student.
    """
    queryset = AdmissionApplication.objects.select_related('academic_year').prefetch_related('documents').all()
    serializer_class = AdmissionApplicationSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'admissions'
    action_permission_map = {
        'list': 'admissions.application.view',
        'retrieve': 'admissions.application.view',
        'create': 'admissions.application.add',
        'update': 'admissions.application.change',
        'partial_update': 'admissions.application.change',
        'destroy': 'admissions.application.change',
        'approve': 'admissions.application.approve',
        'reject': 'admissions.application.approve',
        'convert_to_student': 'admissions.application.approve',
        'record_evaluation': 'admissions.application.change',
    }
    filterset_fields = ['status', 'applying_class', 'academic_year']
    search_fields = ['application_number', 'first_name', 'last_name', 'guardian_phone']

    def perform_create(self, serializer):
        data = serializer.validated_data
        try:
            app = AdmissionService.create_application(
                academic_year=data['academic_year'],
                applying_class=data['applying_class'],
                first_name=data['first_name'],
                last_name=data['last_name'],
                date_of_birth=data['date_of_birth'],
                gender=data['gender'],
                father_name=data['father_name'],
                mother_name=data['mother_name'],
                guardian_phone=data['guardian_phone'],
                address=data['address'],
                guardian_email=data.get('guardian_email', ''),
                previous_school=data.get('previous_school', ''),
                actor=self.request.user,
                request=self.request
            )
            serializer.instance = app
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        app = self.get_object()
        ser = ApplicationApprovalSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            updated_app = AdmissionService.approve_application(
                application=app,
                user=request.user,
                remarks=ser.validated_data.get('remarks', ''),
                request=request
            )
            return Response(self.get_serializer(updated_app).data)
        except BusinessConflictError as err:
            return Response({'error': str(err)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        app = self.get_object()
        ser = ApplicationRejectionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            updated_app = AdmissionService.reject_application(
                application=app,
                user=request.user,
                reason=ser.validated_data['reason'],
                request=request
            )
            return Response(self.get_serializer(updated_app).data)
        except BusinessConflictError as err:
            return Response({'error': str(err)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    @action(detail=True, methods=['post'], url_path='convert-to-student')
    def convert_to_student(self, request, pk=None):
        app = self.get_object()
        ser = StudentConversionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            student = AdmissionService.convert_to_student(
                application_id=app.pk,
                section_name=data.get('section_name', 'A'),
                roll_number=data.get('roll_number'),
                admission_date=data.get('admission_date'),
                user=request.user,
                request=request
            )
            return Response({
                'status': 'success',
                'message': f"Application {app.application_number} successfully converted to student {student.admission_number}.",
                'student_id': student.id,
                'admission_number': student.admission_number
            }, status=status.HTTP_201_CREATED)
        except BusinessConflictError as err:
            return Response({'error': str(err)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    @action(detail=True, methods=['post'], url_path='record-evaluation')
    def record_evaluation(self, request, pk=None):
        app = self.get_object()
        ser = EvaluationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            updated_app = AdmissionService.record_evaluation(
                application_id=app.pk,
                test_score=data.get('test_score'),
                interview_remarks=data.get('interview_remarks', ''),
                status=data.get('status'),
                user=request.user,
                request=request
            )
            return Response(self.get_serializer(updated_app).data)
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)


class SeatQuotaViewSet(viewsets.ModelViewSet):
    queryset = SeatQuota.objects.select_related('academic_year').all()
    serializer_class = SeatQuotaSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'admissions'
    action_permission_map = {
        'list': 'admissions.application.view',
        'retrieve': 'admissions.application.view',
        'create': 'admissions.seats.manage',
        'update': 'admissions.seats.manage',
        'partial_update': 'admissions.seats.manage',
        'destroy': 'admissions.seats.manage',
    }
    filterset_fields = ['academic_year', 'class_name']


class ApplicationDocumentViewSet(viewsets.ModelViewSet):
    queryset = ApplicationDocument.objects.select_related('application').all()
    serializer_class = ApplicationDocumentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'admissions'
    action_permission_map = {
        'list': 'admissions.application.view',
        'retrieve': 'admissions.application.view',
        'create': 'admissions.application.change',
        'update': 'admissions.application.change',
        'partial_update': 'admissions.application.change',
        'destroy': 'admissions.application.change',
        'verify': 'admissions.application.approve',
    }

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        doc = self.get_object()
        ser = DocumentVerificationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            updated_doc = AdmissionService.verify_document(
                document_id=doc.pk,
                verification_status=data['verification_status'],
                notes=data.get('verification_notes', ''),
                user=request.user,
                request=request
            )
            return Response(self.get_serializer(updated_doc).data)
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)
