from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.examinations.views import ExamTermViewSet, AssessmentViewSet, ExamMarkViewSet

router = DefaultRouter()
router.register(r'terms', ExamTermViewSet, basename='exam-term')
router.register(r'assessments', AssessmentViewSet, basename='exam-assessment')
router.register(r'marks', ExamMarkViewSet, basename='exam-mark')

urlpatterns = [
    path('', include(router.urls)),
]
