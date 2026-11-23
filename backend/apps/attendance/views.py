from datetime import date, datetime
from rest_framework import viewsets, status, serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.attendance.models import (
    Holiday, WorkingDayConfig, SpecialDayOverride,
    StudentDailyAttendance, AttendanceCorrectionRequest, WEEKDAY_CHOICES
)
from apps.attendance.serializers import (
    HolidaySerializer, VacationBreakSerializer,
    WorkingDayConfigSerializer, SpecialDayOverrideSerializer,
    DayStatusSerializer,
    StudentDailyAttendanceSerializer, AttendanceCorrectionRequestSerializer
)
from apps.attendance.services import CalendarService, StudentDailyAttendanceService
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.rbac.permissions import HasDynamicPermission
from apps.school_config.models import AcademicYear
from common.exceptions import BusinessConflictError, ResourceNotFoundError


class HolidayViewSet(viewsets.ModelViewSet):
    """
    CRUD API for school holidays and multi-day vacation breaks.
    """
    queryset = Holiday.objects.select_related('academic_year').all()
    serializer_class = HolidaySerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    action_permission_map = {
        'list': 'attendance.holiday.view',
        'retrieve': 'attendance.holiday.view',
        'create': 'attendance.holiday.manage',
        'update': 'attendance.holiday.manage',
        'partial_update': 'attendance.holiday.manage',
        'destroy': 'attendance.holiday.manage',
        'vacation_break': 'attendance.holiday.manage',
    }
    filterset_fields = ['academic_year', 'holiday_type', 'is_national_holiday', 'date']
    search_fields = ['title', 'description']

    def perform_create(self, serializer):
        try:
            holiday = serializer.save()
            AuditService.log(
                action=AuditAction.CREATE,
                module="attendance",
                entity_type="Holiday",
                entity_id=str(holiday.pk),
                actor=self.request.user,
                reason=f"Created holiday '{holiday.title}' on {holiday.date}",
                changes={
                    "title": holiday.title,
                    "date": str(holiday.date),
                    "academic_year": holiday.academic_year.name,
                    "holiday_type": holiday.holiday_type,
                    "is_national": holiday.is_national_holiday,
                },
                request=self.request
            )
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    def perform_update(self, serializer):
        old_obj = self.get_object()
        old_data = {
            "title": old_obj.title,
            "date": str(old_obj.date),
            "holiday_type": old_obj.holiday_type,
            "is_national": old_obj.is_national_holiday,
        }
        try:
            holiday = serializer.save()
            AuditService.log(
                action=AuditAction.UPDATE,
                module="attendance",
                entity_type="Holiday",
                entity_id=str(holiday.pk),
                actor=self.request.user,
                reason=f"Updated holiday '{holiday.title}'",
                changes={
                    "old": old_data,
                    "new": {
                        "title": holiday.title,
                        "date": str(holiday.date),
                        "holiday_type": holiday.holiday_type,
                        "is_national": holiday.is_national_holiday,
                    }
                },
                request=self.request
            )
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    def perform_destroy(self, instance):
        h_id = str(instance.pk)
        h_title = instance.title
        h_date = str(instance.date)
        instance.delete()
        AuditService.log(
            action=AuditAction.DELETE,
            module="attendance",
            entity_type="Holiday",
            entity_id=h_id,
            actor=self.request.user,
            reason=f"Deleted holiday '{h_title}' on {h_date}",
            changes={"title": h_title, "date": h_date},
            request=self.request
        )

    @action(detail=False, methods=['post'], url_path='vacation-break')
    def vacation_break(self, request):
        """
        Creates individual Holiday records across a date range atomically.
        """
        serializer = VacationBreakSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            created_holidays = CalendarService.create_vacation_break(
                academic_year=data['academic_year'],
                title=data['title'],
                start_date=data['start_date'],
                end_date=data['end_date'],
                holiday_type=data.get('holiday_type'),
                description=data.get('description', ''),
                is_national_holiday=data.get('is_national_holiday', False),
                actor=request.user,
                request=request
            )
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)
        except BusinessConflictError as err:
            return Response({'error': str(err)}, status=status.HTTP_409_CONFLICT)

        output_serializer = HolidaySerializer(created_holidays, many=True)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class WorkingDayConfigViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Academic-Year Working Day Configurations.
    """
    queryset = WorkingDayConfig.objects.select_related('academic_year').all()
    serializer_class = WorkingDayConfigSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    action_permission_map = {
        'list': 'attendance.calendar.view',
        'retrieve': 'attendance.calendar.view',
        'create': 'attendance.calendar.manage',
        'update': 'attendance.calendar.manage',
        'partial_update': 'attendance.calendar.manage',
        'destroy': 'attendance.calendar.manage',
    }
    filterset_fields = ['academic_year', 'saturday_rule']

    def perform_create(self, serializer):
        try:
            config = serializer.save()
            AuditService.log(
                action=AuditAction.CREATE,
                module="attendance",
                entity_type="WorkingDayConfig",
                entity_id=str(config.pk),
                actor=self.request.user,
                reason=f"Created working day config for academic year '{config.academic_year.name}'",
                changes={
                    "academic_year": config.academic_year.name,
                    "saturday_rule": config.saturday_rule,
                    "sunday": config.sunday,
                },
                request=self.request
            )
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    def perform_update(self, serializer):
        try:
            config = serializer.save()
            AuditService.log(
                action=AuditAction.UPDATE,
                module="attendance",
                entity_type="WorkingDayConfig",
                entity_id=str(config.pk),
                actor=self.request.user,
                reason=f"Updated working day config for academic year '{config.academic_year.name}'",
                changes={
                    "saturday_rule": config.saturday_rule,
                    "sunday": config.sunday,
                    "monday": config.monday,
                    "tuesday": config.tuesday,
                    "wednesday": config.wednesday,
                    "thursday": config.thursday,
                    "friday": config.friday,
                },
                request=self.request
            )
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)


class SpecialDayOverrideViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Special Working Day Overrides & Emergency Closures.
    """
    queryset = SpecialDayOverride.objects.select_related('academic_year', 'created_by').all()
    serializer_class = SpecialDayOverrideSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    action_permission_map = {
        'list': 'attendance.calendar.view',
        'retrieve': 'attendance.calendar.view',
        'create': 'attendance.calendar.manage',
        'update': 'attendance.calendar.manage',
        'partial_update': 'attendance.calendar.manage',
        'destroy': 'attendance.calendar.manage',
    }
    filterset_fields = ['academic_year', 'date', 'is_working', 'override_type']

    def perform_create(self, serializer):
        try:
            override = serializer.save(created_by=self.request.user)
            AuditService.log(
                action=AuditAction.CREATE,
                module="attendance",
                entity_type="SpecialDayOverride",
                entity_id=str(override.pk),
                actor=self.request.user,
                reason=f"Created special day override on {override.date} ({'Working' if override.is_working else 'Closed'}): {override.reason}",
                changes={
                    "date": str(override.date),
                    "is_working": override.is_working,
                    "override_type": override.override_type,
                    "timetable_weekday": override.timetable_weekday,
                    "reason": override.reason,
                },
                request=self.request
            )
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    def perform_update(self, serializer):
        try:
            override = serializer.save()
            AuditService.log(
                action=AuditAction.UPDATE,
                module="attendance",
                entity_type="SpecialDayOverride",
                entity_id=str(override.pk),
                actor=self.request.user,
                reason=f"Updated special day override on {override.date}",
                changes={
                    "date": str(override.date),
                    "is_working": override.is_working,
                    "override_type": override.override_type,
                    "timetable_weekday": override.timetable_weekday,
                    "reason": override.reason,
                },
                request=self.request
            )
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    def perform_destroy(self, instance):
        o_id = str(instance.pk)
        o_date = str(instance.date)
        instance.delete()
        AuditService.log(
            action=AuditAction.DELETE,
            module="attendance",
            entity_type="SpecialDayOverride",
            entity_id=o_id,
            actor=self.request.user,
            reason=f"Deleted special day override on {o_date}",
            changes={"date": o_date},
            request=self.request
        )


