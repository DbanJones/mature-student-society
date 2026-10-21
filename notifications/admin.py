from django.contrib import admin

from .models import Notification, NotificationEmail


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["recipient", "kind", "text", "created_at", "read_at"]
    list_filter = ["kind"]


@admin.register(NotificationEmail)
class NotificationEmailAdmin(admin.ModelAdmin):
    list_display = ["subject", "recipient", "status", "attempts", "created_at", "sent_at"]
    list_filter = ["status"]
    readonly_fields = [field.name for field in NotificationEmail._meta.fields]
    actions = ["retry_delivery"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Retry selected failed emails", permissions=["change"])
    def retry_delivery(self, request, queryset):
        from .delivery import retry_failed
        count = retry_failed(queryset)
        self.message_user(request, f"{count} emails queued for another attempt.")
