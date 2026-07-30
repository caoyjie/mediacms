import re
from urllib.parse import urlsplit, urlunsplit

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from users.models import User


MAX_DESCRIPTION_LENGTH = 10_000
MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_LOGO_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_LOGO_FORMATS = {"JPEG", "PNG", "WEBP"}


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid")
        return data


class CurrentProfileSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(
        allow_blank=True,
        max_length=254,
        required=False,
    )
    notification_on_comments = StrictBooleanField(required=False)
    thumbnail_url = serializers.SerializerMethodField()
    session_version = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "description",
            "email",
            "notification_on_comments",
            "thumbnail_url",
            "session_version",
        )
        extra_kwargs = {
            "description": {
                "allow_blank": True,
                "max_length": MAX_DESCRIPTION_LENGTH,
                "required": False,
            },
            "name": {
                "allow_blank": True,
                "max_length": 250,
                "required": False,
            },
        }

    def get_thumbnail_url(self, user: User) -> str | None:
        path = user.thumbnail_url()
        if not path:
            return None
        parts = urlsplit(path)
        normalized_path = re.sub(r"/{2,}", "/", parts.path)
        normalized = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                normalized_path,
                parts.query,
                parts.fragment,
            )
        )
        return self.context["request"].build_absolute_uri(normalized)

    def validate_email(self, value: str) -> str:
        if not value:
            return value
        validate_email(value)
        duplicate = (
            User.objects.filter(email__iexact=value)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if duplicate:
            raise serializers.ValidationError(
                "A user with this email already exists."
            )
        return value


class ProfileLogoSerializer(serializers.Serializer):
    logo = serializers.FileField(required=True)

    def validate_logo(self, upload):
        if upload.size > MAX_LOGO_BYTES:
            raise serializers.ValidationError(
                "Logo files must be at most 2 MiB."
            )
        if upload.content_type not in ALLOWED_LOGO_CONTENT_TYPES:
            raise serializers.ValidationError(
                "Logo must be a JPEG, PNG, or WebP image."
            )
        try:
            image = Image.open(upload)
            image.verify()
            image_format = image.format
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise serializers.ValidationError(
                "Logo must contain a valid image."
            ) from exc
        finally:
            upload.seek(0)
        if image_format not in ALLOWED_LOGO_FORMATS:
            raise serializers.ValidationError(
                "Logo must be a JPEG, PNG, or WebP image."
            )
        return upload


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,
        write_only=True,
    )
    new_password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,
        write_only=True,
    )

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError(
                {"current_password": "The current password is incorrect."}
            )
        try:
            validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"new_password": list(exc.messages)}
            ) from exc
        return attrs
