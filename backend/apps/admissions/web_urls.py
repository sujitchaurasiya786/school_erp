"""
Admissions Web URL Configuration for the School ERP Dashboard.
"""
from django.urls import path
from apps.admissions.web_views import (
    applications_list_view,
    application_detail_view,
    seat_quotas_view,
)

urlpatterns = [
    path('applications/', applications_list_view, name='admissions-applications-list'),
    path('applications/<int:pk>/', application_detail_view, name='admissions-application-detail'),
    path('quotas/', seat_quotas_view, name='admissions-quotas-list'),
]
