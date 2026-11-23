"""
Attendance Web URL Configuration for the School ERP Dashboard.
"""
from django.urls import path
from apps.attendance.web_views import (
    attendance_calendar_view,
    holidays_list_view,
    daily_attendance_view,
    attendance_corrections_view,
    staff_attendance_view,
    attendance_reports_view,
    self_attendance_view,
)

urlpatterns = [
    path('daily/', daily_attendance_view, name='attendance-daily'),
    path('self-mark/', self_attendance_view, name='attendance-self-mark'),
    path('staff/', staff_attendance_view, name='attendance-staff'),
    path('reports/', attendance_reports_view, name='attendance-reports'),
    path('corrections/', attendance_corrections_view, name='attendance-corrections'),
    path('calendar/', attendance_calendar_view, name='attendance-calendar'),
    path('holidays/', holidays_list_view, name='attendance-holidays'),
]
