from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.utils import timezone

COLLEGES = [
    ("christs", "Christ's"),
    ("churchill", "Churchill"),
    ("clare", "Clare"),
    ("clare-hall", "Clare Hall"),
    ("corpus", "Corpus Christi"),
    ("darwin", "Darwin"),
    ("downing", "Downing"),
    ("emmanuel", "Emmanuel"),
    ("fitzwilliam", "Fitzwilliam"),
    ("girton", "Girton"),
    ("gonville-caius", "Gonville & Caius"),
    ("homerton", "Homerton"),
    ("hughes-hall", "Hughes Hall"),
    ("jesus", "Jesus"),
    ("kings", "King's"),
    ("lucy-cavendish", "Lucy Cavendish"),
    ("magdalene", "Magdalene"),
    ("murray-edwards", "Murray Edwards"),
    ("newnham", "Newnham"),
    ("pembroke", "Pembroke"),
    ("peterhouse", "Peterhouse"),
    ("queens", "Queens'"),
    ("robinson", "Robinson"),
    ("selwyn", "Selwyn"),
    ("sidney-sussex", "Sidney Sussex"),
    ("st-catharines", "St Catharine's"),
    ("st-edmunds", "St Edmund's"),
    ("st-johns", "St John's"),
    ("trinity", "Trinity"),
    ("trinity-hall", "Trinity Hall"),
    ("wolfson", "Wolfson"),
    ("other", "Other / staff / not collegiate"),
]


