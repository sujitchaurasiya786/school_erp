from django.urls import path
from apps.analytics.views import DashboardOverviewAPIView

urlpatterns = [
    path('dashboard-overview/', DashboardOverviewAPIView.as_view(), name='dashboard-overview'),
]
