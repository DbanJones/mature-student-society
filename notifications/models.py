"""In-app notifications: short, linkable, and marked read when followed.

Anything that changes a member's plans lands here (an event they're going
to is cancelled or moved, a poll opens or decides, they come off a waitlist,
their testimonial is reviewed, a page they edit is changed by someone else).
The important ones are emailed too; see ``services.notify``.
"""

from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Kind(models.TextChoices):
        EVENT = "event", "Event"
        POLL = "poll", "Poll"
        WAITLIST = "waitlist", "Waitlist"
        TESTIMONIAL = "testimonial", "Testimonial"
        PAGE = "page", "Page"
        GENERAL = "general", "General"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.GENERAL)
    text = models.CharField(max_length=200)
    url = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "read_at"])]

    def __str__(self):
        return f"{self.recipient}: {self.text}"

    @property
    def is_read(self):
        return self.read_at is not None

    @classmethod
    def unread_count_for(cls, user):
        return cls.objects.filter(recipient=user, read_at__isnull=True).count()

    EMOJI = {
        "event": "📅", "poll": "📊", "waitlist": "🎟️",
        "testimonial": "🗣️", "page": "📝", "general": "🔔",
    }

    @property
    def emoji(self):
        return self.EMOJI.get(self.kind, "🔔")
