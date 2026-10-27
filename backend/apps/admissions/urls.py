from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.admissions.views import AdmissionApplicationViewSet, SeatQuotaViewSet, ApplicationDocumentViewSet

router = DefaultRouter()
router.register(r'applications', AdmissionApplicationViewSet, basename='admission-application')
router.register(r'seat-quotas', SeatQuotaViewSet, basename='seat-quota')
router.register(r'documents', ApplicationDocumentViewSet, basename='admission-document')

urlpatterns = [
    path('', include(router.urls)),
]
