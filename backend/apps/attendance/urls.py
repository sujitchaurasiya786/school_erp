from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.attendance.views import (
    StudentDailyAttendanceViewSet, HolidayViewSet, AttendanceCorrectionRequestViewSet,
    WorkingDayConfigViewSet, SpecialDayOverrideViewSet, CalendarResolutionViewSet
)

router = DefaultRouter()
router.register(r'daily', StudentDailyAttendanceViewSet, basename='daily-attendance')
router.register(r'holidays', HolidayViewSet, basename='holiday')
router.register(r'corrections', AttendanceCorrectionRequestViewSet, basename='attendance-correction')
router.register(r'calendar/working-days', WorkingDayConfigViewSet, basename='working-day-config')
router.register(r'calendar/overrides', SpecialDayOverrideViewSet, basename='special-day-override')
router.register(r'calendar', CalendarResolutionViewSet, basename='calendar-resolution')

urlpatterns = [
    path('', include(router.urls)),
]
