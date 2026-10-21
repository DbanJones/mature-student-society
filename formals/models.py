"""Private formal invitations, allocations and bilateral return promises.

Event owns the date/venue/host. These tables are the only attendance ledger
for formal events: never create a parallel events.RSVP.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import COLLEGES

HOST_COLLEGES = [(code, name) for code, name in COLLEGES if code != "other"]


class FormalListing(models.Model):
    class InvitationType(models.TextChoices):
        OPEN = "open", "Open invitation (no return promise)"
        RECIPROCAL = "reciprocal", "Reciprocal swap"

    class BookingMode(models.TextChoices):
        APPROVAL = "approval", "Host chooses guests"
        INSTANT = "instant", "Instant reservation"

    event = models.OneToOneField("events.Event", on_delete=models.PROTECT, related_name="formal_listing")
    college = models.CharField(max_length=32, choices=HOST_COLLEGES)
    capacity = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(3)])
    return_places = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(3)], help_text="Zero means the same number as the booking; otherwise the host's requested return places.")
    invitation_type = models.CharField(max_length=12, choices=InvitationType.choices, default=InvitationType.OPEN)
    booking_mode = models.CharField(max_length=12, choices=BookingMode.choices, default=BookingMode.APPROVAL)
    allowed_return_colleges = models.JSONField(default=list, blank=True)
    booking_closes_at = models.DateTimeField()
    return_by = models.DateField(null=True, blank=True)
    price_text = models.CharField(max_length=160, blank=True)
    practical_details = models.TextField(blank=True, max_length=5000)
    terms_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["event__start", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(capacity__gte=1, capacity__lte=3), name="formal_capacity_1_to_3"),
            models.CheckConstraint(condition=Q(return_places__lte=3), name="formal_requested_returns_0_to_3"),
        ]

    def __str__(self):
        return self.event.title

    def get_absolute_url(self):
        return reverse("formals:detail", args=[self.pk])

    @property
    def host(self):
        return self.event.effective_host

    @property
    def occupied_count(self):
        if hasattr(self, "_occupied_bookings"):
            return sum(booking.places for booking in self._occupied_bookings)
        return self.bookings.filter(
            Q(status__in=["reserved", "attended"])
            | Q(status="held", return_proposal__status="pending", return_proposal__expires_at__gt=timezone.now())
        ).aggregate(total=models.Sum("places", default=0))["total"]

    @property
    def spots_left(self):
        return max(0, self.capacity - self.occupied_count)

    @property
    def is_open(self):
        now = timezone.now()
        return not self.event.is_cancelled and self.event.start > now and self.booking_closes_at > now

    @property
    def allowed_return_college_names(self):
        names = dict(HOST_COLLEGES)
        return [names.get(code, code) for code in self.allowed_return_colleges]


class FormalBooking(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Awaiting host approval"
        HELD = "held", "Held for a return invitation"
        RESERVED = "reserved", "Reserved"
        ATTENDED = "attended", "Attendance confirmed"
        ABSENT = "absent", "No guests attended"
        CANCELLED = "cancelled", "Cancelled"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"
        NO_SHOW = "no_show", "Attendance disputed"

    class Purpose(models.TextChoices):
        ORIGINAL = "original", "Guest place"
        RETURN = "return", "Return invitation"

    class TicketStatus(models.TextChoices):
        PENDING = "pending", "College ticket not yet confirmed"
        SECURED = "secured", "College ticket confirmed by host"

    listing = models.ForeignKey(FormalListing, on_delete=models.PROTECT, related_name="bookings")
    guest = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="formal_bookings")
    places = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(3)])
    companion_names = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    purpose = models.CharField(max_length=8, choices=Purpose.choices, default=Purpose.ORIGINAL)
    return_college = models.CharField(max_length=32, choices=HOST_COLLEGES, blank=True)
    terms_version = models.PositiveIntegerField(default=1)
    terms_snapshot = models.JSONField(default=dict)
    ticket_status = models.CharField(max_length=8, choices=TicketStatus.choices, default=TicketStatus.PENDING)
    host_confirmed = models.BooleanField(default=False)
    guest_confirmed = models.BooleanField(default=False)
    host_attendance = models.JSONField(null=True, blank=True, default=None)
    guest_attendance = models.JSONField(null=True, blank=True, default=None)
    reserved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["listing", "guest"], name="formal_unique_guest"),
            models.CheckConstraint(condition=Q(places__gte=1, places__lte=3), name="formal_booking_places_1_to_3"),
        ]
        indexes = [models.Index(fields=["listing", "status"])]

    def __str__(self):
        return f"{self.guest} — {self.listing}"

    @property
    def seat_names(self):
        lead = self.guest.get_full_name().strip() or f"Member #{self.guest_id}"
        companions = self.companion_names or []
        return [lead] + [companions[index] if index < len(companions) and companions[index] else f"Guest {index + 2} (name needed)" for index in range(self.places - 1)]

    @property
    def missing_guest_names(self):
        names = self.companion_names or []
        return len(names) < self.places - 1 or any(not name for name in names)

    @property
    def confirmed_seats(self):
        if self.status != "attended":
            return []
        if self.places == 1 and self.host_attendance is None and self.guest_attendance is None:
            # Historical single-place confirmations and imported attendance.
            return [0]
        if self.host_attendance == self.guest_attendance and self.host_attendance is not None:
            return self.host_attendance
        return []

    @property
    def confirmed_places(self):
        return len(self.confirmed_seats)

    @property
    def lead_attended(self):
        return 0 in self.confirmed_seats

    @property
    def attended_names(self):
        names = self.seat_names
        return [names[index] for index in self.confirmed_seats if 0 <= index < len(names)]


class FormalExchange(models.Model):
    class Status(models.TextChoices):
        CONDITIONAL = "conditional", "Return promise agreed; first dinner pending"
        OWED = "owed", "Return invitation owed"
        COMPLETED = "completed", "Both dinners confirmed"
        CLOSED = "closed", "Closed before first attendance"
        WAIVED = "waived", "Return promise waived"
        DISPUTED = "disputed", "Committee review requested"

    original_booking = models.OneToOneField(FormalBooking, on_delete=models.PROTECT, related_name="exchange")
    creditor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="formal_returns_due")
    debtor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="formal_returns_owed")
    return_college = models.CharField(max_length=32, choices=HOST_COLLEGES)
    return_by = models.DateField()
    return_places = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(3)])
    terms_snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.CONDITIONAL)
    extension_date = models.DateField(null=True, blank=True)
    extension_requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="formal_extensions_requested")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(return_places__gte=1, return_places__lte=3), name="formal_exchange_returns_1_to_3")]

    def __str__(self):
        return f"Return invitation #{self.pk}"

    def get_absolute_url(self):
        return reverse("formals:exchange", args=[self.pk])

    @property
    def is_overdue(self):
        return self.status in ("conditional", "owed", "disputed") and self.return_by < timezone.localdate()

    @property
    def fulfilled_return_places(self):
        return sum(proposal.booking.confirmed_places for proposal in self.proposals.filter(status="accepted", booking__status="attended").select_related("booking"))

    @property
    def remaining_return_places(self):
        return max(0, self.return_places - self.fulfilled_return_places)


class ReturnProposal(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting acceptance"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    exchange = models.ForeignKey(FormalExchange, on_delete=models.PROTECT, related_name="proposals")
    booking = models.OneToOneField(FormalBooking, on_delete=models.PROTECT, related_name="return_proposal")
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["status", "expires_at"])]


class FormalActivity(models.Model):
    listing = models.ForeignKey(FormalListing, null=True, blank=True, on_delete=models.PROTECT, related_name="activity")
    exchange = models.ForeignKey(FormalExchange, null=True, blank=True, on_delete=models.PROTECT, related_name="activity")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="formal_activity")
    action = models.CharField(max_length=40)
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class FormalEmail(models.Model):
    """Transactional outbox. No SMTP is performed while seats are locked."""
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="formal_emails")
    subject = models.CharField(max_length=200)
    body = models.TextField()
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["sent_at", "available_at"])]


class FormalLedgerLock(models.Model):
    """A short database mutex for the society's cross-listing allocations.

    A return proposal changes two invitations and can release older holds.
    One lock gives all those writes a consistent order on MySQL/PostgreSQL.
    Do not hold it during mail delivery or any other network operation.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)


