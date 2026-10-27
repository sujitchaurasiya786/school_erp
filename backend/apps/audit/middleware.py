import threading

_thread_locals = threading.local()


def get_current_request():
    """Returns the HTTP request currently being processed by the thread."""
    return getattr(_thread_locals, 'request', None)


def get_current_user():
    """Returns the authenticated user for the current request."""
    request = get_current_request()
    if request and hasattr(request, 'user') and request.user.is_authenticated:
        return request.user
    return None


class AuditMiddleware:
    """
    Middleware that captures the current HTTP request into thread-local storage
    for audit logging throughout models and services.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.request = request
        try:
            response = self.get_response(request)
        finally:
            _thread_locals.request = None
        return response
