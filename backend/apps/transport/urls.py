from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.transport.views import (
    VehicleViewSet, DriverViewSet, RouteViewSet,
    RouteStopViewSet, StudentTransportAllocationViewSet
)

router = DefaultRouter()
router.register(r'vehicles', VehicleViewSet, basename='vehicle')
router.register(r'drivers', DriverViewSet, basename='driver')
router.register(r'routes', RouteViewSet, basename='route')
router.register(r'stops', RouteStopViewSet, basename='route-stop')
router.register(r'allocations', StudentTransportAllocationViewSet, basename='student-transport-allocation')

urlpatterns = [
    path('', include(router.urls)),
]
