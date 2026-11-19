"""
Academics Web URL Configuration for the School ERP Dashboard.
"""
from django.urls import path
from apps.academics.web_views import (
    academic_years_list_view,
    academic_year_activate_view,
    classes_list_view,
    sections_list_view,
    streams_list_view,
    subjects_list_view,
    class_subjects_list_view,
    teacher_assignments_view,
)

urlpatterns = [
    # Academic Year Management
    path('years/', academic_years_list_view, name='academics-years-list'),
    path('years/<int:pk>/activate/', academic_year_activate_view, name='academics-year-activate'),

    # Class Management
    path('classes/', classes_list_view, name='academics-classes-list'),

    # Section Management
    path('sections/', sections_list_view, name='academics-sections-list'),

    # Stream Management (Classes 11-12)
    path('streams/', streams_list_view, name='academics-streams-list'),

    # Subject Master Management
    path('subjects/', subjects_list_view, name='academics-subjects-list'),

    # Class–Subject Mapping Management
    path('class-subjects/', class_subjects_list_view, name='academics-class-subjects-list'),

    # Teacher Assignments Management (Phase 3)
    path('teacher-assignments/', teacher_assignments_view, name='academics-teacher-assignments-list'),
]
