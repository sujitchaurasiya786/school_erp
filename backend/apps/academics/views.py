from rest_framework import serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from apps.academics.models import (
    Room, RoomType, SchoolClass, Section, Subject, ClassSubject, TimetablePeriod, TimetableSlot
)
from apps.academics.serializers import (
    RoomSerializer, SchoolClassSerializer, SectionSerializer, SubjectSerializer,
    ClassSubjectSerializer, TimetablePeriodSerializer, TimetableSlotSerializer
)
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError
from apps.academics.services import (
    AcademicsService, ClassSubjectService, RoomService, TeacherAssignmentService,
    TimetablePeriodService, TimetableService
)
from apps.rbac.permissions import HasDynamicPermission
from common.exceptions import BusinessConflictError, ResourceNotFoundError


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

    @action(detail=True, methods=['post'], url_path='assign-class-teacher')
    def assign_class_teacher(self, request, pk=None):
        teacher_id = request.data.get('teacher_id')
        force = bool(request.data.get('force', False))
        reason = request.data.get('reason', '')
        if not teacher_id:
            return Response({'error': 'teacher_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            section = TeacherAssignmentService.assign_class_teacher(
                section_id=pk,
                teacher_id=teacher_id,
                actor=request.user,
                reason=reason or f"API class teacher assignment to section {pk}",
                request=request,
                force=force,
            )
            return Response(self.get_serializer(section).data, status=status.HTTP_200_OK)
        except (ValidationError, ResourceNotFoundError, BusinessConflictError) as err:
            msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='remove-class-teacher')
    def remove_class_teacher(self, request, pk=None):
        reason = request.data.get('reason', '')
        try:
            section = TeacherAssignmentService.remove_class_teacher(
                section_id=pk,
                actor=request.user,
                reason=reason or f"API unassigned class teacher from section {pk}",
                request=request,
            )
            return Response(self.get_serializer(section).data, status=status.HTTP_200_OK)
        except (ResourceNotFoundError, ValidationError) as err:
            msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)


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

    @action(detail=True, methods=['post'], url_path='assign-teacher')
    def assign_teacher(self, request, pk=None):
        teacher_id = request.data.get('teacher_id')
        reason = request.data.get('reason', '')
        if not teacher_id:
            return Response({'error': 'teacher_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            mapping = TeacherAssignmentService.assign_subject_teacher(
                class_subject_id=pk,
                teacher_id=teacher_id,
                actor=request.user,
                reason=reason or f"API assigned subject teacher to mapping {pk}",
                request=request,
            )
            return Response(self.get_serializer(mapping).data, status=status.HTTP_200_OK)
        except (ValidationError, ResourceNotFoundError, BusinessConflictError) as err:
            msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='remove-teacher')
    def remove_teacher(self, request, pk=None):
        reason = request.data.get('reason', '')
        try:
            mapping = TeacherAssignmentService.remove_subject_teacher(
                class_subject_id=pk,
                actor=request.user,
                reason=reason or f"API unassigned subject teacher from mapping {pk}",
                request=request,
            )
            return Response(self.get_serializer(mapping).data, status=status.HTTP_200_OK)
        except (ResourceNotFoundError, ValidationError) as err:
            msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='bulk-assign-teacher')
    def bulk_assign_teacher(self, request):
        class_subject_ids = request.data.get('class_subjects', [])
        if hasattr(request.data, 'getlist'):
            getlist_val = request.data.getlist('class_subjects')
            if getlist_val:
                class_subject_ids = getlist_val
        if isinstance(class_subject_ids, str):
            class_subject_ids = [s.strip() for s in class_subject_ids.split(',') if s.strip()]
        elif not isinstance(class_subject_ids, (list, tuple, set)):
            class_subject_ids = [class_subject_ids]

        teacher_id = request.data.get('teacher_id')
        reason = request.data.get('reason', '')
        if not teacher_id:
            return Response({'error': 'teacher_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not class_subject_ids:
            return Response({'error': 'class_subjects list is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            count = TeacherAssignmentService.bulk_assign_subject_teacher(
                class_subject_ids=class_subject_ids,
                teacher_id=teacher_id,
                actor=request.user,
                reason=reason or f"API bulk assigned subject teacher to {len(class_subject_ids)} mappings",
                request=request,
            )
            return Response({'assigned_count': count}, status=status.HTTP_200_OK)
        except (ValidationError, ResourceNotFoundError) as err:
            msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='faculty-workload')
    def faculty_workload(self, request):
        workload = TeacherAssignmentService.get_faculty_workload_summary()
        results = [
            {
                'teacher_id': w['teacher'].pk,
                'teacher_name': w['teacher'].get_full_name() or w['teacher'].username,
                'teacher_email': w['teacher'].email,
                'total_subjects': w['total_subjects'],
                'distinct_classes': w['distinct_classes'],
                'intensity': w['intensity'],
                'class_teacher_sections': [str(s) for s in w['class_teacher_sections']],
            }
            for w in workload
        ]
        return Response(results, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='eligible-faculty')
    def eligible_faculty(self, request):
        qs = TeacherAssignmentService.get_eligible_faculty_queryset()
        results = [
            {
                'id': u.pk,
                'username': u.username,
                'full_name': u.get_full_name() or u.username,
                'email': u.email,
                'user_type': u.user_type,
            }
            for u in qs
        ]
        return Response(results, status=status.HTTP_200_OK)


class TimetablePeriodViewSet(viewsets.ModelViewSet):
    queryset = TimetablePeriod.objects.all()
    serializer_class = TimetablePeriodSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'

    @action(detail=False, methods=['post'], url_path='generate-cbse-defaults')
    def generate_cbse_defaults(self, request):
        try:
            periods = TimetablePeriodService.bulk_create_cbse_standard_periods(
                actor=request.user,
                request=request
            )
            serializer = self.get_serializer(periods, many=True)
            return Response({
                'created_count': len(periods),
                'periods': serializer.data
            }, status=status.HTTP_201_CREATED)
        except (BusinessConflictError, ValidationError) as err:
            msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)


class RoomViewSet(viewsets.ModelViewSet):
    queryset = Room.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'
    action_permission_map = {
        'list': 'academics.room.view',
        'retrieve': 'academics.room.view',
        'create': 'academics.room.manage',
        'update': 'academics.room.manage',
        'partial_update': 'academics.room.manage',
        'destroy': 'academics.room.manage',
        'toggle_status': 'academics.room.manage',
    }
    filterset_fields = ['room_type', 'is_active']
    search_fields = ['room_number', 'name', 'floor']

    def perform_create(self, serializer):
        data = serializer.validated_data
        try:
            room = RoomService.create_room(
                room_number=data.get('room_number'),
                room_type=data.get('room_type', RoomType.CLASSROOM),
                capacity=data.get('capacity', 40),
                name=data.get('name', ''),
                floor=data.get('floor', ''),
                description=data.get('description', ''),
                is_active=data.get('is_active', True),
                actor=self.request.user,
                request=self.request,
            )
            serializer.instance = room
        except ValidationError as err:
            raise serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    def perform_update(self, serializer):
        data = serializer.validated_data
        try:
            room = RoomService.update_room(
                room_id=serializer.instance.pk,
                actor=self.request.user,
                request=self.request,
                **data
            )
            serializer.instance = room
            if hasattr(serializer, '_data'):
                del serializer._data
        except ValidationError as err:
            raise serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    def perform_destroy(self, instance):
        try:
            RoomService.delete_room(
                room_id=instance.pk,
                actor=self.request.user,
                request=self.request,
            )
        except ValidationError as err:
            raise serializers.ValidationError(err.message_dict if hasattr(err, 'message_dict') else err.messages)

    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        room = self.get_object()
        new_status = not room.is_active
        if 'is_active' in request.data:
            val = request.data['is_active']
            if isinstance(val, str):
                new_status = val.strip().lower() in ('true', '1', 't', 'yes', 'on')
            else:
                new_status = bool(val)
        try:
            room = RoomService.toggle_room_status(
                room_id=room.pk,
                is_active=new_status,
                actor=request.user,
                request=request,
            )
            return Response(self.get_serializer(room).data, status=status.HTTP_200_OK)
        except ValidationError as err:
            msg = "; ".join(err.messages) if hasattr(err, 'messages') else str(err)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)


