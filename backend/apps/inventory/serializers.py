from rest_framework import serializers
from apps.inventory.models import Vendor, InventoryItem, StockMovement


class VendorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = '__all__'


class InventoryItemSerializer(serializers.ModelSerializer):
    is_low_stock = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            'id', 'name', 'code', 'category', 'unit_of_measure',
            'current_stock', 'low_stock_threshold', 'is_low_stock'
        ]
        read_only_fields = ['id', 'current_stock']

    def get_is_low_stock(self, obj):
        return obj.current_stock <= obj.low_stock_threshold


class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    actor_name = serializers.ReadOnlyField(source='actor.get_full_name')

    class Meta:
        model = StockMovement
        fields = [
            'id', 'item', 'item_name', 'movement_type', 'quantity',
            'source_location', 'destination_location', 'actor', 'actor_name',
            'notes', 'created_at'
        ]
        read_only_fields = ['id', 'actor', 'created_at']
