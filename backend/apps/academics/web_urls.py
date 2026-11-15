"""
Academics Web URL Configuration for the School ERP Dashboard.
"""
from django.urls import path
from apps.academics.web_views import (
    academic_years_list_view,
    academic_year_activate_view,
    classes_list_view,
)

urlpatterns = [
    # Academic Year Management
    path('years/', academic_years_list_view, name='academics-years-list'),
    path('years/<int:pk>/activate/', academic_year_activate_view, name='academics-year-activate'),

    # Class Management
    path('classes/', classes_list_view, name='academics-classes-list'),
]
