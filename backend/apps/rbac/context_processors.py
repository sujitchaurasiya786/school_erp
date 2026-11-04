"""
RBAC Template Context Processor — School ERP.

Injects dynamic user permissions into all Django templates to enable
permission-driven navigation without hardcoding role names.
"""
from apps.rbac.services import RBACService


def rbac_context(request):
    """
    Returns dynamic permission context for the current user.

    Context variables:
      - `user_permissions`: set of permission codename strings granted to user.
      - `has_rbac_access`: boolean whether user can access any RBAC or admin feature.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated or not request.user.is_active:
        return {
            'user_permissions': set(),
            'has_rbac_access': False,
        }

    permissions = RBACService.get_user_permissions(request.user)

    return {
        'user_permissions': permissions,
        'has_rbac_access': bool(
            request.user.is_superuser
            or permissions.intersection({
                'identity.users.view',
                'rbac.role.view',
                'rbac.role.manage',
                'school_config.profile.view',
                'audit.logs.view',
            })
        ),
    }
