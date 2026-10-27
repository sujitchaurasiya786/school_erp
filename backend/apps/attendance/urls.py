from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.attendance.views import (
    StudentDailyAttendanceViewSet, HolidayViewSet, AttendanceCorrectionRequestViewSet
)

router = DefaultRouter()
router.register(r'daily', StudentDailyAttendanceViewSet, basename='daily-attendance')
router.register(r'holidays', HolidayViewSet, basename='holiday')
router.register(r'corrections', AttendanceCorrectionRequestViewSet, basename='attendance-correction')

urlpatterns = [
    path('', include(router.urls)),
]
