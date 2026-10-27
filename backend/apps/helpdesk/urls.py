from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.helpdesk.views import TicketCategoryViewSet, TicketViewSet

router = DefaultRouter()
router.register(r'categories', TicketCategoryViewSet, basename='ticket-category')
router.register(r'tickets', TicketViewSet, basename='ticket')

urlpatterns = [
    path('', include(router.urls)),
]
