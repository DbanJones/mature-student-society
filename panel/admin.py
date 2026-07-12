from django.contrib import admin

from .models import AuditLog, MailLog


@admin.register(MailLog)
class MailLogAdmin(admin.ModelAdmin):
    list_display = ("subject", "recipients", "sent_by", "sent_at", "ok")
    readonly_fields = ("subject", "body", "recipients", "sent_by", "sent_at", "ok", "error")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("actor", "action", "target", "created_at")
    list_filter = ("action",)
