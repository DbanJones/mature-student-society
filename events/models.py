from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class Category(models.Model):
    """An event tag, e.g. History Club, Supper Club, Coffee Club.

    Presented as a "tag" throughout the UI. Tags can have owners (the people
    who run that club): owners may mark their events as official and edit the
    tag's own subpage.
    """

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    color = models.CharField(
        max_length=7, default="#4a5d78",
        help_text="Hex colour used for calendar chips, e.g. #7a3b2e.",
    )
    emoji = models.CharField(max_length=8, blank=True)
    description = models.CharField(max_length=200, blank=True)
    page_content = models.TextField(
        blank=True,
        help_text="The tag's own page — Markdown and basic HTML supported. "
                  "Editable by the tag's owners and society admins.",
    )
    owners = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="tags_owned",
        help_text="Members who run this club: they manage the tag's events "
                  "from the panel (promoting them to tagged events), and "
                  "edit the tag's page. Every tag should have at least one.",
    )
    has_restaurant_ratings = models.BooleanField(
        default=False,
        help_text="Supper Club: events in this category link to a restaurant "
                  "and attendees can rate it afterwards.",
    )
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "tag"
        verbose_name_plural = "tags"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("events:tag_page", args=[self.slug])

    def is_owned_by(self, user):
        return (
            user is not None
            and user.is_authenticated
            and self.owners.filter(pk=user.pk).exists()
        )

    def can_edit_page(self, user):
        return (
            user is not None
            and user.is_authenticated
            and (user.is_portal_admin or self.is_owned_by(user))
        )


class EventQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Members see everything; the public only sees non-hidden events.

        ``user`` may be None (treated as anonymous), so this is safe to call
        from aggregation helpers that don't always have a request user.

        Shadow-banned members' events are invisible to everyone except the
        member themselves and admins — the member sees the site normally.
        """
        qs = self.filter(is_cancelled=False)
        if user is None or not user.is_authenticated:
            return qs.filter(members_only=False, created_by__is_shadow_banned=False)
        if user.is_portal_admin:
            return qs
        return qs.filter(
            Q(created_by__is_shadow_banned=False) | Q(created_by=user)
        )

    def search(self, q):
        """Free-text search over title, description, location and tag name."""
        q = (q or "").strip()
        if not q:
            return self
        return self.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(location__icontains=q)
            | Q(category__name__icontains=q)
        )

    def upcoming(self):
        return self.filter(start__gte=timezone.now()).order_by("start")

    def by_promotion(self):
        """The site-wide listing order: super events, then tagged (official)
        events, then everything else — each group soonest-first."""
        return self.order_by("-is_super", "-is_official", "start")

    def in_next_days(self, days=14):
        now = timezone.now()
        return self.filter(
            start__gte=now, start__lte=now + timezone.timedelta(days=days)
        ).by_promotion()


class Event(models.Model):
    title = models.CharField(max_length=140)
    slug = models.SlugField(
        max_length=180, unique=True, blank=True,
        help_text="Set automatically from the title and date, e.g. "
                  "winter-ball-12-dec-2026.",
    )
    description = models.TextField(
        blank=True, help_text="Markdown supported (links, lists, emphasis)."
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="events")
    location = models.CharField(max_length=200, blank=True)
    image = models.ImageField(
        upload_to="events/", blank=True,
        help_text="Optional picture shown on the event page.",
    )
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="events_created"
    )
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="events_hosted",
        help_text="Who is running the event. Defaults to whoever created it.",
    )
    group_chat_link = models.URLField(
        blank=True,
        help_text="Optional WhatsApp group-chat link, shown only to people "
                  "who have RSVP'd.",
    )
    attendee_info = models.TextField(
        blank=True,
        help_text="Details for attendees (meeting point, what to bring…) — "
                  "shown only to people who have RSVP'd. Markdown supported.",
    )
    is_super = models.BooleanField(
        default=False,
        help_text="Super event: a society headline. Shown larger on the "
                  "calendar, pinned to every member's dashboard, and listed "
                  "first everywhere. Only super admins can set this.",
    )
    is_official = models.BooleanField(
        default=False,
        help_text="Tagged event: promoted by the owners of its tag (or a "
                  "society admin), e.g. an official Supper Club outing. "
                  "Ranks above ordinary member events, below super events.",
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

    # Slugs that would shadow event URL patterns.
    RESERVED_SLUGS = {"new", "tags"}

    def build_slug(self):
        """'Winter Ball' on 12 Dec 2026 → 'winter-ball-12-dec-2026' (de-duped)."""
        # Format the day without a leading zero portably: the strftime "%-d"
        # flag is glibc-only and raises on Windows, so build it from .day.
        local_start = timezone.localtime(self.start)
        date_part = f"{local_start.day}-{local_start:%b-%Y}".lower()
        base = slugify(f"{self.title} {date_part}")[:170] or f"event-{date_part}"
        slug, n = base, 2
        while slug in self.RESERVED_SLUGS or (
            Event.objects.filter(slug=slug).exclude(pk=self.pk).exists()
        ):
            slug = f"{base}-{n}"
            n += 1
        return slug

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.build_slug()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"slug"}
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("events:detail", args=[self.slug])

    @property
    def effective_host(self):
        return self.host or self.created_by

    @property
    def map_embed_url(self):
        """Google Maps embed for the location — no API key required."""
        if not self.location:
            return ""
        return "https://www.google.com/maps?" + urlencode(
            {"q": f"{self.location}, Cambridge, UK", "output": "embed"}
        )

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

    @property
    def waiting(self):
        """The waitlist in the order people joined it."""
        return (
            self.rsvps.filter(status=RSVP.Status.WAITING)
            .select_related("user").order_by("updated_at")
        )

    @property
    def waiting_count(self):
        return self.rsvps.filter(status=RSVP.Status.WAITING).count()

    def promote_waitlist(self):
        """Move people off the waitlist while there is room. Returns the
        RSVPs promoted; the caller tells them."""
        promoted = []
        if self.capacity is None:
            waiters = list(self.waiting)
        else:
            room = self.capacity - self.going_count
            waiters = list(self.waiting[:max(0, room)])
        for rsvp in waiters:
            rsvp.status = RSVP.Status.GOING
            rsvp.save(update_fields=["status", "updated_at"])
            promoted.append(rsvp)
        return promoted

    def user_rsvp(self, user):
        if not user.is_authenticated:
            return None
        return self.rsvps.filter(user=user).first()

    def can_edit(self, user):
        """Creator, host, society admins — and the owners of the event's tag,
        who manage all their tag's events from the panel."""
        return user.is_authenticated and (
            user == self.created_by
            or user == self.host
            or user.is_portal_admin
            or self.category.is_owned_by(user)
        )

    def can_promote(self, user):
        """May ``user`` toggle this event's tagged (official) status?

        Society admins may promote anything; a tag's owners may promote the
        events that carry their tag. Super status is NOT covered here — that
        is checked against ``is_super_admin`` alone.
        """
        return user.is_authenticated and (
            user.is_portal_admin or self.category.is_owned_by(user)
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
        if self.is_super:
            bits.insert(0, "🌟 MSS super event")
        elif self.is_official:
            bits.insert(0, f"⭐ Official {self.category.name} event")
        if absolute_url:
            bits.append(absolute_url)
        return "\n".join(bits)

    def whatsapp_share_url(self, absolute_url=""):
        return "https://wa.me/?text=" + quote(self.whatsapp_share_text(absolute_url))


class RSVP(models.Model):
    class Status(models.TextChoices):
        GOING = "going", "Going"
        WAITING = "waiting", "On the waitlist"
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
