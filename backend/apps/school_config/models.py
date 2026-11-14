from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel
from common.validators import validate_academic_year_format


class AcademicYearQuerySet(models.QuerySet):
    """
    Custom QuerySet for AcademicYear to prevent bulk bypass of critical invariants.
    """
    def update(self, **kwargs):
        if kwargs.get('is_current') is True:
            raise ValidationError(
                "Direct bulk update of 'is_current=True' is prohibited to prevent multi-current corruption. "
                "Use AcademicYearService.set_current_year() to ensure atomic singleton consistency."
            )
        return super().update(**kwargs)


class AcademicYear(TimeStampedModel):
    """
    CBSE Academic year (e.g. 2026-2027) running from April to March.
    Only one academic year is marked as current at any given time.
    """
    name = models.CharField(
        max_length=50,
        unique=True,
        validators=[validate_academic_year_format],
        help_text=_("e.g. 2026-2027")
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Marks this as the currently active academic year across modules.")
    )

    objects = AcademicYearQuerySet.as_manager()

    class Meta:
        db_table = 'config_academic_years'
        ordering = ['-start_date']

    def __str__(self):
        status = " (Current)" if self.is_current else ""
        return f"{self.name}{status}"

    def clean(self):
        super().clean()
        if self.name:
            validate_academic_year_format(self.name)

        if self.start_date and self.end_date:
            if self.start_date >= self.end_date:
                raise ValidationError({
                    'end_date': _("End date must be strictly after start date.")
                })

            if self.name:
                try:
                    start_yr, end_yr = map(int, str(self.name).strip().split('-'))
                    if self.start_date.year != start_yr or self.end_date.year != end_yr:
                        raise ValidationError({
                            'name': _(
                                f"Academic year name '{self.name}' must align with start date year "
                                f"({self.start_date.year}) and end date year ({self.end_date.year})."
                            )
                        })
                except (ValueError, TypeError):
                    pass

            # Prevent overlapping academic year date ranges
            overlapping = AcademicYear.objects.filter(
                start_date__lte=self.end_date,
                end_date__gte=self.start_date,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)

            if overlapping.exists():
                overlap_names = ", ".join(overlapping.values_list('name', flat=True))
                raise ValidationError(
                    _(f"Academic year date range ({self.start_date} to {self.end_date}) overlaps with existing academic year(s): {overlap_names}.")
                )

        if self.is_current and not self.is_active:
            raise ValidationError({
                'is_current': _("An inactive academic year cannot be selected as current.")
            })

    def save(self, *args, **kwargs):
        self.clean()
        if self.is_current:
            if not self.is_active:
                raise ValidationError({
                    'is_current': _("An inactive academic year cannot be selected as current.")
                })
            try:
                SchoolProfile.acquire_lock()
            except Exception:
                pass
            AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)
            super().save(*args, **kwargs)
            try:
                profile = SchoolProfile.get_singleton()
                if profile and profile.current_academic_year_id != self.pk:
                    profile.current_academic_year = self
                    profile.save(update_fields=['current_academic_year', 'updated_at'])
            except Exception:
                pass
            return
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

    @classmethod
    def get_singleton(cls):
        """
        Retrieves or initializes the persistent singleton SchoolProfile record.
        """
        profile = cls.objects.first()
        if not profile:
            profile, _ = cls.objects.get_or_create(
                id=1,
                defaults={'school_name': 'CBSE Public School'}
            )
        return profile

    @classmethod
    def acquire_lock(cls):
        """
        Acquires an exclusive row-level lock (FOR UPDATE) on the singleton SchoolProfile.
        Used to strictly serialize state-wide configuration updates such as academic year activation,
        even when no current academic year exists.
        """
        profile = cls.get_singleton()
        return cls.objects.select_for_update().get(pk=profile.pk)