class User(AbstractUser):
    """A portal member.

    Two kinds of account:
    - RAVEN: a Cambridge member who signed in with Raven; ``crsid`` is set and
      is the canonical identifier (username mirrors it).
    - ASSOCIATE: a partner/guest without a CRSid, created through the waitlist
      and approved by an admin; signs in with email + password.
    """

    class AccountType(models.TextChoices):
        RAVEN = "raven", "Raven (CRSid)"
        ASSOCIATE = "associate", "Associate (approved guest)"

    class Messaging(models.TextChoices):
        DEFAULT = "default", "Committee only"
        ENABLED = "enabled", "Can message anyone"
        MUTED = "muted", "Muted"

    account_type = models.CharField(
        max_length=12, choices=AccountType.choices, default=AccountType.RAVEN
    )
    crsid = models.CharField(
        max_length=16, unique=True, null=True, blank=True,
        help_text="Cambridge CRSid, e.g. dbj25. Set automatically by Raven login.",
    )
    college = models.CharField(max_length=32, choices=COLLEGES, blank=True)
    course = models.CharField(
        max_length=120, blank=True,
        help_text="What you're studying (or your role), e.g. 'MPhil History of Science'.",
    )
    bio = models.TextField(
        blank=True, max_length=1500,
        help_text="A short 'about me' shown on your member profile.",
    )
    talk_to_me_about = models.CharField(
        max_length=200, blank=True,
        help_text="Conversation starters, e.g. 'the Civil War, sourdough, "
                  "returning to study at 40'.",
    )
    work = models.CharField(
        max_length=200, blank=True,
        help_text="What you do or did before Cambridge, e.g. '15 years in "
                  "supply-chain logistics'.",
    )
    interests = models.CharField(
        max_length=250, blank=True,
        help_text="Hobbies and interests, e.g. 'hill walking, chess, "
                  "amateur radio'.",
    )
    mobile = models.CharField(
        max_length=24, blank=True,
        help_text="Used for WhatsApp group adds and event organiser contact. "
                  "Only visible to admins and to organisers of events you RSVP to.",
    )
    photo = models.ImageField(upload_to="profiles/", blank=True)

    is_portal_admin = models.BooleanField(
        default=False,
        help_text="Society admin: can approve members, manage events, send mailers.",
    )
    is_super_admin = models.BooleanField(
        default=False,
        help_text="Super admin: can appoint/remove admins, manage tags and "
                  "site-wide email settings. Normally just the webmaster.",
    )
    is_banned = models.BooleanField(default=False)
    is_shadow_banned = models.BooleanField(
        default=False,
        help_text="Shadow ban: the member can use the site normally, but their "
                  "events and messages are invisible to everyone else.",
    )
    shadow_banned_at = models.DateTimeField(null=True, blank=True)
    messaging = models.CharField(
        max_length=10, choices=Messaging.choices, default=Messaging.DEFAULT,
        help_text="Who this member may send direct messages to. When "
                  "site-wide messaging is restricted (the default) members "
                  "can only write to the committee unless an admin enables "
                  "them; muted members can read but never send. Admins can "
                  "always message anyone.",
    )
    wants_mailer = models.BooleanField(
        default=True, verbose_name="Email me the What's On mailer",
        help_text="The society's round-up of what's on, sent by the committee now and then.",
    )
    banned_at = models.DateTimeField(null=True, blank=True)
    banned_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bans_issued",
    )

    # Terms and conditions. The authoritative log is core.TermsAcceptance;
    # these two fields are a denormalised copy of the latest acceptance so
    # TermsAcceptanceMiddleware can check every request without a join.
    terms_accepted_version = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Highest version of the terms this member has accepted.",
    )
    terms_accepted_at = models.DateTimeField(null=True, blank=True)

    # Personal calendar feed: the secret in /me/calendar.ics?token=…
    calendar_token = models.CharField(max_length=48, blank=True, db_index=True)

    # One-time WhatsApp invite (requirement: approval grants a single view of
    # the group link; afterwards access must be re-requested from an admin).
    whatsapp_link_viewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["first_name", "last_name"]

    def __str__(self):
        name = self.get_full_name() or self.username
        return f"{name} ({self.crsid})" if self.crsid else name

    @property
    def initials(self):
        parts = [self.first_name[:1], self.last_name[:1]]
        joined = "".join(p for p in parts if p).upper()
        return joined or self.username[:2].upper()

    @property
    def profile_complete(self):
        return bool(self.first_name and self.last_name and self.college and self.mobile)

    class Onboarding(models.TextChoices):
        NEVER_LOGGED_IN = "never_logged_in", "Never logged in"
        TERMS = "terms", "Terms not accepted"
        PROFILE = "profile", "Profile incomplete"
        COMPLETE = "complete", "Complete"

    def onboarding_status(self, current_terms_number):
        """Where this account is in sign-up, in the order the gates fire.

        Raven creates the account at first login, before the terms gate and
        the profile form, and an approved associate exists before they ever
        log in — so an account can sit at any of these steps indefinitely.
        That is why some members show no college or mobile: they never got
        as far as the profile form.
        """
        if self.last_login is None:
            return self.Onboarding.NEVER_LOGGED_IN
        if not self.has_accepted_terms(current_terms_number):
            return self.Onboarding.TERMS
        if not self.profile_complete:
            return self.Onboarding.PROFILE
        return self.Onboarding.COMPLETE

    @classmethod
    def incomplete_q(cls, current_terms_number):
        """Queryset filter matching every account whose ``onboarding_status``
        is anything other than complete."""
        q = (
            Q(last_login__isnull=True) | Q(first_name="") | Q(last_name="")
            | Q(college="") | Q(mobile="")
        )
        if current_terms_number is not None:
            q |= Q(terms_accepted_version__isnull=True) | Q(
                terms_accepted_version__lt=current_terms_number
            )
        return q

    def has_accepted_terms(self, current_number):
        """Has this member accepted version ``current_number`` (or later)?

        ``None`` means no terms are published, so there is nothing to accept.
        """
        if current_number is None:
            return True
        return (
            self.terms_accepted_version is not None
            and self.terms_accepted_version >= current_number
        )

    def record_terms_acceptance(self, terms, source, ip=None, user_agent="",
                                accepted_at=None):
        """Write the acceptance log row and update the denormalised fields.

        Idempotent: a member who has already accepted this version keeps their
        original timestamp rather than gaining a second row.
        """
        from core.models import TermsAcceptance

        acceptance, _ = TermsAcceptance.objects.get_or_create(
            user=self, version_number=terms.number,
            defaults={
                "terms": terms,
                "terms_title": terms.title,
                "source": source,
                "ip_address": ip,
                "user_agent": (user_agent or "")[:300],
                "accepted_at": accepted_at or timezone.now(),
            },
        )
        if (self.terms_accepted_version or 0) < terms.number:
            self.terms_accepted_version = terms.number
            self.terms_accepted_at = acceptance.accepted_at
            self.save(update_fields=["terms_accepted_version", "terms_accepted_at"])
        return acceptance

    def get_calendar_token(self):
        if not self.calendar_token:
            self.reset_calendar_token()
        return self.calendar_token

    def reset_calendar_token(self):
        import secrets

        self.calendar_token = secrets.token_urlsafe(24)
        self.save(update_fields=["calendar_token"])

    @property
    def can_view_whatsapp_link(self):
        return self.whatsapp_link_viewed_at is None

    def mark_whatsapp_link_viewed(self):
        self.whatsapp_link_viewed_at = timezone.now()
        self.save(update_fields=["whatsapp_link_viewed_at"])

    @property
    def is_muted(self):
        """Muted members can't send at all. Admins are never muted."""
        return self.messaging == self.Messaging.MUTED and not self.is_portal_admin

    @property
    def messaging_enabled(self):
        """May message any member, whatever the site-wide messaging mode."""
        return self.is_portal_admin or self.messaging == self.Messaging.ENABLED

    def shadow_ban(self):
        self.is_shadow_banned = True
        self.shadow_banned_at = timezone.now()
        self.save(update_fields=["is_shadow_banned", "shadow_banned_at"])

    def shadow_unban(self):
        self.is_shadow_banned = False
        self.shadow_banned_at = None
        self.save(update_fields=["is_shadow_banned", "shadow_banned_at"])

    def ban(self, by_admin):
        self.is_banned = True
        self.banned_at = timezone.now()
        self.banned_by = by_admin
        self.is_active = False  # blocks password auth outright
        self.save(update_fields=["is_banned", "banned_at", "banned_by", "is_active"])

    def unban(self):
        self.is_banned = False
        self.banned_at = None
        self.banned_by = None
        self.is_active = True
        self.save(update_fields=["is_banned", "banned_at", "banned_by", "is_active"])


class WaitlistRequest(models.Model):
    """A request for an account from someone without a CRSid (e.g. a partner).

    Approving creates an ASSOCIATE user and emails them a set-password link.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField(unique=True)
    mobile = models.CharField(max_length=24, blank=True)
    connection = models.TextField(
        help_text="Their connection to the society, e.g. 'Partner of Jane Smith (Wolfson)'."
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # Which terms the applicant ticked on the public form, and when. Shown to
    # the reviewing admin, and carried onto the account when it is approved so
    # a new associate isn't asked to accept the same version twice.
    terms_version = models.PositiveIntegerField(null=True, blank=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="waitlist_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=200, blank=True)
    created_user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="waitlist_request",
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} <{self.email}> [{self.status}]"


class WhatsAppAccessRequest(models.Model):
    """A member asking an admin to re-admit them to the WhatsApp group after
    their one-time invite link has been used."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        HANDLED = "handled", "Handled"
        DECLINED = "declined", "Declined"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="whatsapp_requests",
    )
    message = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="whatsapp_requests_handled",
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"WhatsApp access: {self.user} [{self.status}]"
