from django.conf import settings
from django.test import SimpleTestCase


class DatabaseHealthSettingsTests(SimpleTestCase):
    def test_database_connections_are_health_checked_before_reuse(self) -> None:
        self.assertIs(settings.DATABASES["default"]["CONN_HEALTH_CHECKS"], True)
