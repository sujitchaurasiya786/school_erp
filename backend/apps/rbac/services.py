"""
Central authorization service for the School ERP dynamic RBAC system.
"""
from django.db.models import Q


class RBACService:
    @staticmethod
    def is_system_administrator(user):
        """True only for an active user with the protected system role assigned."""
        if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
            return False
        from apps.rbac.models import UserRoleAssignment
        return UserRoleAssignment.objects.filter(
            user=user,
            is_active=True,
            role__code="system-administrator",
            role__is_system_role=True,
            role__is_active=True,
        ).exists()

    @staticmethod
    def get_user_permissions(user, scope_type=None, scope_id=None):
        """
        Return active permissions from active assignments and active roles.
        When a scope is supplied, GLOBAL permissions and matching scoped
        permissions are included. A missing scope ID never widens access.
        """
        from apps.rbac.models import Permission, UserRoleAssignment

        if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
            return set()

        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
        )
        if scope_type:
            if scope_id is None or str(scope_id).strip() == "":
                assignments = assignments.filter(
                    scope_type="GLOBAL",
                ).filter(Q(scope_id="") | Q(scope_id__isnull=True))
            else:
                assignments = assignments.filter(
                    Q(scope_type="GLOBAL") & (Q(scope_id="") | Q(scope_id__isnull=True))
                    | Q(scope_type=scope_type, scope_id=str(scope_id))
                )

        return set(
            Permission.objects.filter(
                is_active=True,
                roles__is_active=True,
                roles__user_assignments__in=assignments,
            ).values_list("codename", flat=True).distinct()
        )

    @classmethod
    def has_permission(cls, user, codename, scope_type=None, scope_id=None):
        if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
            return False
        return codename in cls.get_user_permissions(
            user, scope_type=scope_type, scope_id=scope_id
        )

    @classmethod
    def has_module_action(cls, user, module, action, scope_type=None, scope_id=None):
        if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
            return False
        permissions = cls.get_user_permissions(
            user, scope_type=scope_type, scope_id=scope_id
        )
        if not permissions:
            return False

        if any(code in permissions for code in (
            f"{module}.{action}", f"{module}:{action}", f"{module}_{action}"
        )):
            return True

        suffix = f".{action}"
        if any(code.startswith(f"{module}.") and code.endswith(suffix) for code in permissions):
            return True

        from apps.rbac.models import Permission
        return Permission.objects.filter(
            codename__in=permissions,
            module=module,
            action=action,
            is_active=True,
        ).exists()

    @classmethod
    def get_user_roles(cls, user):
        from apps.rbac.models import Role
        if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
            return Role.objects.none()
        return Role.objects.filter(
            user_assignments__user=user,
            user_assignments__is_active=True,
            is_active=True,
        ).distinct()

    @classmethod
    def has_any_permission(cls, user, codenames):
        return bool(cls.get_user_permissions(user) & set(codenames))

    @classmethod
    def has_all_permissions(cls, user, codenames):
        return set(codenames).issubset(cls.get_user_permissions(user))

    @classmethod
    def active_role_manager_exists(cls, exclude_assignment_id=None, exclude_role_id=None):
        """Protect the installation from losing its final active RBAC manager."""
        from apps.rbac.models import UserRoleAssignment
        assignments = UserRoleAssignment.objects.filter(
            is_active=True,
            role__is_active=True,
            role__permissions__codename="users.membership.manage",
            user__is_active=True,
        )
        if exclude_assignment_id is not None:
            assignments = assignments.exclude(pk=exclude_assignment_id)
        if exclude_role_id is not None:
            assignments = assignments.exclude(role_id=exclude_role_id)
        return assignments.exists()