class TimetableSlotViewSet(viewsets.ModelViewSet):
    queryset = TimetableSlot.objects.select_related(
        'academic_year', 'period', 'class_subject', 'class_subject__section',
        'class_subject__subject', 'class_subject__teacher', 'room_ref'
    ).all()
    serializer_class = TimetableSlotSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'academics'
    filterset_fields = ['academic_year', 'day_of_week', 'class_subject__section', 'period']

    def perform_create(self, serializer):
        data = serializer.validated_data
        room_val = data.get('room_ref') or data.get('room')
        AcademicsService.validate_slot_conflicts(
            data['academic_year'], data['day_of_week'], data['period'],
            data['class_subject'], room_val
        )
        if data.get('room_ref') and not data.get('room'):
            serializer.validated_data['room'] = data['room_ref'].room_number
        serializer.save()

    def perform_update(self, serializer):
        data = serializer.validated_data
        academic_year = data.get('academic_year', serializer.instance.academic_year)
        day_of_week = data.get('day_of_week', serializer.instance.day_of_week)
        period = data.get('period', serializer.instance.period)
        class_subject = data.get('class_subject', serializer.instance.class_subject)
        room_val = data.get('room_ref', serializer.instance.room_ref) or data.get('room', serializer.instance.room)

        AcademicsService.validate_slot_conflicts(
            academic_year=academic_year,
            day_of_week=day_of_week,
            period=period,
            class_subject=class_subject,
            room=room_val,
            exclude_slot_id=serializer.instance.pk
        )
        if data.get('room_ref') and not data.get('room'):
            serializer.validated_data['room'] = data['room_ref'].room_number
        serializer.save()

    @action(detail=False, methods=['get'], url_path='section-matrix')
    def section_matrix(self, request):
        section_id = request.GET.get('section_id')
        if not section_id:
            return Response({'error': 'section_id query parameter is required.'}, status=status.HTTP_400_BAD_REQUEST)
        academic_year_id = request.GET.get('academic_year_id')
        matrix = TimetableService.get_section_timetable_matrix(
            section_id=section_id,
            academic_year_id=academic_year_id
        )

        formatted_grid = []
        for row in matrix['grid']:
            formatted_periods = []
            for cell in row['periods']:
                slot_data = None
                if cell['slot']:
                    slot_data = self.get_serializer(cell['slot']).data
                formatted_periods.append({
                    'period_id': cell['period'].pk,
                    'period_number': cell['period'].period_number,
                    'period_name': cell['period'].name,
                    'start_time': str(cell['period'].start_time),
                    'end_time': str(cell['period'].end_time),
                    'is_break': cell['is_break'],
                    'slot': slot_data,
                })
            formatted_grid.append({
                'day_code': row['day_code'],
                'day_name': row['day_name'],
                'periods': formatted_periods,
            })

        return Response({
            'total_scheduled': matrix['total_scheduled'],
            'subject_counts': matrix['subject_counts'],
            'grid': formatted_grid,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='teacher-matrix')
    def teacher_matrix(self, request):
        teacher_id = request.GET.get('teacher_id')
        if not teacher_id:
            return Response({'error': 'teacher_id query parameter is required.'}, status=status.HTTP_400_BAD_REQUEST)
        academic_year_id = request.GET.get('academic_year_id')
        matrix = TimetableService.get_teacher_timetable_matrix(
            teacher_id=teacher_id,
            academic_year_id=academic_year_id
        )

        formatted_grid = []
        for row in matrix['grid']:
            formatted_periods = []
            for cell in row['periods']:
                slot_data = None
                if cell['slot']:
                    slot_data = self.get_serializer(cell['slot']).data
                formatted_periods.append({
                    'period_id': cell['period'].pk,
                    'period_number': cell['period'].period_number,
                    'period_name': cell['period'].name,
                    'start_time': str(cell['period'].start_time),
                    'end_time': str(cell['period'].end_time),
                    'is_break': cell['is_break'],
                    'slot': slot_data,
                })
            formatted_grid.append({
                'day_code': row['day_code'],
                'day_name': row['day_name'],
                'periods': formatted_periods,
            })

        return Response({
            'total_scheduled': matrix['total_scheduled'],
            'sections_covered': matrix['sections_covered'],
            'grid': formatted_grid,
        }, status=status.HTTP_200_OK)
