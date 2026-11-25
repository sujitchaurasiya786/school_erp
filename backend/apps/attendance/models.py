from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class AttendanceStatus(models.TextChoices):
    PRESENT = 'PRESENT', _('Present')
    ABSENT = 'ABSENT', _('Absent')
    HALF_DAY = 'HALF_DAY', _('Half Day')
    LEAVE = 'LEAVE', _('Approved Leave')
    LATE = 'LATE', _('Late')


class AttendanceSessionStatus(models.TextChoices):
    DRAFT = 'DRAFT', _('Draft')
    SUBMITTED = 'SUBMITTED', _('Submitted')


class HolidayType(models.TextChoices):
    GAZETTED = 'GAZETTED', _('Gazetted Holiday')
    RESTRICTED = 'RESTRICTED', _('Restricted / Optional')
    VACATION = 'VACATION', _('Vacation / Break')
    LOCAL = 'LOCAL', _('Local / School Holiday')


class SaturdayRule(models.TextChoices):
    ALL_WORKING = 'ALL_WORKING', _('All Saturdays Working')
    ALL_OFF = 'ALL_OFF', _('All Saturdays Off')
    SECOND_FOURTH_OFF = 'SECOND_FOURTH_OFF', _('2nd and 4th Saturday Off')


class StaffApprovalStatus(models.TextChoices):
    AUTO_APPROVED = 'AUTO_APPROVED', _('Auto Approved')
    PENDING_APPROVAL = 'PENDING_APPROVAL', _('Pending Approval')
    APPROVED = 'APPROVED', _('Approved')
    REJECTED = 'REJECTED', _('Rejected')


class CheckInMethod(models.TextChoices):
    MANUAL = 'MANUAL', _('Manual (Admin/HR)')
    SELF_GEO = 'SELF_GEO', _('Self Check-in (Geo/Camera)')


class OverrideType(models.TextChoices):
    SPECIAL_WORKING = 'SPECIAL_WORKING', _('Special / Compensatory Working Day')
    EMERGENCY_CLOSURE = 'EMERGENCY_CLOSURE', _('Emergency / Unplanned Closure')
    EVENT = 'EVENT', _('School Event / Celebration')


class CalendarEventType(models.TextChoices):
    HOLIDAY = 'HOLIDAY', _('Holiday')
    VACATION = 'VACATION', _('Vacation')
    EXAM = 'EXAM', _('Exam')
    PTM = 'PTM', _('Parent-teacher meeting')
    EVENT = 'EVENT', _('Event')
    OTHER = 'OTHER', _('Other')


WEEKDAY_CHOICES = [
    (1, _('Monday')),
    (2, _('Tuesday')),
    (3, _('Wednesday')),
    (4, _('Thursday')),
    (5, _('Friday')),
    (6, _('Saturday')),
    (7, _('Sunday')),
]


class Holiday(TimeStampedModel):
    """
    School calendar holidays and non-working days.
    """
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.CASCADE,
        related_name='holidays'
    )
    title = models.CharField(max_length=100)
    date = models.DateField(db_index=True)
    is_national_holiday = models.BooleanField(default=False)
    holiday_type = models.CharField(
        max_length=30,
        choices=HolidayType.choices,
        default=HolidayType.GAZETTED
    )
    description = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'attendance_holidays'
        unique_together = ('academic_year', 'date')
        ordering = ['date']

    def __str__(self):
        return f"{self.title} ({self.date})"

    def clean(self):
        super().clean()
        if self.academic_year_id and self.date:
            ay = self.academic_year
            if ay.start_date and ay.end_date:
                if not (ay.start_date <= self.date <= ay.end_date):
                    raise ValidationError({
                        'date': _(f"Holiday date {self.date} falls outside academic year '{ay.name}' ({ay.start_date} to {ay.end_date}).")
                    })


