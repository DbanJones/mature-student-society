from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils import timezone

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
    ("testimonials", "Testimonials", "public"),
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

    class MessagingMode(models.TextChoices):
        OPEN = "open", "Open: any member can message any other member"
        RESTRICTED = (
            "restricted",
            "Restricted: members can message the committee; only members an "
            "admin has enabled can message each other",
        )

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
    geoapify_api_key = models.CharField(
        max_length=80, blank=True,
        help_text="Geoapify key for the poster map and venue look-ups. "
                  "Stored server-side; blank means posters show a directions "
                  "QR code instead of a map.",
    )
    email_tone = models.TextField(
        blank=True,
        help_text="Tone-of-voice instructions for anyone (or anything) "
                  "drafting society emails — shown alongside the mailer.",
    )

    # Cambridge term starts (the Tuesday Full Term begins), so the calendar
    # can label weeks "Michaelmas wk 3". Leave blank to show no labels.
    michaelmas_start = models.DateField(null=True, blank=True)
    lent_start = models.DateField(null=True, blank=True)
    easter_start = models.DateField(null=True, blank=True)

    def term_week_label(self, day):
        """'Michaelmas wk 3' for a date inside a term (weeks 0 to 9), else ''."""
        import datetime

        for name, start in (
            ("Michaelmas", self.michaelmas_start),
            ("Lent", self.lent_start),
            ("Easter", self.easter_start),
        ):
            if start is None:
                continue
            # Count from the Monday of the week Full Term begins.
            monday = start - datetime.timedelta(days=start.weekday())
            weeks = (day - monday).days // 7
            if 0 <= weeks <= 9:
                return f"{name} wk {weeks + 1}"
        return ""

    # Site-wide announcement, shown under the header until it expires or the
    # visitor dismisses it for their session.
    banner_text = models.TextField(
        blank=True, help_text="Markdown. Leave blank for no banner.",
    )
    banner_until = models.DateTimeField(
        null=True, blank=True, help_text="Hide the banner after this time.",
    )

    # Who may send direct messages to whom. Restricted by default: the
    # community policy already asks for no unsolicited DMs. See inbox.policy.
    messaging_mode = models.CharField(
        max_length=10, choices=MessagingMode.choices,
        default=MessagingMode.RESTRICTED,
        help_text="Admins can always message anyone, and anyone can reply "
                  "to an admin, whichever mode is chosen.",
    )

    # Which built-in nav tabs are visible to whom, keyed by tab key
    # (see BUILTIN_TABS). Managed by admins on the panel's Content tab.
    tab_visibility = models.JSONField(default=dict, blank=True)

    def tab_visibility_for(self, key):
        default = next((d for k, _, d in BUILTIN_TABS if k == key), "public")
        return self.tab_visibility.get(key, default)

    @property
    def geoapify_api_key_hint(self):
        key = self.geoapify_api_key
        if not key:
            return ""
        return f"{key[:3]}…{key[-4:]}" if len(key) > 8 else "•" * len(key)

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


class CommitteeMember(models.Model):
    """One row of the committee table on the About page."""

    name = models.CharField(max_length=80)
    role = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True, help_text="Untick when someone steps down.")

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.role})"


class Activity(models.Model):
    """One card in the homepage's "What we do" grid."""

    emoji = models.CharField(max_length=8, blank=True)
    name = models.CharField(max_length=60)
    blurb = models.CharField(max_length=160)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "activities"

    def __str__(self):
        return self.name


SECTION_CHOICES = [
    ("about", "About"),
    ("guide", "Guide"),
    ("members", "Members portal"),
]


