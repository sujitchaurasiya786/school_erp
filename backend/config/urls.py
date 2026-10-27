from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([AllowAny])
def api_root(request):
    """
    Root endpoint for School ERP REST API v1.
    """
    return Response({
        'status': 'online',
        'system': 'CBSE School ERP System',
        'api_version': 'v1',
        'endpoints': {
            'identity': '/api/v1/identity/',
            'rbac': '/api/v1/rbac/',
            'school_config': '/api/v1/school-config/',
            'admissions': '/api/v1/admissions/',
            'students': '/api/v1/students/',
            'academics': '/api/v1/academics/',
            'attendance': '/api/v1/attendance/',
            'finance': '/api/v1/finance/',
            'examinations': '/api/v1/examinations/',
            'hrms': '/api/v1/hrms/',
            'library': '/api/v1/library/',
            'transport': '/api/v1/transport/',
            'inventory': '/api/v1/inventory/',
            'helpdesk': '/api/v1/helpdesk/',
            'analytics': '/api/v1/analytics/',
        }
    })


# API v1 URLs
api_v1_patterns = [
    path('', api_root, name='api-root'),
    path('identity/', include('apps.identity.urls')),
    path('rbac/', include('apps.rbac.urls')),
    path('school-config/', include('apps.school_config.urls')),
    path('admissions/', include('apps.admissions.urls')),
    path('students/', include('apps.students.urls')),
    path('academics/', include('apps.academics.urls')),
    path('attendance/', include('apps.attendance.urls')),
    path('finance/', include('apps.finance.urls')),
    path('examinations/', include('apps.examinations.urls')),
    path('hrms/', include('apps.hrms.urls')),
    path('library/', include('apps.library.urls')),
    path('transport/', include('apps.transport.urls')),
    path('inventory/', include('apps.inventory.urls')),
    path('helpdesk/', include('apps.helpdesk.urls')),
    path('analytics/', include('apps.analytics.urls')),
]

from config.views import dashboard_view

urlpatterns = [
    path('', dashboard_view, name='home-dashboard'),
    path('admin/', admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
