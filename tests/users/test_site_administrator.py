import pytest
from django.contrib.admin.sites import AdminSite
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import RequestFactory

from users.admin import SiteAdministratorAdmin, UserAdmin
from users.models import SiteAdministrator, User


@pytest.mark.django_db
def test_only_default_singleton_key_is_allowed():
    user = User.objects.create_user(
        username="admin",
        email="admin@example.invalid",
        password="strong-test-password",
    )
    SiteAdministrator.objects.create(user=user)

    with pytest.raises(IntegrityError), transaction.atomic():
        SiteAdministrator.objects.create(
            singleton_key="other",
            user=User.objects.create_user(username="other"),
        )


@pytest.mark.django_db
def test_init_command_is_idempotent(monkeypatch):
    monkeypatch.setenv("MEDIACMS_ADMIN_PASSWORD", "strong-test-password")

    for _ in range(2):
        call_command(
            "init_site_administrator",
            username="admin",
            email="admin@example.invalid",
            interactive=False,
        )

    user = User.objects.get()
    assert SiteAdministrator.objects.get().user == user
    assert user.is_active and user.is_staff and user.is_superuser and user.is_approved
    assert user.check_password("strong-test-password")


@pytest.mark.django_db
def test_noninteractive_initialization_requires_environment_password(monkeypatch):
    monkeypatch.delenv("MEDIACMS_ADMIN_PASSWORD", raising=False)

    with pytest.raises(CommandError, match="MEDIACMS_ADMIN_PASSWORD"):
        call_command(
            "init_site_administrator",
            username="admin",
            email="admin@example.invalid",
            interactive=False,
        )


@pytest.mark.django_db
def test_rebinding_requires_explicit_flag_and_deactivates_other_users(monkeypatch):
    monkeypatch.setenv("MEDIACMS_ADMIN_PASSWORD", "strong-test-password")
    call_command(
        "init_site_administrator",
        username="first",
        email="first@example.invalid",
        interactive=False,
    )

    with pytest.raises(CommandError, match="--rebind"):
        call_command(
            "init_site_administrator",
            username="second",
            email="second@example.invalid",
            interactive=False,
        )

    call_command(
        "init_site_administrator",
        username="second",
        email="second@example.invalid",
        interactive=False,
        rebind=True,
    )

    first = User.objects.get(username="first")
    second = User.objects.get(username="second")
    assert not first.is_active and not first.is_staff and not first.is_superuser
    assert SiteAdministrator.is_site_administrator(second)
    assert not SiteAdministrator.is_site_administrator(first)


@pytest.mark.django_db
def test_django_admin_only_exposes_bound_user(monkeypatch):
    monkeypatch.setenv("MEDIACMS_ADMIN_PASSWORD", "strong-test-password")
    call_command(
        "init_site_administrator",
        username="admin",
        email="admin@example.invalid",
        interactive=False,
    )
    admin_user = User.objects.get(username="admin")
    other = User.objects.create_user(username="other", is_active=True)
    request = RequestFactory().get("/admin/users/user/")
    request.user = admin_user
    model_admin = UserAdmin(User, AdminSite())

    assert list(model_admin.get_queryset(request)) == [admin_user]
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_delete_permission(request, admin_user)
    assert model_admin.has_change_permission(request, admin_user)
    assert not model_admin.has_change_permission(request, other)

    binding_admin = SiteAdministratorAdmin(SiteAdministrator, AdminSite())
    assert not binding_admin.has_add_permission(request)
    assert not binding_admin.has_delete_permission(request)
    assert not binding_admin.has_change_permission(request)
