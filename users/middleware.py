from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect

from users.models import SiteAdministrator


class SiteAdministratorGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "MEDIACMS_SINGLE_ADMIN_MODE", False):
            return self.get_response(request)
        if not request.user.is_authenticated or self._is_exempt(request.path):
            return self.get_response(request)

        binding = SiteAdministrator.get_solo()
        if binding is None or not SiteAdministrator.is_site_administrator(binding.user):
            return JsonResponse({"code": "site_administrator_unavailable"}, status=503)
        if request.user.pk == binding.user_id:
            return self.get_response(request)
        if request.path.startswith(("/api/", "/internal/api/")):
            return JsonResponse({"code": "single_administrator_required"}, status=403)

        logout(request)
        return redirect(settings.LOGIN_URL)

    @staticmethod
    def _is_exempt(path):
        prefixes = (
            settings.STATIC_URL,
            settings.MEDIA_URL,
            "/accounts/login",
            "/accounts/logout",
            "/health",
        )
        return any(path.startswith(prefix) for prefix in prefixes if prefix)
