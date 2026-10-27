from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from apps.inventory.models import Vendor, InventoryItem, StockMovement, StockMovementType
from apps.inventory.serializers import (
    VendorSerializer, InventoryItemSerializer, StockMovementSerializer
)
from apps.rbac.permissions import HasDynamicPermission
from common.exceptions import SchoolERPException


class VendorViewSet(viewsets.ModelViewSet):
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'inventory'
    search_fields = ['name', 'contact_person', 'phone', 'gstin']


class InventoryItemViewSet(viewsets.ModelViewSet):
    queryset = InventoryItem.objects.all()
    serializer_class = InventoryItemSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'inventory'
    filterset_fields = ['category']
    search_fields = ['name', 'code']


class StockMovementViewSet(viewsets.ModelViewSet):
    queryset = StockMovement.objects.select_related('item', 'actor').all()
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated, HasDynamicPermission]
    module_name = 'inventory'
    filterset_fields = ['item', 'movement_type']

    @transaction.atomic
    def perform_create(self, serializer):
        item = serializer.validated_data['item']
        qty = serializer.validated_data['quantity']
        mtype = serializer.validated_data['movement_type']

        if qty <= 0:
            raise SchoolERPException("Movement quantity must be positive.")

        if mtype in (StockMovementType.PURCHASE, StockMovementType.RETURN):
            item.current_stock += qty
        elif mtype in (StockMovementType.ISSUE, StockMovementType.DISPOSAL):
            if item.current_stock < qty:
                raise SchoolERPException(f"Insufficient stock. Available: {item.current_stock}, Requested: {qty}")
            item.current_stock -= qty

        item.save()
        serializer.save(actor=self.request.user)
