"""
Server-rendered RBAC management screens.
All pages enforce backend permissions; navigation visibility is not security.
"""
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import AuditService
from apps.rbac.forms import RoleForm, UserRoleAssignmentForm
from apps.rbac.models import Permission, Role, UserRoleAssignment
from apps.rbac.services import RBACService


def _require(user, codename):
    if not RBACService.has_permission(user, codename):
        raise PermissionDenied("You do not have permission to access this RBAC screen.")


@login_required(login_url="login")
def role_list(request):
    _require(request.user, "rbac.role.view")
    roles = Role.objects.prefetch_related("permissions").all()
    return render(request, "rbac/role_list.html", {"roles": roles})


@login_required(login_url="login")
def role_form(request, role_id=None):
    role = get_object_or_404(Role, pk=role_id) if role_id else None
    _require(request.user, "rbac.role.change" if role else "rbac.role.manage")
    if role and role.is_system_role and not RBACService.is_system_administrator(request.user):
        raise PermissionDenied("Only a System Administrator can edit a protected system role.")

    if request.method == "POST":
        form = RoleForm(request.POST, instance=role, actor=request.user)
        if form.is_valid():
            with transaction.atomic():
                saved = form.save()
                AuditService.log(
                    action=AuditAction.UPDATE if role else AuditAction.CREATE,
                    module="rbac",
                    entity_type="Role",
                    entity_id=saved.pk,
                    actor=request.user,
                    changes={
                        "name": saved.name,
                        "code": saved.code,
                        "permission_codenames": list(
                            saved.permissions.values_list("codename", flat=True)
                        ),
                    },
                )
            messages.success(request, "Role saved successfully.")
            return redirect("rbac-role-list")
    else:
        form = RoleForm(instance=role, actor=request.user)
    return render(request, "rbac/role_form.html", {"form": form, "role": role})


@login_required(login_url="login")
@require_POST
def role_deactivate(request, role_id):
    _require(request.user, "rbac.role.delete")
    role = get_object_or_404(Role, pk=role_id)
    if role.is_system_role:
        raise PermissionDenied("Protected system roles cannot be deactivated.")
    role.is_active = False
    role.save(update_fields=["is_active", "updated_at"])
    AuditService.log(
        action=AuditAction.UPDATE, module="rbac", entity_type="Role",
        entity_id=role.pk, actor=request.user,
        changes={"is_active": {"from": True, "to": False}},
    )
    messages.success(request, "Role deactivated.")
    return redirect("rbac-role-list")


@login_required(login_url="login")
def permission_matrix(request):
    _require(request.user, "rbac.role.view")
    grouped = defaultdict(list)
    for permission in Permission.objects.filter(is_active=True).order_by("module", "action", "name"):
        grouped[permission.get_module_display()].append(permission)
    return render(request, "rbac/permission_matrix.html", {"groups": dict(grouped)})


@login_required(login_url="login")
def assignment_list(request):
    _require(request.user, "users.membership.manage")
    assignments = UserRoleAssignment.objects.select_related("user", "role").order_by(
        "user__username", "role__name"
    )
    return render(request, "rbac/assignment_list.html", {"assignments": assignments})


@login_required(login_url="login")
def assignment_create(request):
    _require(request.user, "users.membership.manage")
    if request.method == "POST":
        form = UserRoleAssignmentForm(request.POST, actor=request.user)
        if form.is_valid():
            assignment = form.save()
            AuditService.log(
                action=AuditAction.CREATE, module="rbac",
                entity_type="UserRoleAssignment", entity_id=assignment.pk,
                actor=request.user,
                changes={
                    "user": assignment.user.username,
                    "role": assignment.role.code,
                    "scope_type": assignment.scope_type,
                    "scope_id": assignment.scope_id,
                },
            )
            messages.success(request, "Role assigned successfully.")
            return redirect("rbac-assignment-list")
    else:
        form = UserRoleAssignmentForm(actor=request.user)
    return render(request, "rbac/assignment_form.html", {"form": form})


@login_required(login_url="login")
@require_POST
def assignment_revoke(request, assignment_id):
    _require(request.user, "users.membership.manage")
    assignment = get_object_or_404(
        UserRoleAssignment.objects.select_related("user", "role"), pk=assignment_id
    )
    if assignment.role.code == "system-administrator":
        raise PermissionDenied("Use the protected recovery procedure to revoke the System Administrator role.")

    if assignment.role.permissions.filter(codename="users.membership.manage", is_active=True).exists():
        if not RBACService.active_role_manager_exists(exclude_user_id=assignment.user_id):
            raise PermissionDenied("This action would remove the last active role manager.")

    assignment.is_active = False
    assignment.save(update_fields=["is_active", "updated_at"])
    AuditService.log(
        action=AuditAction.UPDATE, module="rbac",
        entity_type="UserRoleAssignment", entity_id=assignment.pk,
        actor=request.user,
        changes={"is_active": {"from": True, "to": False}},
    )
    messages.success(request, "Role assignment revoked.")
    return redirect("rbac-assignment-list")


@login_required(login_url="login")
def role_history(request):
    _require(request.user, "audit.logs.view")
    logs = AuditLog.objects.filter(module="rbac").select_related("actor")[:200]
    return render(request, "rbac/role_history.html", {"logs": logs})
