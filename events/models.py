from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Category(models.Model):
    """Event category, e.g. History Club, Supper Club, Reading Club."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    color = models.CharField(
        max_length=7, default="#4a5d78",
        help_text="Hex colour used for calendar chips, e.g. #7a3b2e.",
    )
    emoji = models.CharField(max_length=8, blank=True)
    description = models.CharField(max_length=200, blank=True)
    has_restaurant_ratings = models.BooleanField(
        default=False,
        help_text="Supper Club: events in this category link to a restaurant "
                  "and attendees can rate it afterwards.",
    )
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class EventQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Members see everything; the public only sees non-hidden events.

        ``user`` may be None (treated as anonymous), so this is safe to call
        from aggregation helpers that don't always have a request user.
        """
        qs = self.filter(is_cancelled=False)
        if user is not None and user.is_authenticated:
            return qs
        return qs.filter(members_only=False)

    def upcoming(self):
        return self.filter(start__gte=timezone.now()).order_by("start")

    def in_next_days(self, days=14):
        now = timezone.now()
        return self.filter(
            start__gte=now, start__lte=now + timezone.timedelta(days=days)
        ).order_by("-is_official", "start")


class Event(models.Model):
    title = models.CharField(max_length=140)
    description = models.TextField(
        blank=True, help_text="Markdown supported (links, lists, emphasis)."
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="events")
    location = models.CharField(max_length=200, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="events_created"
    )
    is_official = models.BooleanField(
        default=False,
        help_text="Official society events are prioritised in listings and the mailer. "
                  "Only admins can set this.",
    )
    members_only = models.BooleanField(
        default=False,
        help_text="Hide this event from the public calendar; only visible when logged in.",
    )
    capacity = models.PositiveIntegerField(
        null=True, blank=True, help_text="Leave blank for unlimited."
    )
    is_cancelled = models.BooleanField(default=False)

    # Supper Club: which restaurant this outing is to (enables ratings).
    restaurant = models.ForeignKey(
        "supper.Restaurant", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="visits",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return f"{self.title} ({self.start:%d %b %Y})"

    def get_absolute_url(self):
        return reverse("events:detail", args=[self.pk])

    # --- attendance -----------------------------------------------------------

    @property
    def going(self):
        return self.rsvps.filter(status=RSVP.Status.GOING).select_related("user")

    @property
    def going_count(self):
        return self.rsvps.filter(status=RSVP.Status.GOING).count()

    @property
    def is_full(self):
        return self.capacity is not None and self.going_count >= self.capacity

    def user_rsvp(self, user):
        if not user.is_authenticated:
            return None
        return self.rsvps.filter(user=user).first()

    def can_edit(self, user):
        return user.is_authenticated and (
            user == self.created_by or user.is_portal_admin
        )

    @property
    def is_past(self):
        reference = self.end or self.start
        return reference < timezone.now()

    @property
    def has_started(self):
        return self.start <= timezone.now()

    # --- sharing ---------------------------------------------------------------

    def whatsapp_share_text(self, absolute_url=""):
        bits = [
            f"{self.category.emoji} {self.title}".strip(),
            f"📅 {timezone.localtime(self.start):%a %d %b, %H:%M}",
        ]
        if self.location:
            bits.append(f"📍 {self.location}")
        if self.is_official:
            bits.insert(0, "⭐ Official MatureSoc event")
        if absolute_url:
            bits.append(absolute_url)
        return "\n".join(bits)

    def whatsapp_share_url(self, absolute_url=""):
        return "https://wa.me/?text=" + quote(self.whatsapp_share_text(absolute_url))


class RSVP(models.Model):
    class Status(models.TextChoices):
        GOING = "going", "Going"
        CANCELLED = "cancelled", "Not going any more"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rsvps")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rsvps"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.GOING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("event", "user")]
        ordering = ["created_at"]
        verbose_name = "RSVP"
        verbose_name_plural = "RSVPs"

    def __str__(self):
        return f"{self.user} → {self.event} [{self.status}]"
