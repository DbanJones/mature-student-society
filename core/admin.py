from django.contrib import admin

from .models import SiteConfig, TermsAcceptance, TermsRevision, TermsVersion


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    exclude = ("about_text",)  # the About introduction is a text block on the Pages tab now

    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()


@admin.register(TermsVersion)
class TermsVersionAdmin(admin.ModelAdmin):
    list_display = ["number", "title", "is_published", "published_at", "updated_by"]
    list_filter = ["is_published"]


@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(admin.ModelAdmin):
    """Read-only in dj-admin: acceptance rows are legal evidence, written only
    by the acceptance flow itself."""

    list_display = ["user", "version_number", "accepted_at", "source", "ip_address"]
    list_filter = ["version_number", "source"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TermsRevision)
class TermsRevisionAdmin(admin.ModelAdmin):
    """Read-only: the who-changed-what trail must not be editable."""

    list_display = ["version_number", "action", "editor", "created_at"]
    list_filter = ["action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
