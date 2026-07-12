from django.contrib import admin

from .models import GuidePage, GuideRevision


@admin.register(GuidePage)
class GuidePageAdmin(admin.ModelAdmin):
    list_display = ("title", "section", "updated_by", "updated_at", "is_published")
    list_filter = ("section", "is_published")
    search_fields = ("title", "content")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(GuideRevision)
class GuideRevisionAdmin(admin.ModelAdmin):
    list_display = ("page", "editor", "created_at")
