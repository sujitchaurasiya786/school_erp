from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
# pyrefly: ignore[missing-import]
from apps.identity.models import User
from apps.identity.serializers import UserSerializer, UserProfileSerializer
from apps.rbac.permissions import HasDynamicPermission


class UserViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for managing user accounts.
    """
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'identity'
    filterset_fields = ['user_type', 'is_active']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'phone_number']

    @action(detail=False, methods=['get', 'patch'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """
        Endpoint to retrieve and update the currently authenticated user's profile.
        """
        if request.method == 'GET':
            serializer = UserProfileSerializer(request.user)
            return Response(serializer.data)
        elif request.method == 'PATCH':
            serializer = UserProfileSerializer(request.user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
