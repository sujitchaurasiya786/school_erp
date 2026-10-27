from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException
from rest_framework import status


class SchoolERPException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "A business rule validation error occurred."
    default_code = "business_rule_error"


class PermissionDeniedError(SchoolERPException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "You do not have permission to perform this action."
    default_code = "permission_denied"


class ResourceNotFoundError(SchoolERPException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "The requested resource was not found."
    default_code = "not_found"


class BusinessConflictError(SchoolERPException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflict detected with existing school records."
    default_code = "conflict"


def custom_exception_handler(exc, context):
    """
    Custom exception handler to ensure standard, consistent API error responses.
    """
    response = exception_handler(exc, context)

    if response is not None:
        errors = response.data
        message = "Validation or processing error"

        if isinstance(errors, dict) and 'detail' in errors:
            message = str(errors['detail'])
        elif isinstance(errors, list) and len(errors) > 0:
            message = str(errors[0])

        response.data = {
            'status': 'error',
            'status_code': response.status_code,
            'message': message,
            'errors': errors
        }

    return response
