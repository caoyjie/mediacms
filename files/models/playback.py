from django.core.validators import MinValueValidator
from django.db import models


class MediaPlaybackProgress(models.Model):
    media = models.ForeignKey("Media", on_delete=models.CASCADE, related_name="playback_progress")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="media_playback_progress")
    asset_version = models.ForeignKey("MediaAssetVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="playback_progress")
    position_seconds = models.DecimalField(max_digits=12, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    duration_seconds = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)])
    completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("media", "user"), name="files_playback_media_user_uniq")]
        indexes = [models.Index(fields=("user", "updated_at"), name="files_pb_user_updated_idx")]
