"""
Django Admin registration for the identity app.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.identity.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """
    Extends Django's built-in UserAdmin to include School ERP custom fields.
    """

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "user_type",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = ("user_type", "is_active", "is_staff", "is_superuser", "date_joined")
    search_fields = ("username", "email", "first_name", "last_name", "phone_number")
    ordering = ("-date_joined",)
    readonly_fields = ("date_joined", "last_login", "created_at", "updated_at")

    # Add custom fields to the user edit form fieldsets
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "School ERP Profile",
            {
                "fields": (
                    "user_type",
                    "phone_number",
                    "avatar",
                    "must_change_password",
                )
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        (
            "School ERP Profile",
            {
                "classes": ("wide",),
                "fields": ("email", "user_type", "phone_number", "must_change_password"),
            },
        ),
    )

