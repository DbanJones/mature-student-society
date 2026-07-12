from django.contrib import admin

from .models import Rating, Restaurant


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ("name", "cuisine", "area", "added_by")
    search_fields = ("name", "cuisine", "area")


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ("event", "user", "food", "service", "atmosphere", "value", "created_at")
