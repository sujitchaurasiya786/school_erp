from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.identity.views import (
    UserViewSet,
    LoginView,
    LogoutView,
    PasswordChangeView,
)

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")

urlpatterns = [
    path("", include(router.urls)),
    path("login/", LoginView.as_view(), name="identity-login"),
    path("logout/", LogoutView.as_view(), name="identity-logout"),
    path("change-password/", PasswordChangeView.as_view(), name="identity-change-password"),
]
