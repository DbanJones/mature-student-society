from django.conf import settings
from django.db import models


class MailLog(models.Model):
    """Record of every What's On mailer sent from the admin panel."""

    subject = models.CharField(max_length=200)
    body = models.TextField()
    recipients = models.CharField(max_length=200)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="mailers_sent",
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    ok = models.BooleanField(default=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.subject} → {self.recipients} @ {self.sent_at:%Y-%m-%d %H:%M}"


class AuditLog(models.Model):
    """Trail of admin actions: approvals, bans, promotions, deletions."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=200, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor} {self.action} {self.target}"

    @classmethod
    def record(cls, actor, action, target="", detail=""):
        cls.objects.create(actor=actor, action=action, target=str(target), detail=detail)
