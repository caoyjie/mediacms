from django.urls import path

from .views import PrivateLoginView, SessionUserView


urlpatterns = [
    path("auth/login/", PrivateLoginView.as_view(), name="external-auth-login"),
    path("session-user/", SessionUserView.as_view(), name="external-session-user"),
]
