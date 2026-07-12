from django.conf import settings
from django.db import models
from django.urls import reverse

SECTIONS = [
    ("arriving", "Before you arrive"),
    ("colleges", "Colleges for mature students"),
    ("study", "Study & academic life"),
    ("money", "Money & funding"),
    ("family", "Family, partners & children"),
    ("living", "Living in Cambridge"),
    ("social", "Social life & societies"),
    ("faq", "FAQs"),
]


class GuidePage(models.Model):
    """A page of the Mature Students Guide — the community wiki.

    Publicly readable; editable by any logged-in member. Every save creates a
    GuideRevision so edits can be reviewed and reverted.
    """

    title = models.CharField(max_length=140)
    slug = models.SlugField(max_length=150, unique=True)
    section = models.CharField(max_length=20, choices=SECTIONS, default="living")
    content = models.TextField(
        help_text="Markdown supported: headings, links, lists, emphasis."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="guide_pages_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="guide_pages_edited",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["section", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("guide:page", args=[self.slug])

    def save_revision(self, editor):
        GuideRevision.objects.create(
            page=self, title=self.title, content=self.content, editor=editor
        )


class GuideRevision(models.Model):
    page = models.ForeignKey(GuidePage, on_delete=models.CASCADE, related_name="revisions")
    title = models.CharField(max_length=140)
    content = models.TextField()
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="guide_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.page.title} @ {self.created_at:%Y-%m-%d %H:%M} by {self.editor}"
