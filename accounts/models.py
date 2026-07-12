from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
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

    account_type = models.CharField(
        max_length=12, choices=AccountType.choices, default=AccountType.RAVEN
    )
    crsid = models.CharField(
        max_length=16, unique=True, null=True, blank=True,
        help_text="Cambridge CRSid, e.g. dbj25. Set automatically by Raven login.",
    )
    college = models.CharField(max_length=32, choices=COLLEGES, blank=True)
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
    is_banned = models.BooleanField(default=False)
    banned_at = models.DateTimeField(null=True, blank=True)
    banned_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bans_issued",
    )

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

    @property
    def can_view_whatsapp_link(self):
        return self.whatsapp_link_viewed_at is None

    def mark_whatsapp_link_viewed(self):
        self.whatsapp_link_viewed_at = timezone.now()
        self.save(update_fields=["whatsapp_link_viewed_at"])

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
