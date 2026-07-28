from rest_framework import serializers

from users.models import User

from files.models import Media


class ExternalMediaSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(write_only=True, required=False)
    version = serializers.IntegerField(source="external_sync_version", read_only=True)

    class Meta:
        model = Media
        fields = (
            "id",
            "backend_media_id",
            "owner_username",
            "title",
            "description",
            "external_hls_url",
            "external_poster_url",
            "external_cover_url",
            "version",
        )
        read_only_fields = ("id", "version")

    def validate_owner_username(self, value: str) -> str:
        if not User.objects.filter(username=value, is_active=True).exists():
            raise serializers.ValidationError("active owner not found")
        return value

    def create(self, validated_data):
        owner_username = validated_data.pop("owner_username")
        owner = User.objects.get(username=owner_username, is_active=True)
        return Media.objects.create(user=owner, media_file="", **validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("owner_username", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance
