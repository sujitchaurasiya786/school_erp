from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.inventory.views import VendorViewSet, InventoryItemViewSet, StockMovementViewSet

router = DefaultRouter()
router.register(r'vendors', VendorViewSet, basename='vendor')
router.register(r'items', InventoryItemViewSet, basename='inventory-item')
router.register(r'movements', StockMovementViewSet, basename='stock-movement')

urlpatterns = [
    path('', include(router.urls)),
]
