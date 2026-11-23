import re
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class ApplicationStatus(models.TextChoices):
    DRAFT = 'DRAFT', _('Draft')
    SUBMITTED = 'SUBMITTED', _('Submitted')
    UNDER_REVIEW = 'UNDER_REVIEW', _('Under Review')
    DOCUMENTS_VERIFIED = 'DOCUMENTS_VERIFIED', _('Documents Verified')
    TEST_SCHEDULED = 'TEST_SCHEDULED', _('Test/Interview Scheduled')
    WAITLIST = 'WAITLIST', _('Waitlisted')
    APPROVED = 'APPROVED', _('Approved')
    REJECTED = 'REJECTED', _('Rejected')
    ADMITTED = 'ADMITTED', _('Converted to Student')


class StudentCategory(models.TextChoices):
    GENERAL = 'GENERAL', _('General')
    OBC = 'OBC', _('Other Backward Class (OBC)')
    SC = 'SC', _('Scheduled Caste (SC)')
    ST = 'ST', _('Scheduled Tribe (ST)')
    EWS = 'EWS', _('Economically Weaker Section (EWS)')


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

    # Personal & Demographics (Enhanced)
    aadhaar_number = models.CharField(max_length=20, blank=True, default='')
    blood_group = models.CharField(max_length=10, blank=True, default='')
    religion = models.CharField(max_length=50, blank=True, default='')
    mother_tongue = models.CharField(max_length=50, blank=True, default='')
    student_photo = models.ImageField(upload_to='admission_photos/%Y/', blank=True, null=True)
    student_email = models.EmailField(blank=True, default='', help_text=_("Student personal/contact email"))

    # Parent / Guardian Detailed
    guardian_relation = models.CharField(max_length=50, default='Father')
    alternate_phone = models.CharField(max_length=20, blank=True, default='')
    father_occupation = models.CharField(max_length=100, blank=True, default='')
    mother_occupation = models.CharField(max_length=100, blank=True, default='')

    # Address Detailed (Current)
    city = models.CharField(max_length=100, blank=True, default='')
    district = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    pincode = models.CharField(max_length=10, blank=True, default='')

    # Permanent Address
    same_as_current_address = models.BooleanField(default=False)
    permanent_address = models.TextField(blank=True, default='')
    permanent_city = models.CharField(max_length=100, blank=True, default='')
    permanent_district = models.CharField(max_length=100, blank=True, default='')
    permanent_state = models.CharField(max_length=100, blank=True, default='')
    permanent_pincode = models.CharField(max_length=10, blank=True, default='')

    # Academic Background
    previous_class = models.CharField(max_length=50, blank=True, default='')
    previous_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # Category, Facilities & Consent
    category = models.CharField(max_length=20, choices=StudentCategory.choices, default=StudentCategory.GENERAL)
    declaration_accepted = models.BooleanField(default=True)
    needs_transport = models.BooleanField(default=False)
    needs_hostel = models.BooleanField(default=False)
    has_sibling_in_school = models.BooleanField(default=False)
    sibling_details = models.CharField(max_length=200, blank=True, default='')
    medical_conditions = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'admission_applications'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.application_number} - {self.first_name} {self.last_name} ({self.applying_class})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def clean(self):
        super().clean()
        errors = {}
        if not str(self.first_name or '').strip():
            errors['first_name'] = _("First name is required.")
        if not str(self.last_name or '').strip():
            errors['last_name'] = _("Last name is required.")
        if not str(self.father_name or '').strip() and not str(self.mother_name or '').strip():
            errors['father_name'] = _("At least one parent name (father or mother) is required.")

        # Phone validation: must be 10 digits
        raw_phone = str(self.guardian_phone or '').strip()
        digits = re.sub(r'\D', '', raw_phone)
        if not raw_phone:
            errors['guardian_phone'] = _("Primary mobile number is required.")
        elif len(digits) != 10:
            errors['guardian_phone'] = _("Primary mobile number must be exactly 10 digits.")
        else:
            self.guardian_phone = digits

        # If same_as_current_address, sync permanent address fields
        if self.same_as_current_address:
            self.permanent_address = self.address
            self.permanent_city = self.city
            self.permanent_district = self.district
            self.permanent_state = self.state
            self.permanent_pincode = self.pincode

        # Pincode validation: 6 digits if provided
        if self.pincode:
            p_digits = re.sub(r'\D', '', str(self.pincode))
            if len(p_digits) != 6:
                errors['pincode'] = _("Postal PIN code must be exactly 6 digits.")

        # Permanent Pincode validation: 6 digits if provided
        if self.permanent_pincode:
            pp_digits = re.sub(r'\D', '', str(self.permanent_pincode))
            if len(pp_digits) != 6:
                errors['permanent_pincode'] = _("Permanent postal PIN code must be exactly 6 digits.")

        # Date of birth validation & Age vs Class check
        if not self.date_of_birth:
            errors['date_of_birth'] = _("Date of birth is required.")
        elif self.date_of_birth >= timezone.now().date():
            errors['date_of_birth'] = _("Date of birth must be in the past.")
        else:
            ref_date = timezone.now().date()
            if self.academic_year_id and hasattr(self.academic_year, 'start_date') and self.academic_year.start_date:
                ref_date = self.academic_year.start_date
            age_years = (ref_date - self.date_of_birth).days / 365.25

            cn = str(self.applying_class or '').lower()
            if 'nursery' in cn and age_years < 3.0:
                errors['date_of_birth'] = _("Candidate must be at least 3 years old for Nursery.")
            elif ('lkg' in cn or 'kg 1' in cn or 'kg-1' in cn or 'prep' in cn) and age_years < 4.0:
                errors['date_of_birth'] = _("Candidate must be at least 4 years old for LKG/Prep.")
            elif ('ukg' in cn or 'kg 2' in cn or 'kg-2' in cn or 'kindergarten' in cn) and age_years < 5.0:
                errors['date_of_birth'] = _("Candidate must be at least 5 years old for UKG.")
            elif ('class 1' in cn or '1st' in cn or cn == '1') and age_years < 5.5:
                errors['date_of_birth'] = _("Candidate must be at least 5.5 years old for Class 1.")

        if not str(self.applying_class or '').strip():
            errors['applying_class'] = _("Applying class is required.")

        if not self.declaration_accepted:
            errors['declaration_accepted'] = _("Declaration must be accepted to proceed.")

        if errors:
            raise ValidationError(errors)


def validate_document_file(file):
    max_size = 2 * 1024 * 1024  # 2 MB limit
    if hasattr(file, 'size') and file.size and file.size > max_size:
        raise ValidationError(_("File size must not exceed 2 MB."))
    if hasattr(file, 'name') and '.' in file.name:
        ext = file.name.split('.')[-1].lower()
        if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
            raise ValidationError(_("Only PDF and JPG/JPEG/PNG files are allowed."))


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
    title = models.CharField(max_length=150, blank=True, default='')
    document_file = models.FileField(upload_to='admission_documents/%Y/%m/', validators=[validate_document_file])
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
