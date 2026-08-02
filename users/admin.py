from django.conf import settings
from django.contrib import admin

from .models import SiteAdministrator, User


class UserAdmin(admin.ModelAdmin):
    search_fields = ["email", "username", "name"]
    exclude = ["user_permissions", "title", "password", "groups", "last_login", "is_featured", "location", "first_name", "last_name", "media_count", "date_joined", "is_active", "is_approved"]
    list_display = [
        "username",
        "name",
        "email",
        "logo",
        "date_added",
        "is_superuser",
        "is_editor",
        "is_manager",
        "media_count",
    ]
    list_filter = ["is_superuser", "is_editor", "is_manager"]
    ordering = ("-date_added",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        binding = SiteAdministrator.get_solo()
        if binding is None:
            return queryset.none()
        return queryset.filter(pk=binding.user_id)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        binding = SiteAdministrator.get_solo()
        if binding is None:
            return False
        return obj is None or obj.pk == binding.user_id

    if settings.USERS_NEEDS_TO_BE_APPROVED:
        list_display.append("is_approved")
        list_filter.append("is_approved")
        exclude.remove("is_approved")


admin.site.register(User, UserAdmin)


@admin.register(SiteAdministrator)
class SiteAdministratorAdmin(admin.ModelAdmin):
    list_display = ("singleton_key", "user", "created_at", "updated_at")
    readonly_fields = ("singleton_key", "user", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
