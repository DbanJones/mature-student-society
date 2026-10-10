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


class OldSubscriber(models.Model):
    """Someone on the society's mailing list from before the website,
    imported from a spreadsheet (Admin → People → Old mailing list). The
    What's On mailer can still reach them, and the page shows who has since
    joined the website."""

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    college = models.CharField(max_length=80, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="old_subscribers_added",
    )
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def crsid(self):
        """The CRSid a cam.ac.uk address implies, or ''."""
        local, _at, domain = self.email.partition("@")
        return local if domain == "cam.ac.uk" else ""

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)
