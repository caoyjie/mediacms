from urllib.parse import urlsplit

from rest_framework import serializers

from users.models import User

from files.models import Language, Media, Subtitle
from files.models.utils import validate_external_media_url


class ExternalSubtitleSerializer(serializers.Serializer):
    language = serializers.CharField(max_length=30)
    label = serializers.CharField(max_length=100)
    external_url = serializers.URLField(
        max_length=1000,
        validators=[validate_external_media_url],
    )

    def validate_language(self, value: str) -> str:
        return value.strip().replace("_", "-").lower()

    def validate_external_url(self, value: str) -> str:
        if not urlsplit(value).path.startswith("/media/"):
            raise serializers.ValidationError(
                "external subtitle URL must be below /media/"
            )
        return value

    def to_representation(self, instance):
        if isinstance(instance, Subtitle):
            return {
                "language": instance.language.code,
                "label": instance.language.title,
                "external_url": instance.external_url,
            }
        return super().to_representation(instance)


def reconcile_external_subtitles(media: Media, items: list[dict]) -> None:
    supplied_codes = {item["language"] for item in items}
    for item in items:
        language = Language.objects.filter(code=item["language"]).first()
        if language is None:
            language = Language.objects.create(
                code=item["language"],
                title=item["label"],
            )
        subtitle = Subtitle.objects.filter(
            media=media,
            language=language,
        ).first()
        if subtitle is None:
            Subtitle.objects.create(
                media=media,
                language=language,
                user=media.user,
                subtitle_file="",
                external_url=item["external_url"],
            )
        else:
            subtitle.external_url = item["external_url"]
            subtitle.save(update_fields=["external_url"])

    omitted = media.subtitles.filter(
        external_url__isnull=False,
    ).exclude(language__code__in=supplied_codes)
    omitted.filter(subtitle_file="").delete()
    for subtitle in omitted.exclude(subtitle_file=""):
        subtitle.external_url = None
        subtitle.save(update_fields=["external_url"])


class ExternalMediaSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(write_only=True, required=False)
    subtitles = ExternalSubtitleSerializer(many=True, required=False)
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
            "subtitles",
            "version",
        )
        read_only_fields = ("id", "version")

    def validate_owner_username(self, value: str) -> str:
        if not User.objects.filter(username=value, is_active=True).exists():
            raise serializers.ValidationError("active owner not found")
        return value

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        external_subtitles = instance.subtitles.exclude(
            external_url__isnull=True,
        ).exclude(external_url="")
        representation["subtitles"] = ExternalSubtitleSerializer(
            external_subtitles,
            many=True,
        ).data
        return representation

    def validate_subtitles(self, value: list[dict]) -> list[dict]:
        codes = [item["language"] for item in value]
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError(
                "subtitle language codes must be unique"
            )
        return value

    def create(self, validated_data):
        subtitles = validated_data.pop("subtitles", None)
        owner_username = validated_data.pop("owner_username")
        owner = User.objects.get(username=owner_username, is_active=True)
        media = Media.objects.create(
            user=owner,
            media_file="",
            **validated_data,
        )
        if subtitles is not None:
            reconcile_external_subtitles(media, subtitles)
        return media

    def update(self, instance, validated_data):
        subtitles = validated_data.pop("subtitles", None)
        validated_data.pop("owner_username", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if subtitles is not None:
            reconcile_external_subtitles(instance, subtitles)
        return instance
