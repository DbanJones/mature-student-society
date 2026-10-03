"""Event posters.

The poster itself is never stored: the server lays it out as SVG from the
event's fields and the browser renders, prints and exports it. What the
SRCF keeps is one small settings row per event (the organiser's choices) and
a count of QR scans.
"""

from django.conf import settings
from django.db import models


class EventPoster(models.Model):
    class Template(models.TextChoices):
        CLASSIC = "classic", "Classic"
        BOLD = "bold", "Bold"
        PHOTO = "photo", "Photo"

    class Size(models.TextChoices):
        A4 = "a4", "A4"
        A3 = "a3", "A3"
        SQUARE = "square", "Square"
        STORY = "story", "Story"

    class Accent(models.TextChoices):
        TAG = "tag", "The event's tag colour"
        PINE = "pine", "Pine"
        BRICK = "brick", "Brick"
        GOLD = "gold", "Gold"

    event = models.OneToOneField(
        "events.Event", on_delete=models.CASCADE, related_name="poster"
    )
    template = models.CharField(max_length=8, choices=Template.choices, default=Template.CLASSIC)
    size = models.CharField(max_length=8, choices=Size.choices, default=Size.A4)
    accent = models.CharField(max_length=6, choices=Accent.choices, default=Accent.TAG)
    use_event_picture = models.BooleanField(
        default=True, help_text="Untick to leave the picture out (a colour scene is used instead).",
    )
    focal_x = models.FloatField(default=0.5)
    focal_y = models.FloatField(default=0.5)
    headline = models.CharField(
        max_length=140, blank=True, help_text="Leave blank to use the event title.",
    )
    extra_line = models.CharField(
        max_length=60, blank=True,
        help_text="One line of your own, e.g. “£5 on the door” or “bring a friend”.",
    )
    show_map = models.BooleanField(default=True)
    show_qr = models.BooleanField(default=True)
    show_description = models.BooleanField(default=True)
    show_host = models.BooleanField(default=True)
    print_marks = models.BooleanField(
        default=False, help_text="Add 3 mm bleed and crop marks for a print shop.",
    )
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="posters_saved",
    )
    saved_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Poster for {self.event}"

    @classmethod
    def for_event(cls, event):
        """The saved settings, or an unsaved default row."""
        try:
            return event.poster
        except cls.DoesNotExist:
            return cls(event=event)

    # Fields a member may override for their own download via the query
    # string, without touching the saved row.
    OVERRIDABLE = [
        "template", "size", "accent", "use_event_picture", "headline",
        "extra_line", "show_map", "show_qr", "show_description", "show_host",
        "print_marks", "focal_x", "focal_y",
    ]


class PosterScan(models.Model):
    """One scan of a poster's QR code: the short link /p/<slug>/ records it
    and sends the phone on to the event page."""

    class Device(models.TextChoices):
        PHONE = "phone", "Phone"
        TABLET = "tablet", "Tablet"
        DESKTOP = "desktop", "Desktop"
        OTHER = "other", "Other"

    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, related_name="poster_scans"
    )
    scanned_at = models.DateTimeField(auto_now_add=True)
    device = models.CharField(max_length=8, choices=Device.choices, default=Device.OTHER)

    class Meta:
        ordering = ["-scanned_at"]

    def __str__(self):
        return f"Scan of {self.event} @ {self.scanned_at:%Y-%m-%d %H:%M}"

    @classmethod
    def device_from(cls, user_agent):
        ua = (user_agent or "").lower()
        if "ipad" in ua or "tablet" in ua:
            return cls.Device.TABLET
        if "mobile" in ua or "iphone" in ua or "android" in ua:
            return cls.Device.PHONE
        if "windows" in ua or "macintosh" in ua or "linux" in ua:
            return cls.Device.DESKTOP
        return cls.Device.OTHER
