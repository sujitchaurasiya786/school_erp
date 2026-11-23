from django.urls import path

from apps.students.web_views import (
    certificates_list_view, class_promotion_view, guardians_list_view,
    print_bonafide_view, print_tc_view, student_create_view,
    student_detail_view, students_list_view
)

urlpatterns = [
    path('', students_list_view, name='students-student-list'),
    path('create/', student_create_view, name='students-student-create'),
    path('<int:pk>/', student_detail_view, name='students-student-detail'),
    path('promotion/', class_promotion_view, name='students-class-promotion'),
    path('certificates/', certificates_list_view, name='students-certificates-list'),
    path('tc/<int:pk>/print/', print_tc_view, name='students-tc-print'),
    path('bonafide/<int:pk>/print/', print_bonafide_view, name='students-bonafide-print'),
    path('guardians/', guardians_list_view, name='students-guardians-list'),
]
