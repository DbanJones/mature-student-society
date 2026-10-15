import os
import re
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


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
    audience = models.CharField(
        max_length=10, blank=True,
        help_text="Which audience a send went to (members, old, old_new, both, address or test).",
    )

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
        # Cut to the columns' sizes: strict MySQL would otherwise refuse the row.
        cls.objects.create(actor=actor, action=str(action)[:60], target=str(target)[:200],
                           detail=str(detail)[:300])


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

    CRSID_DOMAINS = ("cam.ac.uk", "hermes.cam.ac.uk")

    @property
    def crsid(self):
        """The CRSid a Cambridge address implies (``ab123@cam.ac.uk``), or ''.
        Only CRSid-shaped local parts count, so ``admin@cam.ac.uk`` is not one."""
        local, _at, domain = self.email.partition("@")
        if domain in self.CRSID_DOMAINS and re.fullmatch(r"[a-z]{2,6}[0-9]{1,5}", local):
            return local
        return ""

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


# --- site health ----------------------------------------------------------------------------

class HealthSnapshot(models.Model):
    """One run of the site health checks (Admin → Health),
    kept so the page can show a history and how long a problem has lasted."""

    class Trigger(models.TextChoices):
        BUTTON = "button", "Run by an admin"
        CRON = "cron", "Scheduled"
        DEPLOY = "deploy", "At a restart"
        COMMAND = "command", "From the command line"

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    trigger = models.CharField(max_length=10, choices=Trigger.choices)
    full = models.BooleanField(default=False, help_text="The network and disk checks ran too.")
    overall = models.CharField(max_length=8)
    statuses = models.JSONField(default=dict, help_text="{check key: status}, for cheap reads.")
    results = models.JSONField(default=list)
    schema = models.PositiveSmallIntegerField(default=1)
    duration_ms = models.PositiveIntegerField(default=0)
    version = models.CharField(max_length=60, blank=True)
    run_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_trigger_display()} {self.created_at:%Y-%m-%d %H:%M}: {self.overall}"


class JobHeartbeat(models.Model):
    """When a scheduled job (close_polls, health_check, a restart's
    sync_static) last ran, and how it went."""

    name = models.CharField(max_length=40, unique=True)
    last_started = models.DateTimeField(null=True, blank=True)
    last_finished = models.DateTimeField(null=True, blank=True)
    last_ok = models.BooleanField(default=False)
    last_error = models.CharField(max_length=300, blank=True)
    detail = models.CharField(max_length=200, blank=True)
    host = models.CharField(max_length=60, blank=True)

    def __str__(self):
        return self.name


class ErrorEvent(models.Model):
    """A kind of server error, grouped by where it happens. Recorded by
    panel.errorlog from Django's own error logging; holds the page's route
    (never its address, which can carry tokens) and no personal data."""

    class Kind(models.TextChoices):
        EXCEPTION = "exception", "Server error"
        SECURITY = "security", "Refused request"
        WARNING = "warning", "Warning"

    fingerprint = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.EXCEPTION)
    exc_type = models.CharField(max_length=120, blank=True)
    message = models.CharField(max_length=300, blank=True)
    route = models.CharField(max_length=200, blank=True)
    view_name = models.CharField(max_length=120, blank=True)
    method = models.CharField(max_length=10, blank=True)
    frames = models.TextField(blank=True)
    count = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    reopened = models.BooleanField(default=False, help_text="Came back after being marked fixed.")

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.exc_type or self.kind} at {self.route or '?'} ×{self.count}"


class RequestStats(models.Model):
    """Requests per hour (UTC, bucketed in Python, never by the database),
    by status and by how long they took."""

    hour = models.DateTimeField(unique=True)
    requests = models.PositiveIntegerField(default=0)
    s4xx = models.PositiveIntegerField(default=0)
    s5xx = models.PositiveIntegerField(default=0)
    under_half = models.PositiveIntegerField(default=0)
    under_1 = models.PositiveIntegerField(default=0)
    under_2 = models.PositiveIntegerField(default=0)
    under_5 = models.PositiveIntegerField(default=0)
    under_10 = models.PositiveIntegerField(default=0)
    under_20 = models.PositiveIntegerField(default=0)
    over_20 = models.PositiveIntegerField(default=0)
    monitor_hits = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-hour"]


class SlowRequest(models.Model):
    """A request that took five seconds or more."""

    at = models.DateTimeField(default=timezone.now, db_index=True)
    route = models.CharField(max_length=200, blank=True)
    view_name = models.CharField(max_length=120, blank=True)
    duration_ms = models.PositiveIntegerField()
    status = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-at"]


# --- the society's mailing list ---------------------------------------------------------------

def _old_list_file_path(instance, filename):
    """Under private/, which /media/ never serves: only the panel's download
    hands these out. A random name, so nothing about it can be guessed."""
    return f"private/old-list/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()[:10]}"


class OldListFile(models.Model):
    """A spreadsheet of the old mailing list exactly as it was uploaded, kept
    as the archive (People > Old mailing list). It holds people's details, so
    only admins can download it, and each download is in the audit log."""

    file = models.FileField(upload_to=_old_list_file_path)
    name = models.CharField(max_length=255, help_text="The file's name when it was uploaded.")
    size = models.PositiveIntegerField(default=0)
    addresses = models.PositiveIntegerField(default=0, help_text="Addresses found in it.")
    uploaded_at = models.DateTimeField(default=timezone.now)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.name


class ListMember(models.Model):
    """An address on the society's Mailman list, as far as the site knows:
    from an admin's latest paste of the list's members (Admin > People >
    Mailing list), or added since then by a download an admin marked as
    done. Mailman is the truth; this is the site's most recent copy."""

    class Source(models.TextChoices):
        LIST = "list", "Copied from the list"
        SITE = "site", "Added from the website"

    email = models.EmailField(unique=True, help_text="As on the list, lower-cased.")
    key = models.CharField(max_length=254, db_index=True,
                           help_text="The mailbox it belongs to (a Cambridge address's two spellings are one).")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.LIST)
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email


class ListSync(models.Model):
    """A change to the site's copy of the list: an update pasted from
    Mailman, or addresses downloaded to add to it or take off it, and when
    an admin said that was done."""

    class Kind(models.TextChoices):
        UPDATE = "update", "Updated from the list"
        ADD = "add", "Downloaded to add"
        REMOVE = "remove", "Downloaded to take off"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    count = models.PositiveIntegerField(default=0)
    addresses = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=200, blank=True, help_text="Why, when it wasn't a download (an account deleted).")
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.created_at:%Y-%m-%d %H:%M}: {self.count}"
