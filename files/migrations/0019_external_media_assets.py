from django.db import migrations, models

import files.models.utils


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0018_embedmediacourse"),
    ]

    operations = [
        migrations.AddField(
            model_name="media",
            name="backend_media_id",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="media",
            name="external_cover_url",
            field=models.URLField(
                blank=True,
                max_length=1000,
                null=True,
                validators=[files.models.utils.validate_external_media_url],
            ),
        ),
        migrations.AddField(
            model_name="media",
            name="external_hls_url",
            field=models.URLField(
                blank=True,
                max_length=1000,
                null=True,
                validators=[files.models.utils.validate_external_media_url],
            ),
        ),
        migrations.AddField(
            model_name="media",
            name="external_poster_url",
            field=models.URLField(
                blank=True,
                max_length=1000,
                null=True,
                validators=[files.models.utils.validate_external_media_url],
            ),
        ),
        migrations.AddField(
            model_name="subtitle",
            name="external_url",
            field=models.URLField(
                blank=True,
                max_length=1000,
                null=True,
                validators=[files.models.utils.validate_external_media_url],
            ),
        ),
    ]
