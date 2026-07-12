from django.conf import settings
from django.db import models

# Visibility levels for nav tabs and CMS pages.
VISIBILITY_CHOICES = [
    ("public", "Everyone (including logged-out visitors)"),
    ("members", "Members only"),
    ("admins", "Admins only"),
    ("hidden", "Hidden from the navigation"),
]

# The built-in tabs admins may show/hide (the calendar, the Guide, the
# dashboard and the admin tab itself are always available).
BUILTIN_TABS = [
    ("supper", "Supper Club", "public"),
    ("ball", "Winter Ball", "public"),
    ("about", "About", "public"),
    ("members", "Members directory", "members"),
    ("messages", "Messages", "members"),
]


def visible_to(visibility, user):
    if visibility == "public":
        return True
    if visibility == "members":
        return user.is_authenticated
    if visibility == "admins":
        return user.is_authenticated and user.is_portal_admin
    return False


class SiteConfig(models.Model):
    """Singleton for society-wide settings, editable in the admin panel."""

    society_name = models.CharField(
        max_length=120, default="University of Cambridge Mature Student Society"
    )
    short_name = models.CharField(max_length=40, default="MSS")
    tagline = models.CharField(
        max_length=200,
        default="Welcoming Cambridge's mature students.",
    )
    about_text = models.TextField(blank=True, help_text="Markdown; shown on the public About page.")
    contact_email = models.EmailField(default="maturesoc@cambridgesu.co.uk")
    mailing_list_address = models.EmailField(
        default="soc-mss-members@srcf.net",
        help_text="Where the What's On mailer is sent (SRCF Mailman list).",
    )
    whatsapp_group_link = models.URLField(
        blank=True,
        help_text="The group invite link revealed once to each approved member.",
    )
    instagram_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)

    # Email sending — managed on the super admin tab.
    email_api_key = models.CharField(
        max_length=200, blank=True,
        help_text="API key for the transactional email provider. Stored "
                  "server-side and never shown in full once saved.",
    )
    email_ai_engine = models.CharField(
        max_length=20, default="deepseek",
        choices=[
            ("deepseek", "DeepSeek"),
            ("anthropic", "Anthropic (Claude)"),
            ("openai", "OpenAI (GPT)"),
            ("gemini", "Google (Gemini)"),
            ("mistral", "Mistral"),
        ],
        help_text="Which AI engine drafts society emails (uses the API key "
                  "above).",
    )
    email_tone = models.TextField(
        blank=True,
        help_text="Tone-of-voice instructions for anyone (or anything) "
                  "drafting society emails — shown alongside the mailer.",
    )

    # Which built-in nav tabs are visible to whom, keyed by tab key
    # (see BUILTIN_TABS). Managed by admins on the panel's Content tab.
    tab_visibility = models.JSONField(default=dict, blank=True)

    def tab_visibility_for(self, key):
        default = next((d for k, _, d in BUILTIN_TABS if k == key), "public")
        return self.tab_visibility.get(key, default)

    @property
    def email_api_key_hint(self):
        """Masked form of the key for display, e.g. 'sk-…3kQ9'."""
        key = self.email_api_key
        if not key:
            return ""
        return f"{key[:3]}…{key[-4:]}" if len(key) > 8 else "•" * len(key)

    class Meta:
        verbose_name = "site configuration"

    def __str__(self):
        return self.society_name

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SitePage(models.Model):
    """An admin-managed content page (everything that isn't the calendar or
    the Guide): served at /pages/<slug>/, optionally shown in the nav."""

    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    content = models.TextField(
        help_text="Markdown and basic HTML supported.",
    )
    is_published = models.BooleanField(
        default=True,
        help_text="Unpublished pages are visible to admins only.",
    )
    nav_label = models.CharField(
        max_length=30, blank=True,
        help_text="Short label for the navigation bar (blank = not in nav).",
    )
    nav_visibility = models.CharField(
        max_length=10, choices=VISIBILITY_CHOICES, default="public",
        help_text="Who sees it in the navigation (the page itself follows "
                  "'published' plus this audience).",
    )
    sort_order = models.PositiveSmallIntegerField(default=100)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="site_pages_edited",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("core:site_page", args=[self.slug])
