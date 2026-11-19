"""
RBAC Service — Central authorization engine for School ERP.

Design principles (per updated SRS §6.4):
  - Dual-tier authorization model:
      1. Permanent System Administrator (is_superuser=True):
         Holds complete, unrestricted access across the entire ERP (all modules,
         APIs, user management, dynamic roles, payroll, finance, grievances, audit logs,
         and school settings) without requiring dynamic role assignments or individual
         permission assignments. Receives all active ERP system permissions automatically.
      2. Dynamic RBAC for all normal users:
         Strictly deny by default. Normal users are governed dynamically through
         roles and permissions configured and assigned by Admin/Principal from the dashboard.
  - Check user.is_authenticated before authorization; anonymous users are strictly denied.
  - Inactive users (is_active=False) are rejected immediately.
  - `is_staff=True` alone NEVER grants complete ERP access or bypasses permission checks.
  - Scope-aware: class/section scope is respected for normal users when scope_id is provided.
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
        Returns a set of active permission codenames for the user.
        For superusers (is_superuser=True), returns all active system permissions per SRS §6.4.
        For normal users, filtered through active role assignments and active roles.

        Args:
            user:        The user instance to evaluate.
            scope_type:  Optional ScopeType value to restrict to a specific scope.
            scope_id:    Optional scope identifier (e.g. class_id or section_id).

        Returns:
            set[str]: Set of codenames the user currently holds. Empty if denied.
        """
        from apps.rbac.models import Permission

        if not user or not getattr(user, "is_authenticated", False):
            return set()

        if not getattr(user, "is_active", False):
            return set()

        # System Administrator permanent complete access (SRS §6.4)
        if getattr(user, "is_superuser", False):
            from apps.rbac.registry import PERMISSION_REGISTRY
            db_perms = set(
                Permission.objects.filter(is_active=True).values_list("codename", flat=True)
            )
            registry_perms = {p["codename"] for p in PERMISSION_REGISTRY}
            return db_perms | registry_perms

        # Base assignment filter for normal users
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
            bool: True if authorized (all system permissions for superuser, or granted by active role for normal user).
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
        Checks:
          1. Direct codename matches: 'module.action', 'module:action', 'module_action'
          2. Three-part codenames: 'module.*.action' (e.g. 'students.profile.view')
          3. Model-level module & action fields
        """
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if not getattr(user, "is_active", False):
            return False

        user_perms = cls.get_user_permissions(user, scope_type=scope_type, scope_id=scope_id)
        if not user_perms:
            return False

        # 1. Exact module-action format matches
        if (
            f"{module}.{action}" in user_perms
            or f"{module}:{action}" in user_perms
            or f"{module}_{action}" in user_perms
        ):
            return True

        # 2. Granular 3-part codenames like 'students.profile.view'
        mod_prefix = f"{module}."
        act_suffix = f".{action}"
        if any(p.startswith(mod_prefix) and p.endswith(act_suffix) for p in user_perms):
            return True

        # 3. Model-level module & action fields
        from apps.rbac.models import Permission
        return Permission.objects.filter(
            codename__in=user_perms,
            module=module,
            action=action,
            is_active=True,
        ).exists()

    @classmethod
    def get_user_roles(cls, user):
        """
        Returns a queryset of active Role objects assigned to the user.
        """
        from apps.rbac.models import Role

        if not user or not getattr(user, "is_authenticated", False):
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
        if not getattr(user, "is_active", False):
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
        if not getattr(user, "is_active", False):
            return False

        user_perms = cls.get_user_permissions(user)
        return set(codenames).issubset(user_perms)
