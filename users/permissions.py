from rest_framework.permissions import BasePermission

from users.models import SiteAdministrator


class IsSiteAdministrator(BasePermission):
    message = "The singleton site administrator is required."

    def has_permission(self, request, view):
        return SiteAdministrator.is_site_administrator(request.user)
