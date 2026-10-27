from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserType(models.TextChoices):
    ADMIN = 'ADMIN', _('System Administrator')
    PRINCIPAL = 'PRINCIPAL', _('Principal')
    TEACHER = 'TEACHER', _('Teacher')
    STAFF = 'STAFF', _('Non-Teaching Staff')
    ACCOUNTANT = 'ACCOUNTANT', _('Accountant')
    PARENT = 'PARENT', _('Parent/Guardian')
    STUDENT = 'STUDENT', _('Student')


class User(AbstractUser):
    """
    Custom user model for School ERP.
    Supports email, phone number, and dynamic role assignments via the rbac app.
    """
    user_type = models.CharField(
        max_length=20,
        choices=UserType.choices,
        default=UserType.STAFF,
        db_index=True,
        help_text=_("Primary persona/category for this user account.")
    )
    email = models.EmailField(_('email address'), max_length=191, unique=True, db_index=True)
    phone_number = models.CharField(
        _('phone number'),
        max_length=20,
        blank=True,
        null=True,
        db_index=True
    )
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'identity_users'
        verbose_name = _('User')
        verbose_name_plural = _('Users')
        ordering = ['-date_joined']

    def __str__(self):
        full_name = self.get_full_name()
        return f"{full_name} ({self.username})" if full_name else self.username

    @property
    def full_name(self):
        return self.get_full_name() or self.username
