from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.attendance.models import Holiday, StudentDailyAttendance, AttendanceCorrectionRequest
from apps.attendance.serializers import (
    HolidaySerializer, StudentDailyAttendanceSerializer, AttendanceCorrectionRequestSerializer
)
from apps.rbac.permissions import HasDynamicPermission


class StudentDailyAttendanceViewSet(viewsets.ModelViewSet):
    queryset = StudentDailyAttendance.objects.select_related('student', 'academic_year').all()
    serializer_class = StudentDailyAttendanceSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    filterset_fields = ['class_name', 'section_name', 'date', 'status', 'academic_year']

    def perform_create(self, serializer):
        serializer.save(marked_by=self.request.user)

    @action(detail=False, methods=['post'])
    def mark_bulk(self, request):
        """
        Mark attendance for an entire class/section in bulk.
        """
        records = request.data.get('records', [])
        created_records = []
        for item in records:
            obj, _ = StudentDailyAttendance.objects.update_or_create(
                student_id=item['student_id'],
                date=item['date'],
                defaults={
                    'academic_year_id': item['academic_year_id'],
                    'class_name': item['class_name'],
                    'section_name': item['section_name'],
                    'status': item.get('status', 'PRESENT'),
                    'remarks': item.get('remarks', ''),
                    'marked_by': request.user
                }
            )
            created_records.append(obj.id)
        return Response({'status': 'success', 'updated_count': len(created_records)})


class HolidayViewSet(viewsets.ModelViewSet):
    queryset = Holiday.objects.all()
    serializer_class = HolidaySerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    filterset_fields = ['academic_year']


class AttendanceCorrectionRequestViewSet(viewsets.ModelViewSet):
    queryset = AttendanceCorrectionRequest.objects.all()
    serializer_class = AttendanceCorrectionRequestSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)
