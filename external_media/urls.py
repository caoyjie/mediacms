from django.urls import path

from .views import (
    CurrentProfileView,
    ExternalMediaDetailView,
    ExternalMediaView,
    IdentityValidationView,
    PrivateLoginView,
    ProfileLogoView,
    ProfilePasswordView,
)


urlpatterns = [
    path("auth/login/", PrivateLoginView.as_view(), name="external-auth-login"),
    path(
        "identity/validate/",
        IdentityValidationView.as_view(),
        name="external-identity-validate",
    ),
    path("profile/", CurrentProfileView.as_view(), name="external-profile"),
    path(
        "profile/logo/",
        ProfileLogoView.as_view(),
        name="external-profile-logo",
    ),
    path(
        "profile/password/",
        ProfilePasswordView.as_view(),
        name="external-profile-password",
    ),
    path("external-media/", ExternalMediaView.as_view(), name="external-media"),
    path("external-media/<str:backend_media_id>/", ExternalMediaDetailView.as_view(), name="external-media-detail"),
]
