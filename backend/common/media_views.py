import os
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.http import FileResponse, Http404
from django.utils._os import safe_join
from apps.rbac.services import RBACService


@login_required(login_url='login')
def serve_private_media(request, file_path):
    """
    Permission-checked view for serving private uploaded files from MEDIA_ROOT
    (SRS §4: uploaded documents stored privately and served only through permission-checked views).
    """
    try:
        full_path = safe_join(str(settings.MEDIA_ROOT), file_path)
    except SuspiciousFileOperation:
        raise Http404("Invalid media path.")

    if not os.path.isfile(full_path):
        raise Http404("File not found.")

    user = request.user
    normalized = file_path.replace("\\", "/").lstrip("/")

    if not user.is_superuser:
        if normalized.startswith(("student_documents/", "student_photos/")):
            has_perm = (
                RBACService.has_permission(user, "students.profile.view")
                or (normalized.startswith("student_photos/") and RBACService.has_permission(user, "attendance.record.view"))
            )
            if not has_perm:
                # Allow students/parents to view only their own linked photo/document
                is_owner = False
                if hasattr(user, "student_profile") and user.student_profile:
                    st = user.student_profile
                    if (st.photo and st.photo.name == normalized) or st.documents.filter(document_file=normalized).exists():
                        is_owner = True
                if not is_owner and hasattr(user, "guardian_profile") and user.guardian_profile:
                    for ward in user.guardian_profile.students.all():
                        if (ward.photo and ward.photo.name == normalized) or ward.documents.filter(document_file=normalized).exists():
                            is_owner = True
                            break
                if not is_owner:
                    raise PermissionDenied("You do not have permission to access this student file.")

        elif normalized.startswith(("admission_documents/", "admission_photos/")):
            if not RBACService.has_permission(user, "admissions.application.view"):
                raise PermissionDenied("You do not have permission to access admission files.")

        elif normalized.startswith(("avatars/", "school_assets/")):
            pass  # Authenticated users may view UI avatars and school logo

        else:
            raise PermissionDenied("Access to this private directory is restricted.")

    return FileResponse(open(full_path, "rb"))
