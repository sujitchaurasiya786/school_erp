from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class ExamType(models.TextChoices):
    UNIT_TEST = 'UNIT_TEST', _('Unit Test')
    HALF_YEARLY = 'HALF_YEARLY', _('Half-Yearly Examination')
    ANNUAL = 'ANNUAL', _('Annual Examination')
    PRACTICAL = 'PRACTICAL', _('Practical Assessment')
    INTERNAL = 'INTERNAL', _('Internal Assessment / Portfolio')


class ResultStatus(models.TextChoices):
    DRAFT = 'DRAFT', _('Draft / Marks Entry')
    APPROVED = 'APPROVED', _('Approved by Principal')
    PUBLISHED = 'PUBLISHED', _('Published to Parents & Students')


class ExamTerm(TimeStampedModel):
    academic_year = models.ForeignKey('school_config.AcademicYear', on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    exam_type = models.CharField(max_length=20, choices=ExamType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=ResultStatus.choices, default=ResultStatus.DRAFT)

    class Meta:
        db_table = 'exam_terms'

    def __str__(self):
        return f"{self.title} ({self.academic_year.name})"


class Assessment(TimeStampedModel):
    """
    Assessment per class, subject and term.
    """
    term = models.ForeignKey(ExamTerm, on_delete=models.CASCADE, related_name='assessments')
    class_name = models.CharField(max_length=50)
    subject = models.ForeignKey('academics.Subject', on_delete=models.PROTECT)
    maximum_marks = models.DecimalField(max_digits=5, decimal_places=2, default=100.00)
    weightage_percent = models.DecimalField(max_digits=5, decimal_places=2, default=100.00)

    class Meta:
        db_table = 'exam_assessments'
        unique_together = ('term', 'class_name', 'subject')

    def __str__(self):
        return f"{self.term.title} - {self.class_name} - {self.subject.name}"


class ExamMark(TimeStampedModel):
    """
    Student mark record.
    """
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='marks')
    student = models.ForeignKey('students.Student', on_delete=models.CASCADE, related_name='exam_marks')
    marks_obtained = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    is_absent = models.BooleanField(default=False)
    is_exempt = models.BooleanField(default=False)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        db_table = 'exam_marks'
        unique_together = ('assessment', 'student')

    def __str__(self):
        val = 'Absent' if self.is_absent else f"{self.marks_obtained}/{self.assessment.maximum_marks}"
        return f"{self.student.admission_number} - {self.assessment.subject.code}: {val}"
