from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0019_external_media_assets"),
    ]

    operations = [
        migrations.AddField(
            model_name="media",
            name="external_sync_version",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
