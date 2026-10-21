from django.contrib import admin
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html

from .models import Category, Event, RSVP


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "emoji", "has_restaurant_ratings", "sort_order")
    prepopulated_fields = {"slug": ("name",)}


class RSVPInline(admin.TabularInline):
    model = RSVP
    extra = 0
    readonly_fields = ("user", "status", "created_at", "updated_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "start", "created_by", "is_official",
                    "members_only", "is_cancelled")
    list_filter = ("category", "is_official", "members_only", "is_cancelled")
    search_fields = ("title", "description", "location")
    date_hierarchy = "start"
    inlines = [RSVPInline]

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return ("capacity", "is_cancelled", "start", "end", "host", "manage_event")
        return ()

    @admin.display(description="Dates, places and cancellation")
    def manage_event(self, obj):
        return format_html('<a href="{}">Edit dates and capacity</a> · <a href="{}">Open the event to manage attendance or cancel</a>', reverse("events:edit", args=[obj.slug]), obj.get_absolute_url())

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            if change:
                current = Event.objects.select_for_update().get(pk=obj.pk)
                for field in ("capacity", "is_cancelled", "start", "end", "host_id"):
                    setattr(obj, field, getattr(current, field))
            super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "category":
            kwargs["queryset"] = Category.objects.exclude(slug="formal-exchange")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_formal:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_formal:
            return False
        return super().has_delete_permission(request, obj)

    def get_inline_instances(self, request, obj=None):
        if obj is not None and obj.is_formal:
            return []
        return super().get_inline_instances(request, obj)
