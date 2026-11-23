from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.students.views import (
    BonafideCertificateViewSet, ClassEnrollmentViewSet, GuardianViewSet,
    StudentDocumentViewSet, StudentViewSet, TransferCertificateViewSet
)

router = DefaultRouter()
router.register(r'profiles', StudentViewSet, basename='student')
router.register(r'guardians', GuardianViewSet, basename='guardian')
router.register(r'enrollments', ClassEnrollmentViewSet, basename='class-enrollment')
router.register(r'transfer-certificates', TransferCertificateViewSet, basename='transfer-certificate')
router.register(r'bonafide-certificates', BonafideCertificateViewSet, basename='bonafide-certificate')
router.register(r'documents', StudentDocumentViewSet, basename='student-document')

urlpatterns = [
    path('', include(router.urls)),
]
