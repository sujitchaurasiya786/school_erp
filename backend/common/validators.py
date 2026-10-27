import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_phone_number(value):
    """
    Validates standard 10-digit Indian phone numbers (optional +91 prefix).
    """
    if value:
        pattern = r'^(\+91[\-\s]?)?[6-9]\d{9}$'
        if not re.match(pattern, str(value).strip()):
            raise ValidationError(
                _("%(value)s is not a valid 10-digit mobile number."),
                params={'value': value},
            )


def validate_academic_year_format(value):
    """
    Validates academic year string format (e.g., '2026-2027').
    """
    pattern = r'^\d{4}-\d{4}$'
    if not re.match(pattern, str(value).strip()):
        raise ValidationError(
            _("Academic year must be formatted as YYYY-YYYY (e.g. 2026-2027)."),
            params={'value': value},
        )
    start_year, end_year = map(int, str(value).strip().split('-'))
    if end_year != start_year + 1:
        raise ValidationError(
            _("End year must be exactly one year after start year (e.g. %(start)s-%(expected)s)."),
            params={'start': start_year, 'expected': start_year + 1},
        )


def validate_file_size_limit(file_obj, max_mb=5):
    """
    Ensures uploaded document does not exceed size limit in megabytes.
    """
    limit = max_mb * 1024 * 1024
    if file_obj.size > limit:
        raise ValidationError(
            _("File size exceeds %(limit)s MB limit."),
            params={'limit': max_mb}
        )
