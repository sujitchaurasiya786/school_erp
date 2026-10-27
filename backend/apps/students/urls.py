from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.students.views import (
    StudentViewSet, GuardianViewSet, ClassEnrollmentViewSet, TransferCertificateViewSet
)

router = DefaultRouter()
router.register(r'profiles', StudentViewSet, basename='student')
router.register(r'guardians', GuardianViewSet, basename='guardian')
router.register(r'enrollments', ClassEnrollmentViewSet, basename='class-enrollment')
router.register(r'transfer-certificates', TransferCertificateViewSet, basename='transfer-certificate')

urlpatterns = [
    path('', include(router.urls)),
]
