from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class ApplicationStatus(models.TextChoices):
    DRAFT = 'DRAFT', _('Draft')
    SUBMITTED = 'SUBMITTED', _('Submitted')
    UNDER_REVIEW = 'UNDER_REVIEW', _('Under Review')
    DOCUMENTS_VERIFIED = 'DOCUMENTS_VERIFIED', _('Documents Verified')
    TEST_SCHEDULED = 'TEST_SCHEDULED', _('Test/Interview Scheduled')
    APPROVED = 'APPROVED', _('Approved')
    REJECTED = 'REJECTED', _('Rejected')
    ADMITTED = 'ADMITTED', _('Converted to Student')


class DocumentStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending')
    VERIFIED = 'VERIFIED', _('Verified')
    REJECTED = 'REJECTED', _('Rejected')


class SeatQuota(TimeStampedModel):
    """
    Configured seats per class and academic year.
    """
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.CASCADE,
        related_name='seat_quotas'
    )
    class_name = models.CharField(max_length=50, help_text=_("e.g. Nursery, KG, Class 1 ... Class 12"))
    total_seats = models.PositiveIntegerField(default=40)
    waitlist_limit = models.PositiveIntegerField(default=10)

    class Meta:
        db_table = 'admission_seat_quotas'
        unique_together = ('academic_year', 'class_name')

    def __str__(self):
        return f"{self.class_name} ({self.academic_year.name}): {self.total_seats} seats"


class AdmissionApplication(TimeStampedModel):
    """
    Admission application record.
    """
    application_number = models.CharField(max_length=30, unique=True, db_index=True)
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.PROTECT,
        related_name='applications'
    )
    applying_class = models.CharField(max_length=50)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')])
    father_name = models.CharField(max_length=100)
    mother_name = models.CharField(max_length=100)
    guardian_phone = models.CharField(max_length=20, db_index=True)
    guardian_email = models.EmailField(blank=True, default='')
    address = models.TextField()
    previous_school = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(
        max_length=30,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SUBMITTED,
        db_index=True
    )
    test_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    interview_remarks = models.TextField(blank=True, default='')
    rejection_reason = models.TextField(blank=True, default='')
    converted_student_id = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'admission_applications'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.application_number} - {self.first_name} {self.last_name} ({self.applying_class})"


class ApplicationDocument(TimeStampedModel):
    """
    Documents uploaded for admission verification (birth cert, transfer cert, photos, etc.)
    """
    application = models.ForeignKey(
        AdmissionApplication,
        on_delete=models.CASCADE,
        related_name='documents'
    )
    doc_type = models.CharField(max_length=50, help_text=_("e.g. Birth Certificate, Transfer Certificate, Address Proof"))
    document_file = models.FileField(upload_to='admission_documents/%Y/%m/')
    verification_status = models.CharField(
        max_length=20,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING
    )
    verification_notes = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'admission_documents'

    def __str__(self):
        return f"{self.doc_type} - {self.application.application_number}"
