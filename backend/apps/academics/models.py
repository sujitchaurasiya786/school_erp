from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class Stream(models.TextChoices):
    NONE = 'NONE', _('General (Nursery-10)')
    SCIENCE = 'SCIENCE', _('Science')
    COMMERCE = 'COMMERCE', _('Commerce')
    ARTS = 'ARTS', _('Humanities / Arts')


class SchoolClass(TimeStampedModel):
    """
    CBSE Class (Nursery, LKG, UKG, Class 1 ... Class 12).
    """
    name = models.CharField(max_length=50, unique=True, help_text=_("e.g. Class 10"))
    code = models.CharField(max_length=20, unique=True, help_text=_("e.g. CL-10"))
    order = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'academic_classes'
        ordering = ['order']

    def __str__(self):
        return self.name


class Section(TimeStampedModel):
    """
    Section within a class (e.g. Section A, Section B).
    """
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=10, help_text=_("e.g. A, B, C"))
    stream = models.CharField(max_length=20, choices=Stream.choices, default=Stream.NONE)
    class_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='class_teacher_sections'
    )
    room_number = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        db_table = 'academic_sections'
        unique_together = ('school_class', 'name', 'stream')

    def __str__(self):
        stream_str = f" ({self.stream})" if self.stream != Stream.NONE else ""
        return f"{self.school_class.name} - {self.name}{stream_str}"


class Subject(TimeStampedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    is_theory = models.BooleanField(default=True)
    is_practical = models.BooleanField(default=False)

    class Meta:
        db_table = 'academic_subjects'

    def __str__(self):
        return f"{self.name} ({self.code})"


class ClassSubject(TimeStampedModel):
    """
    Subject mapped to a Class-Section with teacher assignment.
    """
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='class_subjects')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='subject_mappings')
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teaching_subjects'
    )
    is_elective = models.BooleanField(default=False)

    class Meta:
        db_table = 'academic_class_subjects'
        unique_together = ('section', 'subject')

    def __str__(self):
        return f"{self.section} - {self.subject.name}"


class TimetablePeriod(TimeStampedModel):
    """
    Period slot definition (e.g. Period 1: 08:30 - 09:15).
    """
    period_number = models.PositiveIntegerField()
    name = models.CharField(max_length=50, default='Period')
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_break = models.BooleanField(default=False)

    class Meta:
        db_table = 'academic_timetable_periods'
        ordering = ['period_number']

    def __str__(self):
        return f"{self.name} {self.period_number} ({self.start_time} - {self.end_time})"


class TimetableSlot(TimeStampedModel):
    """
    Conflict-checked timetable schedule.
    """
    DAYS_OF_WEEK = [
        (1, 'Monday'), (2, 'Tuesday'), (3, 'Wednesday'),
        (4, 'Thursday'), (5, 'Friday'), (6, 'Saturday')
    ]
    academic_year = models.ForeignKey('school_config.AcademicYear', on_delete=models.CASCADE)
    day_of_week = models.PositiveSmallIntegerField(choices=DAYS_OF_WEEK)
    period = models.ForeignKey(TimetablePeriod, on_delete=models.CASCADE)
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.CASCADE)
    room = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        db_table = 'academic_timetable_slots'
        unique_together = ('academic_year', 'day_of_week', 'period', 'class_subject')

    def __str__(self):
        return f"{self.get_day_of_week_display()} {self.period}: {self.class_subject}"
