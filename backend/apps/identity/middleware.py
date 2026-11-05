"""
Middleware to enforce mandatory password change on first login for School ERP.
"""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import resolve, Resolver404


class MandatoryPasswordChangeMiddleware:
    """
    Guards all protected Web and API resources.
    If an authenticated user has must_change_password=True:
      - Allows only exempt endpoints (password change, logout, static/media files, auth).
      - API requests to any protected endpoint are rejected with 403 Forbidden.
      - Web requests to any protected endpoint are redirected to the password change page.
    """

    EXEMPT_URL_NAMES = {
        "password-change",
        "logout",
        "identity-logout",
        "identity-change-password",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if user and user.is_authenticated and getattr(user, "must_change_password", False):
            if not self.is_exempt(request):
                # REST API requests: respond with 403 Forbidden JSON
                if request.path_info.startswith("/api/"):
                    return JsonResponse(
                        {
                            "status": "error",
                            "message": "You must change your password before accessing this resource.",
                            "code": "must_change_password",
                        },
                        status=403,
                    )
                # Web browser requests: redirect to password change page
                return redirect("password-change")

        return self.get_response(request)

    def is_exempt(self, request):
        path = request.path_info

        # 1. Static and media files
        if settings.STATIC_URL and path.startswith(settings.STATIC_URL):
            return True
        if settings.MEDIA_URL and path.startswith(settings.MEDIA_URL):
            return True

        # 2. Django admin logout
        if path.startswith("/admin/logout/"):
            return True

        # 3. Dedicated API password change / me PATCH / logout
        if path == "/api/v1/identity/change-password/":
            return True
        if path == "/api/v1/identity/logout/":
            return True
        if path == "/api/v1/identity/users/me/" and request.method == "PATCH":
            return True

        # 4. URL name matching
        try:
            match = resolve(path)
            if match.url_name in self.EXEMPT_URL_NAMES:
                return True
        except Resolver404:
            pass

        return False