class CalendarEvent(TimeStampedModel):
    """
    Events, holidays, vacations, examinations, and PTMs displayed on the school calendar.
    """
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.CASCADE,
        related_name='calendar_events'
    )
    title = models.CharField(max_length=150)
    event_type = models.CharField(
        max_length=30,
        choices=CalendarEventType.choices,
        default=CalendarEventType.HOLIDAY,
        db_index=True
    )
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    note = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_calendar_events'
    )

    class Meta:
        db_table = 'attendance_calendar_events'
        ordering = ['start_date', 'title']

    def __str__(self):
        if self.start_date == self.end_date:
            return f"{self.title} ({self.start_date})"
        return f"{self.title} ({self.start_date} - {self.end_date})"

    @property
    def is_multi_day(self):
        return self.start_date != self.end_date

    @property
    def display_date_range(self):
        if self.start_date == self.end_date:
            return self.start_date.strftime("%d %b %Y").lstrip("0")
        return f"{self.start_date.strftime('%d %b %Y').lstrip('0')} - {self.end_date.strftime('%d %b %Y').lstrip('0')}"

    @property
    def color_hex(self):
        colors = {
            CalendarEventType.HOLIDAY: '#dc2626',
            CalendarEventType.VACATION: '#ea580c',
            CalendarEventType.EXAM: '#7c3aed',
            CalendarEventType.PTM: '#0891b2',
            CalendarEventType.EVENT: '#2563eb',
            CalendarEventType.OTHER: '#475569',
        }
        return colors.get(self.event_type, '#475569')

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({'end_date': _("End date cannot be earlier than start date.")})


class WorkingDayConfig(TimeStampedModel):
    """
    Academic-year-specific weekly working-day configuration.
    Defines regular weekly operational days and Saturday rules.
    """
    academic_year = models.OneToOneField(
        'school_config.AcademicYear',
        on_delete=models.CASCADE,
        related_name='working_day_config'
    )
    monday = models.BooleanField(default=True)
    tuesday = models.BooleanField(default=True)
    wednesday = models.BooleanField(default=True)
    thursday = models.BooleanField(default=True)
    friday = models.BooleanField(default=True)
    sunday = models.BooleanField(default=False)
    saturday_rule = models.CharField(
        max_length=30,
        choices=SaturdayRule.choices,
        default=SaturdayRule.ALL_WORKING,
        help_text=_("Policy governing whether Saturdays are working, off, or alternate (2nd/4th off).")
    )
    notes = models.CharField(max_length=255, blank=True, default='')
    shortage_threshold = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=75.00,
        help_text=_("Minimum attendance percentage threshold before shortage alert (default 75%).")
    )
    allow_late_status = models.BooleanField(
        default=False,
        help_text=_("Allow marking students as LATE in daily attendance.")
    )

    class Meta:
        db_table = 'attendance_working_day_configs'
        verbose_name = _('Working Day Configuration')
        verbose_name_plural = _('Working Day Configurations')
        ordering = ['-created_at']

    def __str__(self):
        return f"Working Days for {self.academic_year.name} (Sat: {self.get_saturday_rule_display()})"


