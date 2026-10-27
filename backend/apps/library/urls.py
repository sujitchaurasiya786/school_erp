from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.library.views import BookCatalogViewSet, BookCopyViewSet, BookIssueRecordViewSet

router = DefaultRouter()
router.register(r'catalogs', BookCatalogViewSet, basename='book-catalog')
router.register(r'copies', BookCopyViewSet, basename='book-copy')
router.register(r'issues', BookIssueRecordViewSet, basename='book-issue')

urlpatterns = [
    path('', include(router.urls)),
]