class FormalAlert(models.Model):
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="formal_alerts")
    college = models.CharField(max_length=32, choices=HOST_COLLEGES, blank=True)
    invitation_type = models.CharField(max_length=12, choices=FormalListing.InvitationType.choices, blank=True)
    booking_mode = models.CharField(max_length=12, choices=FormalListing.BookingMode.choices, blank=True)
    after = models.DateField(null=True, blank=True)
    before = models.DateField(null=True, blank=True)
    min_places = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(3)])
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(min_places__gte=1, min_places__lte=3), name="formal_alert_places_1_to_3")]

    @property
    def description(self):
        parts = [self.get_college_display() if self.college else "Any college"]
        if self.invitation_type:
            parts.append("Swap" if self.invitation_type == "reciprocal" else "No obligation")
        if self.booking_mode:
            parts.append("Instant booking" if self.booking_mode == "instant" else "Host chooses")
        parts.append(f"At least {self.min_places} place{'s' if self.min_places != 1 else ''}")
        if self.after:
            parts.append(f"From {self.after:%d %b %Y}")
        if self.before:
            parts.append(f"Until {self.before:%d %b %Y}")
        return " · ".join(parts)


class FormalAlertDelivery(models.Model):
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="formal_alert_deliveries")
    listing = models.ForeignKey(FormalListing, on_delete=models.CASCADE, related_name="alert_deliveries")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["member", "listing"], name="formal_alert_once_per_member")]
