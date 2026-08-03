from django.conf import settings


def test_pytest_does_not_enable_persistent_postgres_pool():
    assert "pool" not in settings.DATABASES["default"].get("OPTIONS", {})
