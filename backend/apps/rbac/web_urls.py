"""
Web URL routing for RBAC & User Management Dashboard views.
"""
from django.urls import path
from apps.rbac.web_views import (
    roles_list_view,
    role_create_view,
    role_detail_view,
    role_edit_view,
    role_delete_view,
    role_revoke_assignment_view,
    permissions_list_view,
    users_list_view,
)

urlpatterns = [
    # Dynamic Roles Management
    path('roles/', roles_list_view, name='rbac-roles-list'),
    path('roles/create/', role_create_view, name='rbac-role-create'),
    path('roles/<int:pk>/', role_detail_view, name='rbac-role-detail'),
    path('roles/<int:pk>/edit/', role_edit_view, name='rbac-role-edit'),
    path('roles/<int:pk>/delete/', role_delete_view, name='rbac-role-delete'),
    path('assignments/<int:assignment_id>/revoke/', role_revoke_assignment_view, name='rbac-revoke-assignment'),

    # Permissions Explorer
    path('permissions/', permissions_list_view, name='rbac-permissions-list'),

    # User Directory & Role Assignment
    path('users/', users_list_view, name='identity-users-list'),
]