class CalendarResolutionViewSet(viewsets.ViewSet):
    """
    Endpoints for query-based calendar resolution and operational day lookups.
    """
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    action_permission_map = {
        'list': 'attendance.calendar.view',
        'day_status': 'attendance.calendar.view',
        'summary': 'attendance.calendar.view',
        'active_days': 'attendance.calendar.view',
    }

    def list(self, request):
        return self.summary(request)

    def _get_academic_year(self, request):
        ay_id = request.query_params.get('academic_year')
        if ay_id:
            try:
                return AcademicYear.objects.get(pk=ay_id)
            except AcademicYear.DoesNotExist:
                raise drf_serializers.ValidationError({'academic_year': f"Academic year with ID {ay_id} not found."})
        current_ay = AcademicYear.objects.filter(is_current=True).first()
        if not current_ay:
            raise drf_serializers.ValidationError({'academic_year': "No active academic year found. Please specify academic_year query param."})
        return current_ay

    @action(detail=False, methods=['get'], url_path='day-status')
    def day_status(self, request):
        """
        Returns resolution status for a single date.
        Query params: date (YYYY-MM-DD), academic_year (optional).
        """
        ay = self._get_academic_year(request)
        target_date_str = request.query_params.get('date')
        if not target_date_str:
            raise drf_serializers.ValidationError({'date': "Query parameter 'date' (YYYY-MM-DD) is required."})

        try:
            status_data = CalendarService.get_day_status(ay, target_date_str)
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

        tt_weekday = status_data.get('timetable_weekday')
        weekday_dict = dict(WEEKDAY_CHOICES)
        tt_display = weekday_dict.get(tt_weekday) if tt_weekday else None

        return Response({
            'academic_year': ay.pk,
            'academic_year_name': ay.name,
            'date': status_data.get('actual_date'),
            'is_working': status_data.get('is_working'),
            'reason': status_data.get('reason'),
            'type': status_data.get('type'),
            'timetable_weekday': tt_weekday,
            'timetable_weekday_display': tt_display,
        })

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """
        Returns working days and holiday breakdown across a date range.
        Query params: start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), academic_year (optional).
        """
        ay = self._get_academic_year(request)
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        if not start_date_str or not end_date_str:
            raise drf_serializers.ValidationError({
                'dates': "Both 'start_date' and 'end_date' query parameters are required."
            })

        try:
            working_dates = CalendarService.get_working_days_in_range(ay, start_date_str, end_date_str)
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

        s_date = CalendarService._coerce_date(start_date_str)
        e_date = CalendarService._coerce_date(end_date_str)
        total_calendar_days = (e_date - s_date).days + 1

        holidays_count = Holiday.objects.filter(
            academic_year=ay, date__range=[s_date, e_date]
        ).count()
        overrides_count = SpecialDayOverride.objects.filter(
            academic_year=ay, date__range=[s_date, e_date]
        ).count()

        return Response({
            'academic_year': ay.pk,
            'academic_year_name': ay.name,
            'start_date': str(s_date),
            'end_date': str(e_date),
            'total_calendar_days': total_calendar_days,
            'working_days_count': len(working_dates),
            'holidays_count': holidays_count,
            'overrides_count': overrides_count,
            'working_days': [str(d) for d in working_dates],
        })

    @action(detail=False, methods=['get'], url_path='active-days')
    def active_days(self, request):
        """
        Returns active timetable weekdays (1-7) for the academic year.
        """
        ay = self._get_academic_year(request)
        active_day_codes = CalendarService.get_active_timetable_days(ay)
        weekday_dict = dict(WEEKDAY_CHOICES)
        return Response({
            'academic_year': ay.pk,
            'academic_year_name': ay.name,
            'active_day_codes': active_day_codes,
            'active_days': [{'code': code, 'name': str(weekday_dict.get(code))} for code in active_day_codes],
        })