class SitePage(models.Model):
    """An admin-managed content page (everything that isn't the calendar or
    the Guide): served at /pages/<slug>/, optionally shown in the nav.

    Admins create, publish, delete and set the audience; the members named
    as ``editors`` may change the title and body without being admins. Every
    save is snapshotted as a SitePageRevision so delegated edits can be
    reviewed and reverted.
    """

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
    section = models.CharField(
        max_length=10, choices=SECTION_CHOICES, default="about",
        help_text="Which menu the page sits under, when it has a navigation label.",
    )
    sort_order = models.PositiveSmallIntegerField(default=100)
    editors = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="site_pages_editable",
        help_text="Members who may edit this page's title and content "
                  "without being admins. Publishing, audience and navigation "
                  "stay with admins.",
    )
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

    def is_visible_to(self, user):
        """May ``user`` read this page?

        Unpublished pages are admin-only. Otherwise the audience applies to
        the page itself, not just its navigation link; "hidden" means
        link-only, readable by anyone who has the address.
        """
        if user.is_authenticated and user.is_portal_admin:
            return True
        if not self.is_published:
            return False
        if self.nav_visibility == "hidden":
            return True
        return visible_to(self.nav_visibility, user)

    def can_edit(self, user):
        return user.is_authenticated and (
            user.is_portal_admin or self.editors.filter(pk=user.pk).exists()
        )

    def save_revision(self, editor, action):
        """Snapshot the page as it stands now."""
        return SitePageRevision.objects.create(
            page=self, title=self.title, content=self.content,
            editor=editor, action=action,
        )


class SitePageRevision(models.Model):
    """A snapshot of a SitePage taken on every save.

    With editing delegated to non-admins, "who changed what, and when" has
    to be answerable, and any version restorable.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        EDITED = "edited", "Edited"
        RESTORED = "restored", "Restored"

    page = models.ForeignKey(
        SitePage, on_delete=models.CASCADE, related_name="revisions"
    )
    title = models.CharField(max_length=120)
    content = models.TextField(blank=True)
    action = models.CharField(
        max_length=10, choices=Action.choices, default=Action.EDITED
    )
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="site_page_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} {self.action} @ {self.created_at:%Y-%m-%d %H:%M}"


class TermsVersion(models.Model):
    """A version of the terms and conditions every member must accept.

    Versions are numbered. The *current* terms are the published version with
    the highest number; publishing a new one asks every member to accept again
    the next time they load a page. Editing is deliberately non-destructive —
    every save writes a TermsRevision — because the society needs to be able to
    show what a member actually agreed to on a given date.
    """

    number = models.PositiveIntegerField(
        unique=True,
        help_text="Version number. The highest published version is the one "
                  "members must accept.",
    )
    title = models.CharField(max_length=140, default="Terms and Conditions")
    content = models.TextField(
        help_text="Markdown supported: headings, links, lists, emphasis.",
    )
    change_note = models.CharField(
        max_length=250, blank=True,
        help_text="What changed in this version, for the committee's own "
                  "records, e.g. 'Added photography consent clause'.",
    )
    is_published = models.BooleanField(
        default=False,
        help_text="Unpublished drafts are visible to admins only and are "
                  "never shown to members for acceptance.",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="terms_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="terms_edited",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-number"]
        verbose_name = "terms version"
        verbose_name_plural = "terms versions"

    def __str__(self):
        state = "published" if self.is_published else "draft"
        return f"{self.title} v{self.number} ({state})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("core:terms")

    @classmethod
    def current(cls):
        """The terms members must accept, or None if none are published."""
        return cls.objects.filter(is_published=True).order_by("-number").first()

    # TermsAcceptanceMiddleware asks for the current number on every
    # authenticated request, so it is cached briefly. 0 is cached to mean
    # "nothing published" (a plain None would look like a cache miss).
    # Saves and deletes invalidate; with several gunicorn workers each holds
    # its own LocMemCache, so the short TTL is what guarantees every worker
    # notices a publish within a minute.
    CURRENT_CACHE_KEY = "terms:current-number"
    CURRENT_CACHE_TTL = 60

    @classmethod
    def current_number(cls):
        """Version number of the current terms, or None if none published."""
        cached = cache.get(cls.CURRENT_CACHE_KEY)
        if cached is None:
            current = cls.current()
            cached = current.number if current else 0
            cache.set(cls.CURRENT_CACHE_KEY, cached, cls.CURRENT_CACHE_TTL)
        return cached or None

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(self.CURRENT_CACHE_KEY)

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        cache.delete(self.CURRENT_CACHE_KEY)
        return result

    @classmethod
    def next_number(cls):
        """The number a newly-created version should take."""
        highest = cls.objects.order_by("-number").values_list(
            "number", flat=True
        ).first()
        return (highest or 0) + 1

    def save_revision(self, editor, action):
        """Snapshot this version's text so edits are reviewable afterwards."""
        return TermsRevision.objects.create(
            terms=self, version_number=self.number, title=self.title,
            content=self.content, change_note=self.change_note,
            action=action, editor=editor,
        )


