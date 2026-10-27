from apps.academics.models import TimetableSlot
from common.exceptions import BusinessConflictError


class AcademicsService:
    """
    Timetable validation and academic scheduling service.
    """

    @classmethod
    def validate_slot_conflicts(cls, academic_year, day_of_week, period, class_subject, room=None, exclude_slot_id=None):
        # 1. Teacher conflict check
        teacher = class_subject.teacher
        if teacher:
            teacher_conflict = TimetableSlot.objects.filter(
                academic_year=academic_year,
                day_of_week=day_of_week,
                period=period,
                class_subject__teacher=teacher
            )
            if exclude_slot_id:
                teacher_conflict = teacher_conflict.exclude(id=exclude_slot_id)
            if teacher_conflict.exists():
                raise BusinessConflictError(f"Teacher {teacher} is already assigned to another class during this period.")

        # 2. Room conflict check
        if room:
            room_conflict = TimetableSlot.objects.filter(
                academic_year=academic_year,
                day_of_week=day_of_week,
                period=period,
                room=room
            )
            if exclude_slot_id:
                room_conflict = room_conflict.exclude(id=exclude_slot_id)
            if room_conflict.exists():
                raise BusinessConflictError(f"Room {room} is already booked for another session in this period.")
