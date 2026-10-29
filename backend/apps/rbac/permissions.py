"""
DRF permission classes for School ERP dynamic RBAC.

HasDynamicPermission:
  - Rejects unauthenticated requests.
  - Rejects inactive users.
  - Enforces `must_change_password` flag: if set, only /api/v1/identity/users/me/
    (PATCH) is allowed so the user can update their password.
  - Does NOT grant automatic superuser bypass — all access flows through RBAC
    per SRS §3.7 (System Administrators are NOT auto-granted payroll/finance/
    confidential grievance permissions).
  - Reads `required_permission` from the view for explicit permission checks.
  - Falls back to `module_name` + HTTP-method → action mapping for ViewSets.
  - Returns False (deny) by default when neither attribute is present.
"""
from rest_framework.permissions import BasePermission

from apps.rbac.services import RBACService


# Maps standard HTTP methods to RBAC action codes
_METHOD_ACTION_MAP = {
    "GET": "view",
    "HEAD": "view",
    "OPTIONS": "view",
    "POST": "add",
    "PUT": "change",
    "PATCH": "change",
    "DELETE": "delete",
}


class HasDynamicPermission(BasePermission):
    """
    DRF permission class that enforces dynamic RBAC on every API request.

    Views MUST declare one of:
      - `required_permission = "some.codename"` for exact codename check.
      - `module_name = "module"` for HTTP-method-to-action mapping.

    Without either, access is DENIED (fail-closed).
    """

    message = "You do not have the required permission to perform this action."

    def has_permission(self, request, view):
        user = request.user

        # Gate 1: Must be authenticated
        if not user or not user.is_authenticated:
            return False

        # Gate 2: Account must be active
        if not user.is_active:
            self.message = "Your account has been deactivated."
            return False

        # Gate 3: must_change_password enforcement
        if getattr(user, "must_change_password", False):
            # Only allow the "me" endpoint for PATCH (password change)
            view_action = getattr(view, "action", None)
            if not (view_action == "me" and request.method == "PATCH"):
                self.message = (
                    "You must change your password before accessing this resource."
                )
                return False

        # Gate 4: Explicit permission codename declared on view
        required_permission = getattr(view, "required_permission", None)
        if required_permission:
            return RBACService.has_permission(user, required_permission)

        # Gate 5: Module-level action mapping
        module_name = getattr(view, "module_name", None)
        if module_name:
            # ViewSet custom actions may override the action code
            custom_action_permission = getattr(view, "action_permission_map", {})
            view_action = getattr(view, "action", None)
            if view_action and view_action in custom_action_permission:
                return RBACService.has_permission(
                    user, custom_action_permission[view_action]
                )

            action = _METHOD_ACTION_MAP.get(request.method, "view")
            return RBACService.has_module_action(user, module_name, action)

        # Fail-closed: deny if no permission rule is declared on the view
        self.message = "This endpoint requires explicit permission configuration."
        return False

    def has_object_permission(self, request, view, obj):
        """
        Object-level permission. Safe read-methods are allowed if has_permission
        already passed; write methods re-verify through RBAC.
        """
        user = request.user

        if not user or not user.is_authenticated or not user.is_active:
            return False

        # For safe methods, trust the view-level check already done
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True

        # Re-check write access at the object level using the same rules
        return self.has_permission(request, view)