class StudentDailyAttendanceViewSet(viewsets.ModelViewSet):
    queryset = StudentDailyAttendance.objects.select_related('student', 'academic_year').all()
    serializer_class = StudentDailyAttendanceSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    action_permission_map = {
        'list': 'attendance.record.view',
        'retrieve': 'attendance.record.view',
        'roster': 'attendance.record.view',
        'create': 'attendance.record.mark',
        'update': 'attendance.record.mark',
        'partial_update': 'attendance.record.mark',
        'destroy': 'attendance.record.mark',
        'mark_bulk': 'attendance.record.mark',
    }
    filterset_fields = ['class_name', 'section_name', 'date', 'status', 'academic_year']

    @action(detail=False, methods=['get'])
    def roster(self, request):
        """
        Retrieves section roster with attendance for an academic year, class, section, date.
        """
        ay_id = request.query_params.get('academic_year')
        class_name = request.query_params.get('class_name')
        section_name = request.query_params.get('section_name')
        date_val = request.query_params.get('date', str(date.today()))

        if not class_name or not section_name:
            return Response({'error': "class_name and section_name are required."}, status=status.HTTP_400_BAD_REQUEST)

        current_ay = None
        if ay_id:
            try:
                current_ay = AcademicYear.objects.get(pk=ay_id)
            except AcademicYear.DoesNotExist:
                return Response({'error': f"Academic year {ay_id} not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            current_ay = AcademicYear.objects.filter(is_current=True).first()
            if not current_ay:
                return Response({'error': "No active academic year found."}, status=status.HTTP_400_BAD_REQUEST)

        can_mark = StudentDailyAttendanceService.validate_user_attendance_scope(request.user, class_name, section_name)
        if not can_mark and not request.user.is_superuser:
            return Response({'error': "You are not authorized to view attendance for this section."}, status=status.HTTP_403_FORBIDDEN)

        try:
            data = StudentDailyAttendanceService.get_section_roster_with_attendance(
                academic_year=current_ay,
                class_name=class_name,
                section_name=section_name,
                target_date=date_val
            )
        except DjangoValidationError as err:
            return Response({'error': err.message_dict if hasattr(err, 'message_dict') else err.messages}, status=status.HTTP_400_BAD_REQUEST)

        roster_data = []
        for r in data['roster']:
            roster_data.append({
                'student_id': r['student'].id,
                'admission_number': r['student'].admission_number,
                'name': r['student'].full_name,
                'roll_number': r['roll_number'],
                'status': r['current_status'],
                'remarks': r['remarks'],
                'attendance_id': r['attendance'].id if r['attendance'] else None,
                'pending_correction': bool(r['pending_correction']),
                'pending_correction_id': r['pending_correction'].id if r['pending_correction'] else None,
            })

        return Response({
            'academic_year': current_ay.id,
            'academic_year_name': current_ay.name,
            'class_name': class_name,
            'section_name': section_name,
            'date': str(data['date']),
            'is_working': data['is_working'],
            'calendar_reason': data['calendar_status'].get('reason'),
            'calendar_type': data['calendar_status'].get('type'),
            'is_already_marked': data['is_already_marked'],
            'counts': data['counts'],
            'roster': roster_data
        })

    def perform_create(self, serializer):
        data = serializer.validated_data
        try:
            record = StudentDailyAttendanceService.mark_attendance(
                student_id=data['student'].pk,
                academic_year_id=data['academic_year'].pk,
                attendance_date=data['date'],
                class_name=data.get('class_name', ''),
                section_name=data.get('section_name', ''),
                status=data.get('status'),
                remarks=data.get('remarks', ''),
                actor=self.request.user,
                request=self.request
            )
            serializer.instance = record
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    @action(detail=False, methods=['post'])
    def mark_bulk(self, request):
        """
        Mark attendance for an entire class/section in bulk with calendar validation.
        """
        records = request.data.get('records', [])
        if not records:
            return Response({'error': 'No records provided.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            created_ids = StudentDailyAttendanceService.mark_bulk_attendance(
                records=records,
                actor=request.user,
                request=request
            )
            return Response({'status': 'success', 'updated_count': len(created_ids), 'ids': created_ids})
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)
        except ResourceNotFoundError as err:
            return Response({'error': str(err)}, status=status.HTTP_404_NOT_FOUND)


class AttendanceCorrectionRequestViewSet(viewsets.ModelViewSet):
    queryset = AttendanceCorrectionRequest.objects.select_related('attendance', 'attendance__student', 'requested_by', 'reviewed_by').all()
    serializer_class = AttendanceCorrectionRequestSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'attendance'
    action_permission_map = {
        'list': 'attendance.record.view',
        'retrieve': 'attendance.record.view',
        'create': 'attendance.record.mark',
        'update': 'attendance.correction.approve',
        'partial_update': 'attendance.correction.approve',
        'destroy': 'attendance.correction.approve',
        'review': 'attendance.correction.approve',
    }

    def perform_create(self, serializer):
        data = serializer.validated_data
        try:
            req = StudentDailyAttendanceService.submit_correction_request(
                attendance_id=data['attendance'].pk,
                new_status=data['new_status'],
                reason=data.get('reason', ''),
                user=self.request.user,
                request=self.request
            )
            serializer.instance = req
        except DjangoValidationError as err:
            raise drf_serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)
        except BusinessConflictError as err:
            raise drf_serializers.ValidationError({'detail': str(err)})

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        """
        Review (approve or reject) an attendance correction request.
        """
        is_approved = request.data.get('is_approved')
        if is_approved is None:
            return Response({'error': "'is_approved' boolean parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
        review_notes = request.data.get('review_notes', '')

        try:
            req = StudentDailyAttendanceService.review_correction_request(
                request_id=pk,
                is_approved=is_approved,
                review_notes=review_notes,
                user=request.user,
                request=request
            )
            return Response(AttendanceCorrectionRequestSerializer(req).data)
        except BusinessConflictError as err:
            return Response({'error': str(err)}, status=status.HTTP_400_BAD_REQUEST)
        except ResourceNotFoundError as err:
            return Response({'error': str(err)}, status=status.HTTP_404_NOT_FOUND)
