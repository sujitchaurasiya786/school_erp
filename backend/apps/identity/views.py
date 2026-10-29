"""
Identity views for School ERP.

Endpoints provided:
  UserViewSet:
    GET/PATCH /api/v1/identity/users/me/     — own profile, no RBAC restriction
    GET       /api/v1/identity/users/        — list users (identity.users.view)
    POST      /api/v1/identity/users/        — create user (identity.users.add)
    PUT/PATCH /api/v1/identity/users/{id}/   — edit user (identity.users.change)
    DELETE    /api/v1/identity/users/{id}/   — soft-deactivate (identity.users.delete)

  LoginView:
    POST /api/v1/identity/login/   — session login (AllowAny)

  LogoutView:
    POST /api/v1/identity/logout/  — session logout (IsAuthenticated)

  PasswordChangeView:
    POST /api/v1/identity/change-password/ — change own password (IsAuthenticated)
"""
from django.contrib.auth import authenticate, login, logout
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.identity.models import User
from apps.identity.serializers import (
    UserSerializer,
    UserProfileSerializer,
    PasswordChangeSerializer,
)
from apps.rbac.permissions import HasDynamicPermission


class UserViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for managing user accounts.
    The 'me' action bypasses RBAC — any authenticated user may view/edit own profile.
    """

    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = "identity"
    filterset_fields = ["user_type", "is_active"]
    search_fields = ["username", "email", "first_name", "last_name", "phone_number"]

    @action(
        detail=False,
        methods=["get", "patch"],
        permission_classes=[IsAuthenticated],
        url_path="me",
    )
    def me(self, request):
        """
        Retrieve or partially update the currently authenticated user's profile.
        Available to every authenticated user regardless of RBAC roles.
        """
        if request.method == "GET":
            serializer = UserProfileSerializer(request.user)
            return Response(serializer.data)

        serializer = UserProfileSerializer(
            request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def perform_destroy(self, instance):
        """Soft-delete: deactivate instead of hard-delete to preserve audit trail."""
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        AuditService.log(
            action=AuditAction.UPDATE,
            module="identity",
            entity_type="User",
            entity_id=str(instance.pk),
            actor=self.request.user,
            reason="User account deactivated",
            changes={"is_active": {"from": True, "to": False}},
        )


class LoginView(APIView):
    """
    Session-based login endpoint.

    POST /api/v1/identity/login/
    Body: { "username": "...", "password": "..." }

    Returns 200 with user profile on success.
    Returns 401 on bad credentials or inactive account.
    CSRF protection is enforced by Django's middleware for same-origin requests.
    """

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")

        if not username or not password:
            return Response(
                {"status": "error", "message": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)

        if user is None:
            return Response(
                {"status": "error", "message": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {
                    "status": "error",
                    "message": "This account has been deactivated. Contact the administrator.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)

        AuditService.log(
            action=AuditAction.LOGIN,
            module="identity",
            entity_type="User",
            entity_id=str(user.pk),
            actor=user,
            request=request,
        )

        serializer = UserProfileSerializer(user)
        return Response(
            {
                "status": "ok",
                "message": "Logged in successfully.",
                "must_change_password": user.must_change_password,
                "user": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """
    Session logout endpoint.

    POST /api/v1/identity/logout/

    Clears the server-side session and logs the action.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        AuditService.log(
            action=AuditAction.LOGOUT,
            module="identity",
            entity_type="User",
            entity_id=str(request.user.pk),
            actor=request.user,
            request=request,
        )
        logout(request)
        return Response(
            {"status": "ok", "message": "Logged out successfully."},
            status=status.HTTP_200_OK,
        )


class PasswordChangeView(APIView):
    """
    Change own password.

    POST /api/v1/identity/change-password/
    Body: { "current_password": "...", "new_password": "..." }

    Clears must_change_password flag on success.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password", "updated_at"])

        # Re-authenticate to keep session alive after password change
        updated_user = authenticate(
            request,
            username=user.username,
            password=serializer.validated_data["new_password"],
        )
        if updated_user:
            login(request, updated_user)

        AuditService.log(
            action=AuditAction.UPDATE,
            module="identity",
            entity_type="User",
            entity_id=str(user.pk),
            actor=user,
            reason="Password changed by user.",
        )

        return Response(
            {"status": "ok", "message": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )
