from django.contrib import admin

from .models import Testimonial


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = [
        "author_name", "is_anonymous", "status", "is_featured",
        "submitted_at", "reviewed_by",
    ]
    list_filter = ["status", "is_anonymous", "is_featured"]
    search_fields = ["author_name", "body"]
