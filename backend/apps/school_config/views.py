from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.school_config.models import AcademicYear, SchoolProfile
from apps.school_config.serializers import AcademicYearSerializer, SchoolProfileSerializer
from apps.rbac.permissions import HasDynamicPermission


class AcademicYearViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for CBSE Academic Years.
    """
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'school_config'
    filterset_fields = ['is_current', 'is_active']
    search_fields = ['name']

    @action(detail=False, methods=['get'])
    def current(self, request):
        current_year = AcademicYear.objects.filter(is_current=True).first()
        if not current_year:
            return Response({'status': 'error', 'message': 'No current academic year configured.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(current_year)
        return Response(serializer.data)


class SchoolProfileViewSet(viewsets.ModelViewSet):
    """
    Manage school affiliation, details, and active defaults.
    """
    queryset = SchoolProfile.objects.all()
    serializer_class = SchoolProfileSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'school_config'
