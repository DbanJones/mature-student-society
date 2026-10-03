"""Member-to-member direct messages.

Moderation model (the "strong admin controls"):
- every message is stored server-side and admins can review recent traffic
  in the panel (this is disclosed in the UI);
- admins can remove individual messages (soft-delete: the thread shows a
  tombstone), mute members, and switch member-to-member messaging on or off
  (``User.messaging`` and ``SiteConfig.messaging_mode``; see ``policy.py``);
- members can block each other; blocked pairs cannot exchange messages;
- shadow-banned members' messages are delivered only to themselves —
  recipients never see them;
- a daily per-sender cap stops flooding.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

MAX_MESSAGES_PER_DAY = 50
MAX_MESSAGE_LENGTH = 2000


class DirectMessageQuerySet(models.QuerySet):
    def between(self, a, b):
        return self.filter(
            Q(sender=a, recipient=b) | Q(sender=b, recipient=a)
        )

    def visible_to(self, user):
        """Hide shadow-banned senders' messages from everyone but themselves.

        Removed messages stay in the queryset — threads render a tombstone so
        conversations keep their shape.
        """
        return self.filter(Q(sender__is_shadow_banned=False) | Q(sender=user))


class DirectMessage(models.Model):
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="messages_sent"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="messages_received",
    )
    body = models.TextField(max_length=MAX_MESSAGE_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    # Admin moderation (soft delete).
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="messages_removed",
    )

    objects = DirectMessageQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["recipient", "read_at"]),
            models.Index(fields=["sender", "created_at"]),
        ]

    def __str__(self):
        return f"{self.sender} → {self.recipient} @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def is_removed(self):
        return self.removed_at is not None

    def remove(self, admin):
        self.removed_at = timezone.now()
        self.removed_by = admin
        self.save(update_fields=["removed_at", "removed_by"])

    @classmethod
    def unread_count_for(cls, user):
        return (
            cls.objects.filter(recipient=user, read_at__isnull=True,
                               removed_at__isnull=True)
            .visible_to(user)
            .count()
        )

    @classmethod
    def sender_is_over_daily_cap(cls, user):
        since = timezone.now() - timezone.timedelta(days=1)
        return (
            cls.objects.filter(sender=user, created_at__gte=since).count()
            >= MAX_MESSAGES_PER_DAY
        )


class MessageBlock(models.Model):
    """``user`` no longer wants to hear from ``blocked`` (and vice versa:
    a block stops messages in both directions)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_made"
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="blocks_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "blocked")]

    def __str__(self):
        return f"{self.user} blocked {self.blocked}"

    @classmethod
    def exists_between(cls, a, b):
        return cls.objects.filter(
            Q(user=a, blocked=b) | Q(user=b, blocked=a)
        ).exists()
