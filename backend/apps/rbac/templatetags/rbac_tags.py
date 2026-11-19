"""
Template tags and filters for dynamic RBAC authorization and navigation in templates.
"""
from django import template
from apps.rbac.services import RBACService

register = template.Library()


@register.filter(name='has_perm')
def has_perm(user, codename):
    """
    Check if user has a single permission codename via RBACService.
    Usage: {% if request.user|has_perm:'identity.users.view' %}
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return RBACService.has_permission(user, codename)


@register.filter(name='has_any_perm')
def has_any_perm(user, codenames_str):
    """
    Check if user has ANY of the comma-separated permission codenames via RBACService.
    Usage: {% if request.user|has_any_perm:'identity.users.view,rbac.role.view' %}
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    codenames = [c.strip() for c in codenames_str.split(',') if c.strip()]
    return RBACService.has_any_permission(user, codenames)


@register.simple_tag(takes_context=True)
def nav_active(context, url_names_csv):
    """
    Returns 'active' if current view's url_name matches any in the CSV list.
    Usage: {% nav_active 'home-dashboard' %}
    """
    request = context.get('request')
    if not request or not getattr(request, 'resolver_match', None):
        return ''
    current_name = getattr(request.resolver_match, 'url_name', '')
    targets = [t.strip() for t in url_names_csv.split(',') if t.strip()]
    if current_name in targets:
        return 'active'
    return ''


@register.simple_tag(takes_context=True)
def nav_group_active(context, url_names_csv, path_prefixes_csv=''):
    """
    Returns 'pc-trigger active' if current page belongs to the group.
    Usage: {% nav_group_active 'identity-users-list,rbac-roles-list' '/dashboard/rbac/,/dashboard/users/' %}
    """
    request = context.get('request')
    if not request:
        return ''

    # Check url_names
    current_name = ''
    if getattr(request, 'resolver_match', None):
        current_name = getattr(request.resolver_match, 'url_name', '')
    targets = [t.strip() for t in url_names_csv.split(',') if t.strip()]
    if current_name and current_name in targets:
        return 'pc-trigger active'

    # Check path prefixes
    if path_prefixes_csv:
        prefixes = [p.strip() for p in path_prefixes_csv.split(',') if p.strip()]
        for prefix in prefixes:
            if request.path.startswith(prefix):
                return 'pc-trigger active'

    return ''