class TermsRevision(models.Model):
    """A snapshot of the terms taken every time an admin changes them.

    Answers "who changed what, and when": the editor, the timestamp, the kind
    of change, and the full text as it stood after that save. Kept even if the
    version itself is later deleted.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        EDITED = "edited", "Edited"
        PUBLISHED = "published", "Published"
        UNPUBLISHED = "unpublished", "Unpublished"
        DELETED = "deleted", "Deleted"

    terms = models.ForeignKey(
        TermsVersion, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="revisions",
    )
    version_number = models.PositiveIntegerField()
    title = models.CharField(max_length=140)
    content = models.TextField(blank=True)
    change_note = models.CharField(max_length=250, blank=True)
    action = models.CharField(
        max_length=12, choices=Action.choices, default=Action.EDITED
    )
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="terms_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"v{self.version_number} {self.get_action_display().lower()} by "
            f"{self.editor} @ {self.created_at:%Y-%m-%d %H:%M}"
        )


class TermsAcceptance(models.Model):
    """One member's acceptance of one version of the terms.

    Rows are written once and never updated — this is the evidence that a
    given member agreed to a given text at a given moment. ``version_number``
    and ``terms_title`` are copied in so the record still means something if
    the version row is later deleted.
    """

    class Source(models.TextChoices):
        PORTAL = "portal", "Accepted in the portal"
        WAITLIST = "waitlist", "Accepted on the waitlist form"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="terms_acceptances",
    )
    terms = models.ForeignKey(
        TermsVersion, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="acceptances",
    )
    version_number = models.PositiveIntegerField()
    terms_title = models.CharField(max_length=140, blank=True)
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.PORTAL
    )
    accepted_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-accepted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "version_number"],
                name="unique_terms_acceptance_per_user_version",
            ),
        ]

    def __str__(self):
        return f"{self.user} accepted v{self.version_number} @ {self.accepted_at:%Y-%m-%d %H:%M}"


class TextBlock(models.Model):
    """An admin's wording for one of the text blocks on the fixed pages
    (see core/blocks.py). While no row exists the built-in wording shows."""

    key = models.CharField(max_length=60, unique=True)
    text = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="text_blocks_edited",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key

    def save(self, *args, **kwargs):
        from .blocks import forget_texts

        super().save(*args, **kwargs)
        forget_texts()

    def delete(self, *args, **kwargs):
        from .blocks import forget_texts

        super().delete(*args, **kwargs)
        forget_texts()


class Picture(models.Model):
    """A picture for the pages: uploaded on the Pictures tab, shrunk on the
    way in, and served to everyone (it lives under media/public/)."""

    image = models.ImageField(upload_to="public/pictures/")
    alt = models.CharField(
        max_length=140, help_text="What the picture shows, for people who can't see it.",
    )
    caption = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="pictures_uploaded",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.alt

    def markdown(self):
        """The line to paste into a page (a backslash or square bracket in
        the alt text would break it, so they are escaped)."""
        alt = self.alt.replace("\\", "&#92;").replace("[", "&#91;").replace("]", "&#93;")
        return f"![{alt}]({self.image.url})"
