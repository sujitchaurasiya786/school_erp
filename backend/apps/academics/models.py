from django.conf import settings
from django.core.exceptions import ValidationError
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

    @property
    def is_senior_secondary(self):
        """
        Returns True if the class is Class 11 or Class 12.
        Matches standard CBSE codes and names (e.g. CL-11, CL-12, 11, 12, XI, XII).
        """
        import re
        normalized_code = (self.code or '').strip().upper()
        normalized_name = (self.name or '').strip().upper()
        senior_tokens = {'11', '12', 'XI', 'XII', 'CL-11', 'CL-12', 'CLASS 11', 'CLASS 12', 'CLASS XI', 'CLASS XII'}
        if normalized_code in senior_tokens or normalized_name in senior_tokens:
            return True
        if re.search(r'\b(11|12|XI|XII)\b', normalized_name) or re.search(r'\b(11|12|XI|XII)\b', normalized_code):
            return True
        return False

    def clean(self):
        super().clean()
        if self.name:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError({'name': _("Class name cannot be empty.")})
            qs = SchoolClass.objects.filter(name__iexact=self.name)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'name': _(f"A class with name '{self.name}' already exists.")})
        else:
            raise ValidationError({'name': _("Class name is required.")})

        if self.code:
            self.code = str(self.code).strip().upper()
            if not self.code:
                raise ValidationError({'code': _("Class code cannot be empty.")})
            qs = SchoolClass.objects.filter(code__iexact=self.code)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'code': _(f"A class with code '{self.code}' already exists.")})
        else:
            raise ValidationError({'code': _("Class code is required.")})

        if self.order is not None and self.order < 1:
            raise ValidationError({'order': _("Display order must be a positive integer (>= 1).")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class Section(TimeStampedModel):
    """
    Section within a class (e.g. Section A, Section B).
    """
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=10, help_text=_("e.g. A, B, C"))
    capacity = models.PositiveIntegerField(default=40, help_text=_("Maximum student capacity"))
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
        ordering = ['school_class__order', 'name']

    def __str__(self):
        stream_str = f" ({self.stream})" if self.stream != Stream.NONE else ""
        return f"{self.school_class.name} - {self.name}{stream_str}"

    def clean(self):
        super().clean()
        if not hasattr(self, 'school_class') or self.school_class_id is None:
            raise ValidationError({'school_class': _("School class is required.")})

        if self.name:
            self.name = str(self.name).strip().upper()
            if not self.name:
                raise ValidationError({'name': _("Section name cannot be empty.")})
            qs = Section.objects.filter(
                school_class=self.school_class,
                name__iexact=self.name,
                stream=self.stream or Stream.NONE
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({
                    'name': _(f"Section '{self.name}' already exists for class '{self.school_class.name}'.")
                })
        else:
            raise ValidationError({'name': _("Section name is required.")})

        if self.capacity is not None and self.capacity < 1:
            raise ValidationError({'capacity': _("Capacity must be a positive integer (>= 1).")})

        if self.school_class.is_senior_secondary:
            if not self.stream or self.stream == Stream.NONE:
                raise ValidationError({
                    'stream': _(f"Senior secondary classes (Classes 11 and 12) must be assigned a valid academic stream (e.g. Science, Commerce, Arts). 'General' is not permitted for '{self.school_class.name}'.")
                })
        else:
            if self.stream and self.stream != Stream.NONE:
                raise ValidationError({
                    'stream': _(f"Streams are applicable only to Classes 11 and 12. '{self.school_class.name}' cannot have a stream.")
                })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class AcademicStream(TimeStampedModel):
    """
    Configurable academic streams for senior-secondary classes (Classes 11 and 12).
    E.g. Science, Commerce, Humanities / Arts.
    """
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.CASCADE,
        related_name='academic_streams',
        help_text=_("Must be Class 11 or Class 12.")
    )
    name = models.CharField(max_length=50, help_text=_("e.g. Science, Commerce, Arts"))
    code = models.CharField(max_length=20, help_text=_("e.g. SCI, COMM, ARTS"))
    description = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'academic_streams'
        unique_together = (('school_class', 'name'), ('school_class', 'code'))
        ordering = ['school_class__order', 'name']

    def __str__(self):
        return f"{self.school_class.name} - {self.name} ({self.code})"

    def clean(self):
        super().clean()
        if not hasattr(self, 'school_class') or self.school_class_id is None:
            raise ValidationError({'school_class': _("School class is required.")})

        if not self.school_class.is_senior_secondary:
            raise ValidationError({
                'school_class': _(f"Streams can only be configured for senior-secondary classes (Class 11 and 12). '{self.school_class.name}' is not eligible.")
            })

        if self.name:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError({'name': _("Stream name cannot be empty.")})
            qs = AcademicStream.objects.filter(school_class=self.school_class, name__iexact=self.name)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'name': _(f"A stream named '{self.name}' already exists for {self.school_class.name}.")})
        else:
            raise ValidationError({'name': _("Stream name is required.")})

        if self.code:
            self.code = str(self.code).strip().upper()
            if not self.code:
                raise ValidationError({'code': _("Stream code cannot be empty.")})
            qs = AcademicStream.objects.filter(school_class=self.school_class, code__iexact=self.code)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'code': _(f"A stream with code '{self.code}' already exists for {self.school_class.name}.")})
        else:
            raise ValidationError({'code': _("Stream code is required.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class SubjectType(models.TextChoices):
    COMPULSORY = 'COMPULSORY', _('Compulsory')
    OPTIONAL = 'OPTIONAL', _('Optional')
    ELECTIVE = 'ELECTIVE', _('Elective')


class Subject(TimeStampedModel):
    name = models.CharField(max_length=100, help_text=_("e.g. Mathematics, English, Physics"))
    code = models.CharField(max_length=20, unique=True, help_text=_("Unique subject code, e.g. MATH-10, PHY-11"))
    subject_type = models.CharField(
        max_length=20,
        choices=SubjectType.choices,
        default=SubjectType.COMPULSORY,
        help_text=_("Default subject classification")
    )
    is_theory = models.BooleanField(default=True, help_text=_("Has written/theory examination"))
    is_practical = models.BooleanField(default=False, help_text=_("Has laboratory/practical examination"))
    description = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'academic_subjects'
        ordering = ['name', 'code']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def clean(self):
        super().clean()
        if self.name:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError({'name': _("Subject name cannot be empty.")})
        else:
            raise ValidationError({'name': _("Subject name is required.")})

        if self.code:
            self.code = str(self.code).strip().upper()
            if not self.code:
                raise ValidationError({'code': _("Subject code cannot be empty.")})
            qs = Subject.objects.filter(code__iexact=self.code)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'code': _(f"A subject with code '{self.code}' already exists.")})
        else:
            raise ValidationError({'code': _("Subject code is required.")})

        if not self.is_theory and not self.is_practical:
            raise ValidationError(_("A subject must include at least one component: Theory, Practical, or both."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ClassSubject(TimeStampedModel):
    """
    Subject mapped to a Class-Section with curriculum classification and optional teacher assignment.
    """
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='class_subjects')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='subject_mappings')
    subject_type = models.CharField(
        max_length=20,
        choices=SubjectType.choices,
        default=SubjectType.COMPULSORY,
        help_text=_("Subject classification for this section (Compulsory, Optional, Elective)")
    )
    is_elective = models.BooleanField(default=False, help_text=_("Flag indicating whether subject is elective"))
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='class_subjects',
        help_text=_("Associated academic session context")
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teaching_subjects',
        help_text=_("Assigned subject teacher (optional)")
    )

    class Meta:
        db_table = 'academic_class_subjects'
        unique_together = ('section', 'subject')
        ordering = ['section__school_class__order', 'section__name', 'subject__name']

    def __str__(self):
        return f"{self.section} - {self.subject.name} ({self.get_subject_type_display()})"

    def clean(self):
        super().clean()
        if not hasattr(self, 'section') or self.section_id is None:
            raise ValidationError({'section': _("Section is required.")})

        if not hasattr(self, 'subject') or self.subject_id is None:
            raise ValidationError({'subject': _("Subject is required.")})

        if self.subject_id and hasattr(self, 'subject'):
            if not self.subject.is_active:
                raise ValidationError({'subject': _(f"Cannot map inactive subject '{self.subject.name}'.")})

        # Sync is_elective boolean flag with subject_type choice
        if self.subject_type == SubjectType.ELECTIVE:
            self.is_elective = True
        elif not self.is_elective:
            self.is_elective = False

        qs = ClassSubject.objects.filter(section=self.section, subject=self.subject)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if qs.exists():
            raise ValidationError({
                'subject': _(f"Subject '{self.subject.name}' is already mapped to Section '{self.section}'.")
            })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


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
