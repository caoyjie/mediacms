from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0002_user_is_approved"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="session_version",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
