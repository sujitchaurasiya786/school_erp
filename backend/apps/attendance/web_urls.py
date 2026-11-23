"""
Attendance Web URL Configuration for the School ERP Dashboard.
"""
from django.urls import path
from apps.attendance.web_views import (
    attendance_calendar_view,
    holidays_list_view,
)

urlpatterns = [
    path('calendar/', attendance_calendar_view, name='attendance-calendar'),
    path('holidays/', holidays_list_view, name='attendance-holidays'),
]
