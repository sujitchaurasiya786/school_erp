from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class AcademicYear(TimeStampedModel):
    """
    CBSE Academic year (e.g. 2026-2027) running from April to March.
    Only one academic year is marked as current at any given time.
    """
    name = models.CharField(max_length=50, unique=True, help_text=_("e.g. 2026-2027"))
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Marks this as the currently active academic year across modules.")
    )

    class Meta:
        db_table = 'config_academic_years'
        ordering = ['-start_date']

    def __str__(self):
        status = " (Current)" if self.is_current else ""
        return f"{self.name}{status}"

    def save(self, *args, **kwargs):
        if self.is_current:
            AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class SchoolProfile(TimeStampedModel):
    """
    Singleton-style CBSE School Profile.
    """
    school_name = models.CharField(max_length=200, default="CBSE Public School")
    affiliation_number = models.CharField(max_length=50, blank=True, default='')
    school_code = models.CharField(max_length=50, blank=True, default='')
    board = models.CharField(max_length=50, default='CBSE')
    address = models.TextField(blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    pincode = models.CharField(max_length=20, blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    website = models.URLField(blank=True, default='')
    logo = models.ImageField(upload_to='school_assets/', blank=True, null=True)
    current_academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='school_profiles'
    )

    class Meta:
        db_table = 'config_school_profile'
        verbose_name = _('School Profile')
        verbose_name_plural = _('School Profile')

    def __str__(self):
        return self.school_name
