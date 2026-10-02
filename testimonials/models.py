"""Member testimonials: a few words about MSS, shown publicly once approved.

Members submit; the committee approves. An anonymous testimonial hides the
author from the public but not from the committee, who need to know who
wrote what in order to moderate (the form says so). The author's name and
college are snapshotted at submission so the quote still reads correctly if
the account is later renamed or deleted.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

MAX_LENGTH = 600
MAX_PENDING_PER_MEMBER = 1
ANONYMOUS_NAME = "An MSS member"


class TestimonialQuerySet(models.QuerySet):
    def approved(self):
        return self.filter(status=Testimonial.Status.APPROVED)

    def pending(self):
        return self.filter(status=Testimonial.Status.PENDING)

    def featured_first(self):
        return self.order_by("-is_featured", "-reviewed_at", "-submitted_at")


class Testimonial(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting review"
        APPROVED = "approved", "Published"
        REJECTED = "rejected", "Not published"

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="testimonials",
    )
    author_name = models.CharField(max_length=120, blank=True)
    author_college = models.CharField(max_length=60, blank=True)
    is_anonymous = models.BooleanField(
        default=False,
        help_text="Shown publicly as “An MSS member”. The committee can still "
                  "see who wrote it.",
    )
    body = models.TextField(max_length=MAX_LENGTH)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    is_featured = models.BooleanField(
        default=False,
        help_text="Featured testimonials lead the page and appear on the "
                  "homepage.",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="testimonials_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(
        max_length=200, blank=True,
        help_text="Shown to the member, e.g. why it wasn't published.",
    )

    objects = TestimonialQuerySet.as_manager()

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.display_name}: {self.body[:40]} [{self.status}]"

    @property
    def display_name(self):
        if self.is_anonymous:
            return ANONYMOUS_NAME
        return self.author_name or "A former member"

    @property
    def display_detail(self):
        return "" if self.is_anonymous else self.author_college

    def review(self, admin, status, note=""):
        self.status = status
        self.reviewed_by = admin
        self.reviewed_at = timezone.now()
        self.review_note = note
        if status != self.Status.APPROVED:
            self.is_featured = False
        self.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_note", "is_featured",
        ])
