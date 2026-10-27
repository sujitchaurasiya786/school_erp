from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.school_config.views import AcademicYearViewSet, SchoolProfileViewSet

router = DefaultRouter()
router.register(r'academic-years', AcademicYearViewSet, basename='academic-year')
router.register(r'profile', SchoolProfileViewSet, basename='school-profile')

urlpatterns = [
    path('', include(router.urls)),
]
