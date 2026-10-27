from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class StudentStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Active')
    TC_ISSUED = 'TC_ISSUED', _('Transfer Certificate Issued')
    PASSED_OUT = 'PASSED_OUT', _('Passed Out')
    WITHDRAWN = 'WITHDRAWN', _('Withdrawn')
    SUSPENDED = 'SUSPENDED', _('Suspended')


class Guardian(TimeStampedModel):
    """
    Guardian records (father, mother, primary guardian) linked to students.
    """
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    relationship = models.CharField(max_length=50, default='Father')
    phone = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True, default='')
    occupation = models.CharField(max_length=100, blank=True, default='')
    annual_income = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    user_account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='guardian_profile'
    )

    class Meta:
        db_table = 'student_guardians'

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.relationship} - {self.phone})"


class Student(TimeStampedModel):
    """
    Primary Student profile record.
    """
    admission_number = models.CharField(max_length=30, unique=True, db_index=True)
    user_account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_profile'
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')])
    blood_group = models.CharField(max_length=5, blank=True, default='')
    address = models.TextField()
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    pincode = models.CharField(max_length=20, blank=True, default='')
    photo = models.ImageField(upload_to='student_photos/%Y/', blank=True, null=True)
    primary_guardian = models.ForeignKey(
        Guardian,
        on_delete=models.PROTECT,
        related_name='students'
    )
    secondary_guardian = models.ForeignKey(
        Guardian,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='secondary_students'
    )
    siblings = models.ManyToManyField('self', blank=True)
    status = models.CharField(
        max_length=20,
        choices=StudentStatus.choices,
        default=StudentStatus.ACTIVE,
        db_index=True
    )
    admission_date = models.DateField()
    previous_school = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        db_table = 'student_profiles'
        ordering = ['admission_number']

    def __str__(self):
        return f"{self.admission_number} - {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class ClassEnrollment(TimeStampedModel):
    """
    One enrollment record per student per academic year.
    Tracks class, section, and roll number.
    """
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='enrollments'
    )
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.PROTECT,
        related_name='student_enrollments'
    )
    class_name = models.CharField(max_length=50, db_index=True)
    section_name = models.CharField(max_length=10, db_index=True)
    roll_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = 'student_class_enrollments'
        unique_together = ('academic_year', 'student')
        indexes = [
            models.Index(fields=['academic_year', 'class_name', 'section_name']),
        ]

    def __str__(self):
        return f"{self.student.admission_number} - {self.class_name} {self.section_name} (Roll: {self.roll_number}) [{self.academic_year.name}]"


class TransferCertificate(TimeStampedModel):
    """
    Transfer Certificate (TC) records.
    """
    tc_number = models.CharField(max_length=50, unique=True, db_index=True)
    student = models.OneToOneField(
        Student,
        on_delete=models.PROTECT,
        related_name='transfer_certificate'
    )
    issue_date = models.DateField()
    reason_for_leaving = models.TextField()
    dues_cleared = models.BooleanField(default=True)
    conduct_remarks = models.CharField(max_length=200, default='Good')

    class Meta:
        db_table = 'student_transfer_certificates'

    def __str__(self):
        return f"TC #{self.tc_number} - {self.student.admission_number}"
