from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.students.models import Student, Guardian, ClassEnrollment, TransferCertificate
from apps.students.serializers import (
    StudentSerializer, GuardianSerializer, ClassEnrollmentSerializer, TransferCertificateSerializer
)
from apps.students.services import StudentService
from apps.rbac.permissions import HasDynamicPermission


class StudentViewSet(viewsets.ModelViewSet):
    """
    Manage student profiles. Students/parents see only their own data.
    """
    queryset = Student.objects.select_related('primary_guardian').all()
    serializer_class = StudentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    filterset_fields = ['status', 'gender', 'admission_date']
    search_fields = ['admission_number', 'first_name', 'last_name', 'primary_guardian__phone']

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.user_type in ('ADMIN', 'PRINCIPAL', 'TEACHER', 'ACCOUNTANT'):
            return self.queryset
        if user.user_type == 'STUDENT' and hasattr(user, 'student_profile'):
            return self.queryset.filter(id=user.student_profile.id)
        if user.user_type == 'PARENT' and hasattr(user, 'guardian_profile'):
            return self.queryset.filter(primary_guardian=user.guardian_profile)
        return self.queryset.none()

    @action(detail=True, methods=['post'])
    def issue_tc(self, request, pk=None):
        reason = request.data.get('reason', 'Parent request')
        dues_cleared = request.data.get('dues_cleared', True)
        tc = StudentService.issue_transfer_certificate(pk, reason, request.user, dues_cleared)
        return Response(TransferCertificateSerializer(tc).data)


class GuardianViewSet(viewsets.ModelViewSet):
    queryset = Guardian.objects.all()
    serializer_class = GuardianSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    search_fields = ['first_name', 'last_name', 'phone', 'email']


class ClassEnrollmentViewSet(viewsets.ModelViewSet):
    queryset = ClassEnrollment.objects.select_related('student', 'academic_year').all()
    serializer_class = ClassEnrollmentSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    filterset_fields = ['academic_year', 'class_name', 'section_name']
    search_fields = ['student__admission_number', 'student__first_name', 'student__last_name']

    @action(detail=False, methods=['post'])
    def promote(self, request):
        target_year_id = request.data.get('academic_year_id')
        target_class = request.data.get('class_name')
        target_section = request.data.get('section_name')
        student_ids = request.data.get('student_ids', [])

        from apps.school_config.models import AcademicYear
        target_year = AcademicYear.objects.get(id=target_year_id)

        promoted = StudentService.promote_students(
            target_year, target_class, target_section, student_ids, request.user
        )
        return Response({'status': 'success', 'promoted_count': len(promoted)})


class TransferCertificateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TransferCertificate.objects.select_related('student').all()
    serializer_class = TransferCertificateSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'students'
    search_fields = ['tc_number', 'student__admission_number']
