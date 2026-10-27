from rest_framework.permissions import BasePermission
from apps.rbac.services import RBACService


class HasDynamicPermission(BasePermission):
    """
    DRF permission class that checks dynamic permissions based on view's required_permission
    or maps standard HTTP methods to actions.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        # If view explicitly defines required_permission
        required_permission = getattr(view, 'required_permission', None)
        if required_permission:
            return RBACService.has_permission(request.user, required_permission)

        # Fallback to module + action mapping if module_name is defined on view
        module_name = getattr(view, 'module_name', None)
        if module_name:
            method_action_map = {
                'GET': 'view',
                'OPTIONS': 'view',
                'HEAD': 'view',
                'POST': 'add',
                'PUT': 'change',
                'PATCH': 'change',
                'DELETE': 'delete'
            }
            action = method_action_map.get(request.method, 'view')
            return RBACService.has_module_action(request.user, module_name, action)

        return True
