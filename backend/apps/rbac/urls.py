from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.rbac.views import RoleViewSet, PermissionViewSet, UserRoleAssignmentViewSet

router = DefaultRouter()
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'permissions', PermissionViewSet, basename='permission')
router.register(r'assignments', UserRoleAssignmentViewSet, basename='user-role-assignment')

urlpatterns = [
    path('', include(router.urls)),
]
