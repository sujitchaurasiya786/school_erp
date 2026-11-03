from django import forms
from django.db.models import Q
from django.contrib.auth import get_user_model

from apps.rbac.models import Permission, Role, UserRoleAssignment
from apps.rbac.services import RBACService


User = get_user_model()


class RoleForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Role
        fields = ("name", "code", "description", "permissions")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        available = Permission.objects.filter(is_active=True).order_by(
            "module", "name"
        )
        if actor and not RBACService.is_system_administrator(actor):
            held = RBACService.get_user_permissions(actor)
            # Existing permissions may be retained, but not newly granted.
            existing_ids = (
                self.instance.permissions.values_list("id", flat=True)
                if self.instance.pk else []
            )
            available = available.filter(
                Q(codename__in=held) | Q(pk__in=existing_ids)
            )
        self.fields["permissions"].queryset = available
        if self.instance.pk:
            self.fields["code"].disabled = True

    def clean_permissions(self):
        selected = self.cleaned_data.get("permissions")
        if not self.actor or RBACService.is_system_administrator(self.actor):
            return selected
        held = RBACService.get_user_permissions(self.actor)
        existing_ids = (
            set(self.instance.permissions.values_list("id", flat=True))
            if self.instance.pk else set()
        )
        newly_granted = [
            permission for permission in selected
            if permission.pk not in existing_ids
        ]
        if any(permission.codename not in held for permission in newly_granted):
            raise forms.ValidationError(
                "You cannot grant a permission that you do not currently hold."
            )
        return selected

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            selected = cleaned.get("permissions")
            if selected is not None and self.instance.permissions.filter(
                codename="users.membership.manage", is_active=True
            ).exists() and not selected.filter(codename="users.membership.manage").exists():
                from apps.rbac.models import UserRoleAssignment
                other_manager_exists = UserRoleAssignment.objects.filter(
                    is_active=True, role__is_active=True, user__is_active=True,
                    role__permissions__codename="users.membership.manage",
                ).exclude(role=self.instance).exists()
                if not other_manager_exists:
                    raise forms.ValidationError(
                        "This role is the last active role manager. Keep its membership-management permission."
                    )
        if self.instance.pk and self.instance.is_system_role:
            selected = cleaned.get("permissions")
            if selected is not None and self.instance.code == "system-administrator":
                required = {
                    "rbac.role.view",
                    "rbac.role.manage",
                    "rbac.role.change",
                    "rbac.role.delete",
                    "users.membership.manage",
                }
                selected_codes = set(selected.values_list("codename", flat=True))
                if not required.issubset(selected_codes):
                    raise forms.ValidationError(
                        "The protected System Administrator role must retain all "
                        "role-management permissions."
                    )
        return cleaned


class UserRoleAssignmentForm(forms.ModelForm):
    class Meta:
        model = UserRoleAssignment
        fields = ("user", "role", "scope_type", "scope_id")
        widgets = {
            "scope_id": forms.TextInput(
                attrs={"placeholder": "Required for class/section/department scope"}
            )
        }

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(is_active=True).order_by(
            "username"
        )
        self.fields["role"].queryset = Role.objects.filter(
            is_active=True
        ).order_by("name")

    def clean(self):
        cleaned = super().clean()
        user = cleaned.get("user")
        role = cleaned.get("role")
        scope_type = cleaned.get("scope_type")
        scope_id = (cleaned.get("scope_id") or "").strip()

        if scope_type in {"CLASS", "SECTION", "DEPARTMENT"} and not scope_id:
            self.add_error("scope_id", "A scope ID is required for this assignment.")

        if role and role.is_system_role and not (
            self.actor and RBACService.is_system_administrator(self.actor)
        ):
            self.add_error("role", "Only a System Administrator can assign a protected system role.")

        if role and self.actor and not RBACService.is_system_administrator(self.actor):
            held = RBACService.get_user_permissions(self.actor)
            missing = set(
                role.permissions.filter(is_active=True).values_list(
                    "codename", flat=True
                )
            ) - held
            if missing:
                self.add_error(
                    "role",
                    "You cannot assign a role containing permissions you do not hold.",
                )

        if user and role and user == self.actor and role.code == "system-administrator":
            self.add_error("role", "Use the bootstrap command to establish the first System Administrator.")

        return cleaned
