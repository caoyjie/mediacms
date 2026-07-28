from django.urls import path

from .views import ExternalMediaDetailView, ExternalMediaView, PrivateLoginView, SessionUserView


urlpatterns = [
    path("auth/login/", PrivateLoginView.as_view(), name="external-auth-login"),
    path("session-user/", SessionUserView.as_view(), name="external-session-user"),
    path("external-media/", ExternalMediaView.as_view(), name="external-media"),
    path("external-media/<str:backend_media_id>/", ExternalMediaDetailView.as_view(), name="external-media-detail"),
]
