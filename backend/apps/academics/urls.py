from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.academics.views import (
    SchoolClassViewSet, SectionViewSet, SubjectViewSet,
    ClassSubjectViewSet, TimetablePeriodViewSet, TimetableSlotViewSet
)

router = DefaultRouter()
router.register(r'classes', SchoolClassViewSet, basename='school-class')
router.register(r'sections', SectionViewSet, basename='section')
router.register(r'subjects', SubjectViewSet, basename='subject')
router.register(r'class-subjects', ClassSubjectViewSet, basename='class-subject')
router.register(r'periods', TimetablePeriodViewSet, basename='timetable-period')
router.register(r'timetable-slots', TimetableSlotViewSet, basename='timetable-slot')

urlpatterns = [
    path('', include(router.urls)),
]
