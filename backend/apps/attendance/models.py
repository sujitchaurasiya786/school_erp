from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class AttendanceStatus(models.TextChoices):
    PRESENT = 'PRESENT', _('Present')
    ABSENT = 'ABSENT', _('Absent')
    HALF_DAY = 'HALF_DAY', _('Half Day')
    LEAVE = 'LEAVE', _('Approved Leave')


class Holiday(TimeStampedModel):
    """
    School calendar holidays and non-working days.
    """
    academic_year = models.ForeignKey('school_config.AcademicYear', on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    date = models.DateField(db_index=True)
    is_national_holiday = models.BooleanField(default=False)

    class Meta:
        db_table = 'attendance_holidays'
        unique_together = ('academic_year', 'date')

    def __str__(self):
        return f"{self.title} ({self.date})"


class StudentDailyAttendance(TimeStampedModel):
    """
    Daily attendance per student per date. One record per student per day.
    """
    student = models.ForeignKey(
        'students.Student',
        on_delete=models.CASCADE,
        related_name='daily_attendances',
        db_index=True
    )
    academic_year = models.ForeignKey('school_config.AcademicYear', on_delete=models.PROTECT)
    class_name = models.CharField(max_length=50, db_index=True)
    section_name = models.CharField(max_length=10, db_index=True)
    date = models.DateField(db_index=True)
    status = models.CharField(
        max_length=20,
        choices=AttendanceStatus.choices,
        default=AttendanceStatus.PRESENT
    )
    remarks = models.CharField(max_length=200, blank=True, default='')
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='marked_attendances'
    )

    class Meta:
        db_table = 'attendance_daily_records'
        unique_together = ('student', 'date')
        indexes = [
            models.Index(fields=['class_name', 'section_name', 'date']),
        ]

    def __str__(self):
        return f"{self.student.admission_number} - {self.date}: {self.status}"


class AttendanceCorrectionRequest(TimeStampedModel):
    """
    Authorized workflow for correcting marked attendance.
    """
    attendance = models.ForeignKey(StudentDailyAttendance, on_delete=models.CASCADE, related_name='correction_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='requested_corrections')
    new_status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    reason = models.TextField()
    is_approved = models.BooleanField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_corrections'
    )
    review_notes = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'attendance_corrections'

    def __str__(self):
        return f"Correction for {self.attendance} -> {self.new_status}"
