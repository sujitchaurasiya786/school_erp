"""
RBAC Web Views — Management Dashboard for Roles, Permissions, and User Assignments.

Provides full web UI for:
  - Role list and metrics
  - Role creation and editing with permission matrix
  - Role detail and member user assignments (with scope support)
  - Permission registry browser
  - User directory with dynamic role badges and assignment management
"""

from collections import defaultdict
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.rbac.models import ActionCode, ModuleCode, Permission, Role, ScopeType, UserRoleAssignment
from apps.rbac.services import RBACService

User = get_user_model()


def _check_rbac_permission(request, permission_codename):
    """
    Enforces dynamic RBAC permission for dashboard views.
    Superusers with is_superuser=True are allowed administrative UI access,
    or users holding the explicit permission codename.
    """
    if not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True
    return RBACService.has_permission(request.user, permission_codename)


# ==============================================================================
# 1. Dynamic Roles Dashboard
# ==============================================================================
@login_required(login_url='login')
def roles_list_view(request):
    """
    Displays the list of all system and dynamic roles, along with summary metrics.
    """
    if not _check_rbac_permission(request, 'rbac.role.view'):
        messages.error(request, "You do not have permission to view roles.")
        return redirect('home-dashboard')

    search_query = request.GET.get('q', '').strip()
    role_type = request.GET.get('type', 'all')

    roles = Role.objects.annotate(
        assigned_user_count=Count('user_assignments', filter=Q(user_assignments__is_active=True), distinct=True),
        perm_count=Count('permissions', filter=Q(permissions__is_active=True), distinct=True)
    ).order_by('-is_system_role', 'name')

    if search_query:
        roles = roles.filter(Q(name__icontains=search_query) | Q(code__icontains=search_query) | Q(description__icontains=search_query))

    if role_type == 'system':
        roles = roles.filter(is_system_role=True)
    elif role_type == 'custom':
        roles = roles.filter(is_system_role=False)

    total_roles = Role.objects.count()
    system_roles_count = Role.objects.filter(is_system_role=True).count()
    custom_roles_count = total_roles - system_roles_count
    total_permissions = Permission.objects.filter(is_active=True).count()

    context = {
        'roles': roles,
        'search_query': search_query,
        'role_type': role_type,
        'total_roles': total_roles,
        'system_roles_count': system_roles_count,
        'custom_roles_count': custom_roles_count,
        'total_permissions': total_permissions,
    }
    return render(request, 'dashboard/rbac/roles_list.html', context)


# ==============================================================================
# 2. Dynamic Role Create & Edit Views
# ==============================================================================
@login_required(login_url='login')
def role_create_view(request):
    """
    Create a new dynamic role with granular permissions.
    """
    if not _check_rbac_permission(request, 'rbac.role.manage'):
        messages.error(request, "You do not have permission to create roles.")
        return redirect('rbac-roles-list')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        description = request.POST.get('description', '').strip()
        selected_permission_ids = request.POST.getlist('permissions')

        if not code and name:
            code = slugify(name).replace('-', '_')

        if not name or not code:
            messages.error(request, "Role name and role code are required.")
            return redirect('rbac-role-create')

        if Role.objects.filter(code=code).exists():
            messages.error(request, f"A role with code '{code}' already exists.")
            return redirect('rbac-role-create')

        with transaction.atomic():
            role = Role.objects.create(
                name=name,
                code=code,
                description=description,
                is_system_role=False,
                is_active=True,
            )
            if selected_permission_ids:
                perms = Permission.objects.filter(id__in=selected_permission_ids, is_active=True)
                role.permissions.set(perms)

            AuditService.log(
                action=AuditAction.CREATE,
                module="rbac",
                entity_type="Role",
                entity_id=str(role.pk),
                actor=request.user,
                changes={"name": role.name, "code": role.code, "permissions_count": len(selected_permission_ids)},
            )

        messages.success(request, f"Dynamic role '{role.name}' created successfully with {role.permissions.count()} permissions.")
        return redirect('rbac-role-detail', pk=role.pk)

    # Group permissions by module
    all_permissions = Permission.objects.filter(is_active=True).order_by('module', 'name')
    permissions_by_module = defaultdict(list)
    for perm in all_permissions:
        permissions_by_module[perm.get_module_display()].append(perm)

    context = {
        'permissions_by_module': dict(permissions_by_module),
        'is_edit': False,
    }
    return render(request, 'dashboard/rbac/role_form.html', context)


