from apps.rbac.models import Permission


class RBACService:
    """
    Central service for evaluating user permissions dynamically.
    """

    @staticmethod
    def get_user_permissions(user):
        """
        Returns a set of permission codenames active for the user.
        """
        if not user or not user.is_authenticated:
            return set()

        if user.is_superuser:
            return set(Permission.objects.values_list('codename', flat=True))

        return set(
            Permission.objects.filter(
                roles__user_assignments__user=user,
                roles__is_active=True,
                roles__user_assignments__is_active=True,
                is_active=True
            ).values_list('codename', flat=True).distinct()
        )

    @classmethod
    def has_permission(cls, user, codename):
        """
        Check if user has a specific permission codename.
        """
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        user_perms = cls.get_user_permissions(user)
        return codename in user_perms

    @classmethod
    def has_module_action(cls, user, module, action):
        """
        Convenience method to check if user has permission for (module, action).
        Checks variations: 'module:action', 'module.action', 'module_action'
        """
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        return (
            cls.has_permission(user, f"{module}:{action}") or
            cls.has_permission(user, f"{module}.{action}") or
            cls.has_permission(user, f"{module}_{action}")
        )
