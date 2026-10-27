from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.transport.models import Vehicle, Driver, Route, RouteStop, StudentTransportAllocation
from apps.transport.serializers import (
    VehicleSerializer, DriverSerializer, RouteSerializer,
    RouteStopSerializer, StudentTransportAllocationSerializer
)
from apps.rbac.permissions import HasDynamicPermission


class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.all()
    serializer_class = VehicleSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'transport'
    search_fields = ['registration_number', 'vehicle_model']


class DriverViewSet(viewsets.ModelViewSet):
    queryset = Driver.objects.all()
    serializer_class = DriverSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'transport'
    search_fields = ['name', 'phone', 'license_number']


class RouteViewSet(viewsets.ModelViewSet):
    queryset = Route.objects.select_related('vehicle', 'driver').prefetch_related('stops').all()
    serializer_class = RouteSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'transport'


class RouteStopViewSet(viewsets.ModelViewSet):
    queryset = RouteStop.objects.select_related('route').all()
    serializer_class = RouteStopSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'transport'
    filterset_fields = ['route']


class StudentTransportAllocationViewSet(viewsets.ModelViewSet):
    queryset = StudentTransportAllocation.objects.select_related('student', 'route_stop__route').all()
    serializer_class = StudentTransportAllocationSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'transport'