@login_required(login_url='login')
def role_edit_view(request, pk):
    """
    Edit an existing dynamic role's metadata and permission matrix.
    """
    if not _check_rbac_permission(request, 'rbac.role.change'):
        messages.error(request, "You do not have permission to edit roles.")
        return redirect('rbac-roles-list')

    role = get_object_or_404(Role, pk=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        selected_permission_ids = request.POST.getlist('permissions')

        if not name:
            messages.error(request, "Role name cannot be empty.")
            return redirect('rbac-role-edit', pk=role.pk)

        # System roles protection: cannot change code, cannot deactivate
        if role.is_system_role and not is_active:
            messages.error(request, "System roles cannot be deactivated.")
            is_active = True

        with transaction.atomic():
            old_perm_ids = set(role.permissions.values_list('id', flat=True))
            new_perm_ids = set(map(int, selected_permission_ids)) if selected_permission_ids else set()

            role.name = name
            role.description = description
            role.is_active = is_active
            role.save()

            perms = Permission.objects.filter(id__in=new_perm_ids, is_active=True)
            role.permissions.set(perms)

            AuditService.log(
                action=AuditAction.UPDATE,
                module="rbac",
                entity_type="Role",
                entity_id=str(role.pk),
                actor=request.user,
                changes={
                    "name": role.name,
                    "permissions_added": list(new_perm_ids - old_perm_ids),
                    "permissions_removed": list(old_perm_ids - new_perm_ids),
                },
            )

        messages.success(request, f"Role '{role.name}' updated successfully.")
        return redirect('rbac-role-detail', pk=role.pk)

    all_permissions = Permission.objects.filter(is_active=True).order_by('module', 'name')
    role_perm_ids = set(role.permissions.values_list('id', flat=True))

    permissions_by_module = defaultdict(list)
    for perm in all_permissions:
        permissions_by_module[perm.get_module_display()].append(perm)

    context = {
        'role': role,
        'role_perm_ids': role_perm_ids,
        'permissions_by_module': dict(permissions_by_module),
        'is_edit': True,
    }
    return render(request, 'dashboard/rbac/role_form.html', context)


@login_required(login_url='login')
def role_delete_view(request, pk):
    """
    Delete a non-system dynamic role.
    """
    if not _check_rbac_permission(request, 'rbac.role.delete'):
        messages.error(request, "You do not have permission to delete roles.")
        return redirect('rbac-roles-list')

    role = get_object_or_404(Role, pk=pk)

    if role.is_system_role:
        messages.error(request, "System roles are protected and cannot be deleted.")
        return redirect('rbac-role-detail', pk=role.pk)

    if request.method == 'POST':
        role_name = role.name
        role_code = role.code
        with transaction.atomic():
            AuditService.log(
                action=AuditAction.DELETE,
                module="rbac",
                entity_type="Role",
                entity_id=str(role.pk),
                actor=request.user,
                changes={"name": role_name, "code": role_code},
            )
            role.delete()

        messages.success(request, f"Role '{role_name}' has been deleted successfully.")
        return redirect('rbac-roles-list')

    return render(request, 'dashboard/rbac/role_confirm_delete.html', {'role': role})


# ==============================================================================
# 3. Dynamic Role Detail & Member Assignment
# ==============================================================================
@login_required(login_url='login')
def role_detail_view(request, pk):
    """
    Detailed view for a role showing assigned permissions and member assignments.
    Provides modal action to assign/revoke users with scopes.
    """
    if not _check_rbac_permission(request, 'rbac.role.view'):
        messages.error(request, "You do not have permission to view role details.")
        return redirect('rbac-roles-list')

    role = get_object_or_404(Role, pk=pk)

    # Handle inline user assignment POST
    if request.method == 'POST' and 'assign_user' in request.POST:
        if not _check_rbac_permission(request, 'users.membership.manage'):
            messages.error(request, "You do not have permission to assign roles to users.")
            return redirect('rbac-role-detail', pk=role.pk)

        user_id = request.POST.get('user_id')
        scope_type = request.POST.get('scope_type', ScopeType.GLOBAL)
        scope_id = request.POST.get('scope_id', '').strip()

        target_user = get_object_or_404(User, pk=user_id)

        assignment, created = UserRoleAssignment.objects.update_or_create(
            user=target_user,
            role=role,
            scope_type=scope_type,
            defaults={
                'scope_id': scope_id,
                'is_active': True,
            }
        )

        AuditService.log(
            action=AuditAction.CREATE if created else AuditAction.UPDATE,
            module="rbac",
            entity_type="UserRoleAssignment",
            entity_id=str(assignment.pk),
            actor=request.user,
            changes={
                "user": target_user.username,
                "role": role.name,
                "scope_type": scope_type,
                "scope_id": scope_id,
            },
        )
        messages.success(request, f"User '{target_user.username}' assigned to role '{role.name}' successfully.")
        return redirect('rbac-role-detail', pk=role.pk)

    # Permissions grouped by module
    role_perms = role.permissions.filter(is_active=True).order_by('module', 'name')
    permissions_by_module = defaultdict(list)
    for p in role_perms:
        permissions_by_module[p.get_module_display()].append(p)

    # Active user assignments
    assignments = UserRoleAssignment.objects.filter(role=role).select_related('user').order_by('-is_active', 'user__username')

    # Available users for assignment modal
    assigned_user_ids = assignments.filter(is_active=True).values_list('user_id', flat=True)
    available_users = User.objects.filter(is_active=True).exclude(id__in=assigned_user_ids).order_by('username')[:100]

    context = {
        'role': role,
        'permissions_by_module': dict(permissions_by_module),
        'assignments': assignments,
        'available_users': available_users,
        'scope_types': ScopeType.choices,
    }
    return render(request, 'dashboard/rbac/role_detail.html', context)


@login_required(login_url='login')
def role_revoke_assignment_view(request, assignment_id):
    """
    Revokes or removes a UserRoleAssignment.
    """
    if not _check_rbac_permission(request, 'users.membership.manage'):
        messages.error(request, "You do not have permission to revoke role assignments.")
        return redirect('rbac-roles-list')

    assignment = get_object_or_404(UserRoleAssignment, pk=assignment_id)
    role_id = assignment.role.pk
    username = assignment.user.username
    role_name = assignment.role.name

    if request.method == 'POST':
        with transaction.atomic():
            AuditService.log(
                action=AuditAction.DELETE,
                module="rbac",
                entity_type="UserRoleAssignment",
                entity_id=str(assignment.pk),
                actor=request.user,
                changes={"user": username, "role": role_name},
            )
            assignment.delete()

        messages.success(request, f"Revoked role '{role_name}' from user '{username}'.")

    return redirect('rbac-role-detail', pk=role_id)


# ==============================================================================
# 4. Permissions Registry Explorer
# ==============================================================================
@login_required(login_url='login')
def permissions_list_view(request):
    """
    Browse all registered system permissions, filter by module/action, and see assigned roles.
    """
    if not _check_rbac_permission(request, 'rbac.role.view'):
        messages.error(request, "You do not have permission to view permissions.")
        return redirect('home-dashboard')

    module_filter = request.GET.get('module', '')
    action_filter = request.GET.get('action', '')
    search_query = request.GET.get('q', '').strip()

    perms = Permission.objects.filter(is_active=True).prefetch_related('roles').order_by('module', 'action', 'name')

    if module_filter:
        perms = perms.filter(module=module_filter)
    if action_filter:
        perms = perms.filter(action=action_filter)
    if search_query:
        perms = perms.filter(Q(codename__icontains=search_query) | Q(name__icontains=search_query) | Q(description__icontains=search_query))

    context = {
        'permissions': perms,
        'modules': ModuleCode.choices,
        'actions': ActionCode.choices,
        'selected_module': module_filter,
        'selected_action': action_filter,
        'search_query': search_query,
        'total_count': perms.count(),
    }
    return render(request, 'dashboard/rbac/permissions_list.html', context)


# ==============================================================================
# 5. User Directory & Role Assignment Dashboard
# ==============================================================================
@login_required(login_url='login')
def users_list_view(request):
    """
    Management table of all user accounts with their assigned dynamic roles.
    Allows authorized administrators to create new ERP user accounts with default
    credentials (password: 12345678) and mandatory first-login password change.
    """
    if not _check_rbac_permission(request, 'identity.users.view'):
        messages.error(request, "You do not have permission to view users.")
        return redirect('home-dashboard')

    from apps.identity.models import UserType
    from apps.identity.services import IdentityService
    from django.core.exceptions import ValidationError

    # Handle Web User Creation
    if request.method == 'POST' and 'create_user' in request.POST:
        if not _check_rbac_permission(request, 'identity.users.add'):
            messages.error(request, "You do not have permission to create user accounts.")
            return redirect('identity-users-list')

        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        user_type = request.POST.get('user_type', UserType.STAFF)
        phone_number = request.POST.get('phone_number', '').strip()
        role_id = request.POST.get('role_id', '')
        scope_type = request.POST.get('scope_type', ScopeType.GLOBAL)
        scope_id = request.POST.get('scope_id', '').strip()

        if not username or not email:
            messages.error(request, "Both Username and Contact Email are required.")
            return redirect('identity-users-list')

        try:
            new_user = IdentityService.create_user(
                username=username,
                email=email,
                actor=request.user,
                first_name=first_name,
                last_name=last_name,
                user_type=user_type,
                phone_number=phone_number or None,
                role=role_id if role_id else None,
                scope_type=scope_type,
                scope_id=scope_id,
                request=request,
            )
            messages.success(
                request,
                f"User account '{new_user.username}' created successfully with default password '12345678'. Mandatory first-login password change is activated."
            )
            return redirect('identity-users-list')
        except ValidationError as err:
            if hasattr(err, 'message_dict'):
                for field, errs in err.message_dict.items():
                    for e in errs:
                        messages.error(request, f"{field.capitalize()}: {e}")
            elif hasattr(err, 'messages'):
                for e in err.messages:
                    messages.error(request, e)
            else:
                messages.error(request, str(err))
            return redirect('identity-users-list')

    search_query = request.GET.get('q', '').strip()
    user_type_filter = request.GET.get('user_type', '')
    role_filter = request.GET.get('role', '')

    users = User.objects.prefetch_related('role_assignments__role').order_by('-date_joined')

    if search_query:
        users = users.filter(
            Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
        )
    if user_type_filter:
        users = users.filter(user_type=user_type_filter)
    if role_filter:
        users = users.filter(role_assignments__role__code=role_filter, role_assignments__is_active=True)

    all_roles = Role.objects.filter(is_active=True).order_by('name')
    can_add_user = _check_rbac_permission(request, 'identity.users.add')

    context = {
        'users': users[:100],  # paginated or capped at 100 for fast responsiveness
        'search_query': search_query,
        'user_type_filter': user_type_filter,
        'role_filter': role_filter,
        'all_roles': all_roles,
        'user_types': UserType.choices,
        'scope_types': ScopeType.choices,
        'can_add_user': can_add_user,
        'total_count': users.count(),
    }
    return render(request, 'dashboard/rbac/users_list.html', context)
