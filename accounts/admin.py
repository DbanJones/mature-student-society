from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, WaitlistRequest, WhatsAppAccessRequest


@admin.register(User)
class PortalUserAdmin(UserAdmin):
    list_display = ("username", "crsid", "get_full_name", "college", "account_type",
                    "is_portal_admin", "is_banned")
    list_filter = ("account_type", "is_portal_admin", "is_banned", "college")
    search_fields = ("username", "crsid", "first_name", "last_name", "email")
    fieldsets = UserAdmin.fieldsets + (
        ("Society", {"fields": ("account_type", "crsid", "college", "mobile", "photo",
                                 "is_portal_admin", "is_banned", "whatsapp_link_viewed_at")}),
    )


@admin.register(WaitlistRequest)
class WaitlistRequestAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "status", "created_at", "reviewed_by")
    list_filter = ("status",)


@admin.register(WhatsAppAccessRequest)
class WhatsAppAccessRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "created_at", "handled_by")
    list_filter = ("status",)
