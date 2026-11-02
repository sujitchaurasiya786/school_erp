from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.identity.models import User


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.ReadOnlyField()
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "full_name",
            "phone_number",
            "user_type",
            "is_active",
            "must_change_password",
            "date_joined",
            "avatar",
        ]
        read_only_fields = ["id", "date_joined"]
        extra_kwargs = {
            "password": {"write_only": True},
        }

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for the authenticated user's own profile view/edit.
    Username and email are read-only to prevent accidental changes.
    """

    full_name = serializers.ReadOnlyField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone_number",
            "user_type",
            "must_change_password",
            "avatar",
        ]
        read_only_fields = ["id", "username", "email", "user_type", "must_change_password"]


class PasswordChangeSerializer(serializers.Serializer):
    """
    Validates a password-change request.
    Verifies the current password before accepting the new one.
    """

    current_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        # Run Django's full password validator suite (min length, common, etc.)
        validate_password(value, user=user)
        return value

    def validate(self, data):
        if data.get("current_password") == data.get("new_password"):
            raise serializers.ValidationError(
                {"new_password": "New password must differ from the current password."}
            )
        return data
