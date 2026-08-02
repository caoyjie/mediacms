from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from ..services.cloudfront_auth import clear_cookie_headers, issue_signed_cookies, set_cookie_headers


@require_GET
def bootstrap(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"detail": "Authentication required."}, status=403)
    try:
        cookies = issue_signed_cookies()
    except RuntimeError:
        return JsonResponse({"detail": "Media authorization is not configured."}, status=503)
    return set_cookie_headers(JsonResponse({"expires_at": cookies["expires_at"]}), cookies)


@require_POST
def logout(request):
    return clear_cookie_headers(JsonResponse({"ok": True}))
