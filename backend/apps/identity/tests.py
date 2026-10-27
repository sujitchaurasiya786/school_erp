from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from apps.identity.models import User, UserType


class IdentityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_superuser(
            username='admin_test',
            email='admin@test.com',
            password='Password123!',
            user_type=UserType.ADMIN
        )

    def test_user_creation(self):
        user = User.objects.create_user(
            username='teacher1',
            email='teacher1@test.com',
            password='Password123!',
            user_type=UserType.TEACHER
        )
        self.assertEqual(user.user_type, UserType.TEACHER)
        self.assertEqual(str(user), 'teacher1')

    def test_user_me_endpoint(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/v1/identity/users/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'admin_test')
