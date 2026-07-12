from django.contrib import admin

from .models import DirectMessage, MessageBlock


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    list_display = ["sender", "recipient", "created_at", "read_at", "removed_at"]
    list_filter = ["removed_at"]
    search_fields = ["sender__username", "recipient__username", "body"]


@admin.register(MessageBlock)
class MessageBlockAdmin(admin.ModelAdmin):
    list_display = ["user", "blocked", "created_at"]
