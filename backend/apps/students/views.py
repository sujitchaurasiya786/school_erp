from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.rbac.permissions import HasDynamicPermission
from apps.rbac.services import RBACService
from apps.school_config.models import AcademicYear
from apps.students.models import (
    BonafideCertificate, ClassEnrollment, Guardian, Student,
    StudentDocument, TransferCertificate
)
from apps.students.serializers import (
    BatchPromotionSerializer, BonafideCertificateSerializer,
    ClassEnrollmentSerializer, GuardianSerializer, IssueBonafideSerializer,
    IssueTCSerializer, StudentDocumentSerializer, StudentSerializer,
    TransferCertificateSerializer
)
from apps.students.services import StudentService
from common.exceptions import BusinessConflictError, ResourceNotFoundError, SchoolERPException


class StudentViewSet(viewsets.ModelViewSet):
    """
    Manage student profiles. Students/parents see only their own data.
    """
    queryset = Student.objects.select_related('primary_guardian', 'secondary_guardian').prefetch_related('siblings', 'documents').all()
    serializer_class = StudentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    filterset_fields = ['status', 'gender', 'admission_date']
    search_fields = ['admission_number', 'first_name', 'last_name', 'primary_guardian__phone']

    action_permission_map = {
        'list': 'students.profile.view',
        'retrieve': 'students.profile.view',
        'create': 'students.profile.add',
        'update': 'students.profile.change',
        'partial_update': 'students.profile.change',
        'destroy': 'students.profile.change',
        'issue_tc': 'students.tc.issue',
        'issue_bonafide': 'students.profile.view',
        'link_sibling': 'students.profile.change',
        'unlink_sibling': 'students.profile.change',
        'upload_document': 'students.profile.change',
    }

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        if user.user_type == 'STUDENT' and hasattr(user, 'student_profile'):
            return self.queryset.filter(id=user.student_profile.id)
        if user.user_type == 'PARENT' and hasattr(user, 'guardian_profile'):
            return self.queryset.filter(primary_guardian=user.guardian_profile)
        if RBACService.has_permission(user, 'students.profile.view') or user.user_type in ('ADMIN', 'PRINCIPAL', 'TEACHER', 'STAFF', 'ACCOUNTANT'):
            return self.queryset
        return self.queryset.none()

    @action(detail=True, methods=['post'], url_path='issue-tc')
    def issue_tc(self, request, pk=None):
        ser = IssueTCSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            tc = StudentService.issue_transfer_certificate(
                student_id=pk,
                reason=data['reason'],
                user=request.user,
                dues_cleared=data.get('dues_cleared', True),
                conduct_remarks=data.get('conduct_remarks', 'Good'),
                request=request
            )
            return Response(TransferCertificateSerializer(tc).data, status=status.HTTP_201_CREATED)
        except (SchoolERPException, BusinessConflictError) as err:
            return Response({'error': str(err)}, status=status.HTTP_409_CONFLICT)

    @action(detail=True, methods=['post'], url_path='issue-bonafide')
    def issue_bonafide(self, request, pk=None):
        ser = IssueBonafideSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        ay = AcademicYear.objects.get(id=data['academic_year_id'])
        bonafide = StudentService.issue_bonafide_certificate(
            student_id=pk,
            academic_year=ay,
            purpose=data['purpose'],
            conduct_remarks=data.get('conduct_remarks', 'Good'),
            user=request.user,
            request=request
        )
        return Response(BonafideCertificateSerializer(bonafide).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='link-sibling')
    def link_sibling(self, request, pk=None):
        sibling_id = request.data.get('sibling_id')
        if not sibling_id:
            return Response({'error': 'sibling_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        StudentService.link_siblings(pk, sibling_id, user=request.user, request=request)
        return Response({'status': 'success', 'message': 'Sibling linked successfully.'})

    @action(detail=True, methods=['post'], url_path='unlink-sibling')
    def unlink_sibling(self, request, pk=None):
        sibling_id = request.data.get('sibling_id')
        if not sibling_id:
            return Response({'error': 'sibling_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        StudentService.unlink_siblings(pk, sibling_id, user=request.user, request=request)
        return Response({'status': 'success', 'message': 'Sibling unlinked successfully.'})

    @action(detail=True, methods=['post'], url_path='upload-document')
    def upload_document(self, request, pk=None):
        doc_type = request.data.get('doc_type', '').strip()
        doc_file = request.FILES.get('document_file')
        title = request.data.get('title', '').strip()
        if not doc_type or not doc_file:
            return Response({'error': 'doc_type and document_file are required.'}, status=status.HTTP_400_BAD_REQUEST)
        doc = StudentService.add_student_document(
            student_id=pk,
            doc_type=doc_type,
            document_file=doc_file,
            title=title,
            user=request.user,
            request=request
        )
        return Response(StudentDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


class GuardianViewSet(viewsets.ModelViewSet):
    queryset = Guardian.objects.all()
    serializer_class = GuardianSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    search_fields = ['first_name', 'last_name', 'phone', 'email']
    action_permission_map = {
        'list': 'students.profile.view',
        'retrieve': 'students.profile.view',
        'create': 'students.profile.add',
        'update': 'students.profile.change',
        'partial_update': 'students.profile.change',
        'destroy': 'students.profile.change',
    }


class ClassEnrollmentViewSet(viewsets.ModelViewSet):
    queryset = ClassEnrollment.objects.select_related('student', 'academic_year').all()
    serializer_class = ClassEnrollmentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    filterset_fields = ['academic_year', 'class_name', 'section_name']
    search_fields = ['student__admission_number', 'student__first_name', 'student__last_name']
    action_permission_map = {
        'list': 'students.profile.view',
        'retrieve': 'students.profile.view',
        'create': 'students.profile.change',
        'update': 'students.profile.change',
        'partial_update': 'students.profile.change',
        'destroy': 'students.profile.change',
        'promote': 'students.promotion.approve',
        'batch_promote': 'students.promotion.approve',
    }

    @action(detail=False, methods=['post'])
    def promote(self, request):
        target_year_id = request.data.get('academic_year_id')
        target_class = request.data.get('class_name')
        target_section = request.data.get('section_name')
        student_ids = request.data.get('student_ids', [])

        target_year = AcademicYear.objects.get(id=target_year_id)
        promoted = StudentService.promote_students(
            target_year, target_class, target_section, student_ids, request.user, request=request
        )
        return Response({'status': 'success', 'promoted_count': len(promoted)})

    @action(detail=False, methods=['post'], url_path='batch-promote')
    def batch_promote(self, request):
        ser = BatchPromotionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        src_year = AcademicYear.objects.get(id=data['source_academic_year_id'])
        tgt_year = AcademicYear.objects.get(id=data['target_academic_year_id'])

        results = StudentService.promote_class_with_retention(
            source_academic_year=src_year,
            target_academic_year=tgt_year,
            promotions=data['promotions'],
            user=request.user,
            request=request
        )
        return Response({'status': 'success', 'summary': results})


class TransferCertificateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TransferCertificate.objects.select_related('student').all()
    serializer_class = TransferCertificateSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    search_fields = ['tc_number', 'student__admission_number']
    action_permission_map = {
        'list': 'students.profile.view',
        'retrieve': 'students.profile.view',
    }


class BonafideCertificateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = BonafideCertificate.objects.select_related('student', 'academic_year').all()
    serializer_class = BonafideCertificateSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    search_fields = ['certificate_number', 'student__admission_number']
    action_permission_map = {
        'list': 'students.profile.view',
        'retrieve': 'students.profile.view',
    }


class StudentDocumentViewSet(viewsets.ModelViewSet):
    queryset = StudentDocument.objects.select_related('student').all()
    serializer_class = StudentDocumentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    action_permission_map = {
        'list': 'students.profile.view',
        'retrieve': 'students.profile.view',
        'create': 'students.profile.change',
        'update': 'students.profile.change',
        'partial_update': 'students.profile.change',
        'destroy': 'students.profile.change',
    }
