import pytest
from django.conf import settings
from django.test import Client, override_settings

from users.models import SiteAdministrator, User
from users.permissions import IsSiteAdministrator


@pytest.fixture(autouse=True)
def database_sessions(settings):
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.db"


@pytest.fixture
def site_administrator(db):
    user = User.objects.create_user(
        username="admin",
        password="strong-test-password",
        is_active=True,
        is_staff=True,
        is_superuser=True,
        is_approved=True,
    )
    SiteAdministrator.objects.create(user=user)
    return user


@pytest.mark.django_db
@override_settings(MEDIACMS_SINGLE_ADMIN_MODE=True)
def test_signup_is_closed(site_administrator):
    assert Client().get("/accounts/signup/").status_code == 404


@pytest.mark.django_db
@override_settings(MEDIACMS_SINGLE_ADMIN_MODE=True)
def test_non_bound_active_user_cannot_use_protected_api(site_administrator):
    other = User.objects.create_user(username="other", is_active=True, is_approved=True)
    client = Client()
    client.force_login(other)

    response = client.get("/api/v1/whoami")

    assert response.status_code == 403
    assert response.json() == {"code": "single_administrator_required"}


@pytest.mark.django_db
@override_settings(MEDIACMS_SINGLE_ADMIN_MODE=True)
def test_missing_binding_returns_maintenance_response():
    user = User.objects.create_user(username="orphan", is_active=True, is_approved=True)
    client = Client()
    client.force_login(user)

    response = client.get("/api/v1/whoami")

    assert response.status_code == 503
    assert response.json() == {"code": "site_administrator_unavailable"}


@pytest.mark.django_db
@override_settings(MEDIACMS_SINGLE_ADMIN_MODE=False)
def test_base_settings_retain_multi_user_compatibility():
    user = User.objects.create_user(username="ordinary", is_active=True)
    client = Client()
    client.force_login(user)

    assert client.get("/api/v1/whoami").status_code == 200


def test_drf_permission_delegates_to_singleton(site_administrator, rf):
    request = rf.get("/internal/api/jobs/")
    request.user = site_administrator

    assert IsSiteAdministrator().has_permission(request, view=None)


def test_dependency_apps_remain_installed():
    installed = set(settings.INSTALLED_APPS)
    assert {
        "actions.apps.ActionsConfig",
        "identity_providers.apps.IdentityProvidersConfig",
        "lti.apps.LtiConfig",
        "rbac.apps.RbacConfig",
        "saml_auth.apps.SamlAuthConfig",
    } <= installed
