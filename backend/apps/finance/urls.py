from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.finance.views import (
    FeeHeadViewSet, FeeStructureViewSet, StudentInvoiceViewSet, PaymentTransactionViewSet
)

router = DefaultRouter()
router.register(r'fee-heads', FeeHeadViewSet, basename='fee-head')
router.register(r'fee-structures', FeeStructureViewSet, basename='fee-structure')
router.register(r'invoices', StudentInvoiceViewSet, basename='student-invoice')
router.register(r'payments', PaymentTransactionViewSet, basename='payment-transaction')

urlpatterns = [
    path('', include(router.urls)),
]
