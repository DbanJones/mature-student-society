from django.conf import settings
from django.db import models
from django.db.models import Avg

# The rating dimensions every Supper Club restaurant is scored on (1-5 stars).
RATING_DIMENSIONS = [
    ("food", "Food"),
    ("service", "Service"),
    ("atmosphere", "Atmosphere"),
    ("value", "Value for money"),
]


class Restaurant(models.Model):
    name = models.CharField(max_length=120, unique=True)
    cuisine = models.CharField(max_length=60, blank=True)
    area = models.CharField(max_length=80, blank=True, help_text="e.g. Mill Road, city centre")
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="restaurants_added",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    # --- aggregates -------------------------------------------------------------

    def rating_summary(self, viewer=None):
        """Average per dimension plus overall, across visits visible to ``viewer``.

        Ratings from members-only (and cancelled) visits must not surface to
        the public, so aggregation runs over ``Event.objects.visible_to(viewer)``
        — the same visibility gate used for the visit and rating lists. Passing
        ``viewer=None`` (or an anonymous user) yields the public-safe figures.
        """
        from events.models import Event  # avoid an import cycle at module load

        visible_events = Event.objects.visible_to(viewer).filter(restaurant=self)
        ratings = Rating.objects.filter(event__in=visible_events)
        if not ratings.exists():
            return None
        aggregates = ratings.aggregate(
            **{key: Avg(key) for key, _ in RATING_DIMENSIONS}
        )
        dimensions = [
            {"key": key, "label": label, "avg": aggregates[key]}
            for key, label in RATING_DIMENSIONS
        ]
        overall = sum(d["avg"] for d in dimensions) / len(dimensions)
        return {
            "dimensions": dimensions,
            "overall": overall,
            "count": ratings.count(),
            "visits": ratings.values("event").distinct().count(),
        }


class Rating(models.Model):
    """One member's scores for one Supper Club visit.

    Only members who RSVP'd 'going' to the visit may rate it (enforced in views).
    """

    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, related_name="restaurant_ratings"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="restaurant_ratings"
    )
    food = models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])
    service = models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])
    atmosphere = models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])
    value = models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])
    comment = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("event", "user")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} rated {self.event} ({self.overall:.1f}★)"

    @property
    def overall(self):
        return (self.food + self.service + self.atmosphere + self.value) / 4
