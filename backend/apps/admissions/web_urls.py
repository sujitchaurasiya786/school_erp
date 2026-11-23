"""
Admissions Web URL Configuration for the School ERP Dashboard.
"""
from django.urls import path
from apps.admissions.web_views import (
    applications_list_view,
    application_detail_view,
    application_apply_view,
    seat_quotas_view,
)

urlpatterns = [
    path('applications/', applications_list_view, name='admissions-applications-list'),
    path('applications/apply/', application_apply_view, name='admissions-application-apply'),
    path('applications/<int:pk>/', application_detail_view, name='admissions-application-detail'),
    path('quotas/', seat_quotas_view, name='admissions-quotas-list'),
]
