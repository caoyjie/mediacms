from django.urls import path

from .views import (
    ExternalMediaDetailView,
    ExternalMediaView,
    IdentityValidationView,
    PrivateLoginView,
)


urlpatterns = [
    path("auth/login/", PrivateLoginView.as_view(), name="external-auth-login"),
    path(
        "identity/validate/",
        IdentityValidationView.as_view(),
        name="external-identity-validate",
    ),
    path("external-media/", ExternalMediaView.as_view(), name="external-media"),
    path("external-media/<str:backend_media_id>/", ExternalMediaDetailView.as_view(), name="external-media-detail"),
]