class SpecialDayOverride(TimeStampedModel):
    """
    Special working-day overrides and emergency school closures.
    Allows declaring a weekend/holiday date as working (with mapped timetable weekday)
    or declaring an emergency unplanned closure.
    """
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.CASCADE,
        related_name='special_day_overrides'
    )
    date = models.DateField(db_index=True)
    is_working = models.BooleanField(
        default=True,
        help_text=_("True if school operates on this day; False if school is closed.")
    )
    override_type = models.CharField(
        max_length=30,
        choices=OverrideType.choices,
        default=OverrideType.SPECIAL_WORKING
    )
    timetable_weekday = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=WEEKDAY_CHOICES,
        help_text=_("Weekday timetable schedule to execute on this special working day.")
    )
    reason = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_special_overrides'
    )

    class Meta:
        db_table = 'attendance_special_overrides'
        unique_together = ('academic_year', 'date')
        ordering = ['date']

    def __str__(self):
        status = "Working" if self.is_working else "Closed"
        return f"{self.date} ({status}) - {self.reason}"

    def clean(self):
        super().clean()
        if not str(self.reason or '').strip():
            raise ValidationError({'reason': _("Reason is required for a special day override.")})

        if self.academic_year_id and self.date:
            ay = self.academic_year
            if ay.start_date and ay.end_date:
                if not (ay.start_date <= self.date <= ay.end_date):
                    raise ValidationError({
                        'date': _(f"Override date {self.date} falls outside academic year '{ay.name}' ({ay.start_date} to {ay.end_date}).")
                    })

        if self.is_working:
            if not self.timetable_weekday:
                raise ValidationError({
                    'timetable_weekday': _("A regular weekday timetable schedule must be selected for special working days.")
                })
            if self.timetable_weekday not in dict(WEEKDAY_CHOICES):
                raise ValidationError({
                    'timetable_weekday': _("Invalid timetable weekday selected.")
                })


class AttendanceSession(TimeStampedModel):
    """
    Represents a single daily attendance marking session for an academic year,
    class, section, and date. Controls the Draft -> Submitted (locked) lifecycle.
    """
    academic_year = models.ForeignKey(
        'school_config.AcademicYear',
        on_delete=models.PROTECT,
        related_name='attendance_sessions'
    )
    class_name = models.CharField(max_length=50, db_index=True)
    section_name = models.CharField(max_length=10, db_index=True)
    date = models.DateField(db_index=True)
    status = models.CharField(
        max_length=20,
        choices=AttendanceSessionStatus.choices,
        default=AttendanceSessionStatus.DRAFT,
        db_index=True
    )
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_attendance_sessions'
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submitted_attendance_sessions'
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'attendance_sessions'
        unique_together = ('academic_year', 'class_name', 'section_name', 'date')
        ordering = ['-date', 'class_name', 'section_name']

    def __str__(self):
        return f"{self.class_name}-{self.section_name} ({self.date}) [{self.status}]"

    @property
    def is_locked(self):
        return self.status == AttendanceSessionStatus.SUBMITTED


class StudentDailyAttendance(TimeStampedModel):
    """
    Daily attendance per student per date. One record per student per day.
    """
    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='records'
    )
    enrollment = models.ForeignKey(
        'students.ClassEnrollment',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='daily_attendances'
    )
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


class StaffDailyAttendance(TimeStampedModel):
    """
    Daily attendance record for school staff (teachers, administrative, and support staff).
    One record per staff member per calendar date.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='staff_daily_attendances',
        db_index=True
    )
    staff_profile = models.ForeignKey(
        'hrms.StaffProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='daily_attendances'
    )
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
        blank=True,
        related_name='marked_staff_attendances'
    )
    # Self-attendance, Geofencing, Timings & Approval
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    selfie_image = models.TextField(blank=True, default='', help_text=_("Base64 Data URL or path of verification selfie"))
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    distance_meters = models.FloatField(null=True, blank=True)
    is_within_radius = models.BooleanField(default=True)
    is_late_check_in = models.BooleanField(default=False)
    is_early_check_out = models.BooleanField(default=False)
    exception_reason = models.TextField(blank=True, default='', help_text=_("Reason when outside radius or late/early"))
    approval_status = models.CharField(
        max_length=20,
        choices=StaffApprovalStatus.choices,
        default=StaffApprovalStatus.AUTO_APPROVED
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_staff_attendances'
    )
    review_notes = models.TextField(blank=True, default='')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    check_in_method = models.CharField(
        max_length=20,
        choices=CheckInMethod.choices,
        default=CheckInMethod.MANUAL
    )

    class Meta:
        db_table = 'attendance_staff_daily_records'
        unique_together = ('user', 'date')
        ordering = ['date', 'user__first_name']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.date}: {self.status}"
