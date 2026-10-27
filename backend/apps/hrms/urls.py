from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.hrms.views import (
    DepartmentViewSet, DesignationViewSet, StaffProfileViewSet,
    StaffLeaveRequestViewSet, PayrollRunViewSet, PayslipViewSet
)

router = DefaultRouter()
router.register(r'departments', DepartmentViewSet, basename='department')
router.register(r'designations', DesignationViewSet, basename='designation')
router.register(r'staff', StaffProfileViewSet, basename='staff')
router.register(r'leaves', StaffLeaveRequestViewSet, basename='staff-leave')
router.register(r'payroll-runs', PayrollRunViewSet, basename='payroll-run')
router.register(r'payslips', PayslipViewSet, basename='payslip')

urlpatterns = [
    path('', include(router.urls)),
]
