from django.contrib import admin

from .models import Category, Event, RSVP


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "emoji", "has_restaurant_ratings", "sort_order")
    prepopulated_fields = {"slug": ("name",)}


class RSVPInline(admin.TabularInline):
    model = RSVP
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "start", "created_by", "is_official",
                    "members_only", "is_cancelled")
    list_filter = ("category", "is_official", "members_only", "is_cancelled")
    search_fields = ("title", "description", "location")
    date_hierarchy = "start"
    inlines = [RSVPInline]
