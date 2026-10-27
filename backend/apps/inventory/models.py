from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class ItemCategory(models.TextChoices):
    ASSET = 'ASSET', _('Fixed Asset (Furniture, Computer, Lab Equipment)')
    CONSUMABLE = 'CONSUMABLE', _('Consumable (Stationery, Sports Items, Chemicals)')


class Vendor(TimeStampedModel):
    name = models.CharField(max_length=150)
    contact_person = models.CharField(max_length=100, blank=True, default='')
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True, default='')
    gstin = models.CharField(max_length=50, blank=True, default='')
    address = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'inventory_vendors'

    def __str__(self):
        return self.name


class InventoryItem(TimeStampedModel):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    category = models.CharField(max_length=20, choices=ItemCategory.choices, default=ItemCategory.CONSUMABLE)
    unit_of_measure = models.CharField(max_length=20, default='Pcs')
    current_stock = models.IntegerField(default=0)
    low_stock_threshold = models.IntegerField(default=5)

    class Meta:
        db_table = 'inventory_items'

    def __str__(self):
        return f"{self.name} ({self.code}) - Stock: {self.current_stock} {self.unit_of_measure}"


class StockMovementType(models.TextChoices):
    PURCHASE = 'PURCHASE', _('Purchase Inward')
    ISSUE = 'ISSUE', _('Issued to Staff / Department')
    RETURN = 'RETURN', _('Returned to Inventory')
    DISPOSAL = 'DISPOSAL', _('Disposed / Damaged')


class StockMovement(TimeStampedModel):
    """
    Every stock change occurs strictly via a recorded stock movement.
    """
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=StockMovementType.choices)
    quantity = models.IntegerField(help_text=_("Positive number of units moved."))
    source_location = models.CharField(max_length=100, blank=True, default='Central Store')
    destination_location = models.CharField(max_length=100, blank=True, default='')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'inventory_stock_movements'

    def __str__(self):
        return f"{self.movement_type}: {self.quantity} {self.item.unit_of_measure} of {self.item.name}"
