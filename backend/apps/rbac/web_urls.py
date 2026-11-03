from django.urls import path

from apps.rbac import web_views

urlpatterns = [
    path("roles/", web_views.role_list, name="rbac-role-list"),
    path("roles/create/", web_views.role_form, name="rbac-role-create"),
    path("roles/<int:role_id>/edit/", web_views.role_form, name="rbac-role-edit"),
    path("roles/<int:role_id>/deactivate/", web_views.role_deactivate, name="rbac-role-deactivate"),
    path("permissions/", web_views.permission_matrix, name="rbac-permission-matrix"),
    path("assignments/", web_views.assignment_list, name="rbac-assignment-list"),
    path("assignments/create/", web_views.assignment_create, name="rbac-assignment-create"),
    path("assignments/<int:assignment_id>/revoke/", web_views.assignment_revoke, name="rbac-assignment-revoke"),
    path("history/", web_views.role_history, name="rbac-role-history"),
]
