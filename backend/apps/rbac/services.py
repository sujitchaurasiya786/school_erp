"""
RBAC Service — Central authorization engine for School ERP.

Design principles (per SRS §4):
  - Deny by default: returns False whenever authorization cannot be confirmed.
  - No trust in role names, request parameters, or UI state.
  - Superuser / Django `is_superuser` does NOT automatically bypass RBAC checks.
    Per SRS §3.7, System Administrators are NOT granted payroll, finance, or
    confidential grievance permissions automatically. Every access must flow
    through explicit role assignments.
  - Inactive users (is_active=False) are rejected immediately.
  - Only ACTIVE role assignments (UserRoleAssignment.is_active=True) count.
  - Only permissions attached to ACTIVE roles (Role.is_active=True) count.
  - Only ACTIVE permissions (Permission.is_active=True) count.
  - Scope-aware: class/section scope is respected when scope_id is provided.
"""
from django.db.models import Q


class RBACService:
    """
    Central service for evaluating user permissions dynamically.
    Use classmethods to avoid instantiation; all methods are pure and stateless.
    """

    @staticmethod
    def get_user_permissions(user, scope_type=None, scope_id=None):
        """
        Returns a set of active permission codenames for the user,
        filtered through active role assignments and active roles.

        Args:
            user:        The user instance to evaluate.
            scope_type:  Optional ScopeType value to restrict to a specific scope.
            scope_id:    Optional scope identifier (e.g. class_id or section_id).

        Returns:
            set[str]: Set of codenames the user currently holds. Empty if denied.
        """
        # Import here to avoid circular imports at module load time
        from apps.rbac.models import Permission, UserRoleAssignment

        if not user or not getattr(user, "is_authenticated", False):
            return set()

        if not getattr(user, "is_active", False):
            return set()

        # Build the base assignment filter
        assignment_filter = Q(
            roles__user_assignments__user=user,
            roles__user_assignments__is_active=True,
            roles__is_active=True,
            is_active=True,
        )

        # Scope narrowing: if a scope_type is requested, only include assignments
        # that are either GLOBAL or match the requested scope exactly.
        if scope_type and scope_id:
            assignment_filter &= Q(
                roles__user_assignments__scope_type__in=["GLOBAL", scope_type],
            ) & (
                Q(roles__user_assignments__scope_id__isnull=True)
                | Q(roles__user_assignments__scope_id="")
                | Q(roles__user_assignments__scope_id=str(scope_id))
            )

        return set(
            Permission.objects.filter(assignment_filter)
            .values_list("codename", flat=True)
            .distinct()
        )

    @classmethod
    def has_permission(cls, user, codename, scope_type=None, scope_id=None):
        """
        Check if user has a specific permission codename.

        Args:
            user:       The user instance.
            codename:   The exact permission codename string.
            scope_type: Optional scope restriction.
            scope_id:   Optional scope identifier.

        Returns:
            bool: True only if the user has an active assignment granting this permission.
        """
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if not getattr(user, "is_active", False):
            return False

        user_perms = cls.get_user_permissions(user, scope_type=scope_type, scope_id=scope_id)
        return codename in user_perms

    @classmethod
    def has_module_action(cls, user, module, action, scope_type=None, scope_id=None):
        """
        Convenience method: checks if user has permission for a (module, action) pair.
        Checks codename formats: 'module.action', 'module:action', 'module_action'.

        This covers the three common codename styles used across the registry.
        """
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if not getattr(user, "is_active", False):
            return False

        return (
            cls.has_permission(user, f"{module}.{action}", scope_type, scope_id)
            or cls.has_permission(user, f"{module}:{action}", scope_type, scope_id)
            or cls.has_permission(user, f"{module}_{action}", scope_type, scope_id)
        )

    @classmethod
    def get_user_roles(cls, user):
        """
        Returns a queryset of active Role objects assigned to the user.
        """
        from apps.rbac.models import Role

        if not user or not getattr(user, "is_authenticated", False):
            from apps.rbac.models import Role
            return Role.objects.none()

        return Role.objects.filter(
            user_assignments__user=user,
            user_assignments__is_active=True,
            is_active=True,
        ).distinct()

    @classmethod
    def has_any_permission(cls, user, codenames):
        """
        Returns True if user has ANY of the given codenames.
        Useful for OR-based permission checks.
        """
        if not user or not getattr(user, "is_authenticated", False):
            return False
        user_perms = cls.get_user_permissions(user)
        return bool(user_perms & set(codenames))

    @classmethod
    def has_all_permissions(cls, user, codenames):
        """
        Returns True only if user has ALL of the given codenames.
        Useful for AND-based compound permission checks.
        """
        if not user or not getattr(user, "is_authenticated", False):
            return False
        user_perms = cls.get_user_permissions(user)
        return set(codenames).issubset(user_perms)
