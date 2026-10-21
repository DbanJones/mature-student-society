"""All mutations of the Formal Exchange ledger live here.

The society has at most three places per invitation. A single, short database
mutex serializes allocation and reciprocal changes, including changes spanning
two invitations. This deliberately favours correctness over write throughput.
No external work takes place inside these transactions. Use MySQL/PostgreSQL
for concurrent production bookings; SQLite is a development database.
"""
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import models, transaction
from django.utils import timezone

from accounts.models import User
from events.models import Category, Event
from inbox.models import MessageBlock
from notifications.models import Notification
from notifications.services import notify

from .models import (
    HOST_COLLEGES, FormalActivity, FormalBooking, FormalEmail, FormalExchange,
    FormalLedgerLock, FormalListing, ReturnProposal,
)


class FormalError(ValidationError):
    pass


def ledger_write(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            with transaction.atomic():
                FormalLedgerLock.objects.get_or_create(pk=1)
                FormalLedgerLock.objects.select_for_update().get(pk=1)
                return fn(*args, **kwargs)
        except FormalError:
            raise
        except ValidationError as exc:
            raise FormalError(exc.messages) from exc
    return wrapped


def _member(actor):
    if not actor or not actor.is_authenticated or not actor.is_active or actor.is_banned:
        raise FormalError("Sign in with an active member account to use Formal Exchange.")


def _host(actor, listing):
    _member(actor)
    if actor.pk != listing.host.pk and not actor.is_portal_admin:
        raise FormalError("Only the host or committee can manage this invitation.")


def _participant(actor, exchange):
    _member(actor)
    if actor.pk not in (exchange.creditor_id, exchange.debtor_id) and not actor.is_portal_admin:
        raise FormalError("This return promise is private to its members and the committee.")


def _pair(a, b):
    _member(a)
    _member(b)
    if a.pk == b.pk:
        raise FormalError("You cannot reserve your own guest place.")
    if a.is_shadow_banned or b.is_shadow_banned or MessageBlock.exists_between(a, b):
        raise FormalError("This invitation is not available between these accounts.")


def _listing(pk):
    return FormalListing.objects.select_related("event__created_by", "event__host").get(pk=pk)


def _booking(pk):
    return FormalBooking.objects.select_related("listing__event__created_by", "listing__event__host", "guest").get(pk=pk)


def _exchange(pk):
    return FormalExchange.objects.select_related("original_booking__listing__event", "creditor", "debtor").get(pk=pk)


def _record(actor, action, text, listing=None, exchange=None):
    return FormalActivity.objects.create(actor=actor, action=action, text=text[:2000], listing=listing, exchange=exchange)


def _tell(people, text, obj):
    url = obj.get_absolute_url()
    people = list({u.pk: u for u in people if u and u.is_active and not u.is_banned}.values())
    notify(people, Notification.Kind.EVENT, text, url=url)
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    FormalEmail.objects.bulk_create([
        FormalEmail(recipient=u, subject="Formal Exchange update", body=f"{text}\n\n{base}{url}\n\nSign in to your member account to view details.")
        for u in people if u.email
    ])


def _snapshot(listing):
    return {
        "title": listing.event.title, "start": listing.event.start.isoformat(),
        "end": listing.event.end.isoformat() if listing.event.end else None,
        "location": listing.event.location, "college": listing.college,
        "host_id": listing.host.pk, "invitation_type": listing.invitation_type,
        "booking_mode": listing.booking_mode, "return_by": str(listing.return_by) if listing.return_by else None,
        "return_places": listing.return_places,
        "allowed_return_colleges": list(listing.allowed_return_colleges),
        "price_text": listing.price_text, "practical_details": listing.practical_details,
        "terms_version": listing.terms_version,
    }


def _listing_data(actor, data, listing=None):
    _member(actor)
    host = listing.host if listing else actor
    if not data.get("hosting_confirmed"):
        raise FormalError("Confirm that you can host these guests and have checked your college's rules.")
    if host.college not in dict(HOST_COLLEGES) or data.get("college") != host.college:
        raise FormalError("List a formal at the college recorded on your member profile.")
    start, end = data.get("start"), data.get("end")
    closes = data.get("booking_closes_at")
    if not start or start <= timezone.now() or not closes or not timezone.now() < closes <= start:
        raise FormalError("Choose a future formal and a booking deadline before it starts.")
    if end and end <= start:
        raise FormalError("The end must be after the start.")
    if data.get("invitation_type") == "reciprocal":
        return_by = data.get("return_by")
        if not return_by or return_by <= timezone.localdate(end or start):
            raise FormalError("Choose a return deadline after this formal.")
    else:
        data["return_by"] = None
        data["allowed_return_colleges"] = []
        data["return_places"] = 0
    requested_returns = data.get("return_places", 0)
    if isinstance(requested_returns, bool) or not isinstance(requested_returns, (int, str)):
        raise FormalError("Choose the number of return places you would like.")
    data["return_places"] = 0 if requested_returns in (0, "0") else _places(requested_returns)
    colleges = data.get("allowed_return_colleges", [])
    if not isinstance(colleges, list) or any(c not in dict(HOST_COLLEGES) for c in colleges):
        raise FormalError("Choose valid colleges for a return invitation.")
    data["allowed_return_colleges"] = sorted(set(colleges))
    if not str(data.get("title", "")).strip() or not str(data.get("location", "")).strip():
        raise FormalError("Add a title and meeting location.")
    return data


LISTING_FIELDS = ("college", "capacity", "invitation_type", "booking_mode", "allowed_return_colleges", "booking_closes_at", "return_places", "return_by", "price_text", "practical_details")
EVENT_FIELDS = ("title", "description", "location", "start", "end")


@ledger_write
def create_listing(actor, **data):
    data = _listing_data(actor, data)
    category, _ = Category.objects.get_or_create(slug="formal-exchange", defaults={"name": "Formal Exchange", "emoji": "🍽", "color": "#52664b"})
    event = Event(category=category, created_by=actor, host=actor, members_only=True, show_attendees=False,
                  **{k: data[k] for k in EVENT_FIELDS if k in data})
    event.full_clean(exclude=["slug"])
    event.save()
    listing = FormalListing(event=event, **{k: data[k] for k in LISTING_FIELDS if k in data})
    listing.full_clean()
    listing.save()
    _record(actor, "created", "Invitation published to members.", listing=listing)
    from .alerts import notify_matching_members
    notify_matching_members(listing)
    return listing


@ledger_write
def update_listing(actor, listing_id, **data):
    listing = _listing(listing_id)
    _host(actor, listing)
    if listing.bookings.exists():
        raise FormalError("Terms are fixed once a member has responded. Cancel this invitation and publish a new one for material changes.")
    if listing.event.is_cancelled:
        raise FormalError("A cancelled invitation cannot be reopened.")
    data = _listing_data(actor, data, listing)
    for key in LISTING_FIELDS:
        if key in data:
            setattr(listing, key, data[key])
    for key in EVENT_FIELDS:
        if key in data:
            setattr(listing.event, key, data[key])
    listing.terms_version += 1
    listing.event.members_only = True
    listing.event.full_clean()
    listing.full_clean()
    listing.event.save()
    listing.save()
    _record(actor, "updated", f"Invitation terms updated to version {listing.terms_version}.", listing=listing)
    return listing


def _places(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise FormalError("Choose between one and three guest places.")
    try:
        value = int(value)
    except (ValueError, TypeError):
        raise FormalError("Choose between one and three guest places.")
    if not 1 <= value <= 3:
        raise FormalError("Choose between one and three guest places.")
    return value


def _companion_names(value, places):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > places - 1:
        raise FormalError("Add one name for each additional guest, up to the number of places booked.")
    names = []
    for name in value:
        if not isinstance(name, str) or len(name.strip()) > 120 or any(ord(char) < 32 for char in name):
            raise FormalError("Use a name of up to 120 characters for each additional guest.")
        names.append(name.strip())
    return names


def _available(listing, places=1):
    _expire()
    if not listing.is_open:
        raise FormalError("Bookings have closed for this formal.")
    if listing.host.college != listing.college:
        raise FormalError("The host's college has changed. Ask the host to review this invitation.")
    if listing.spots_left < places:
        raise FormalError("There are not enough guest places left for this booking. Choose a smaller group or another formal.")


def _reserve(booking):
    listing = booking.listing
    _pair(booking.guest, listing.host)
    _available(listing, booking.places)
    if booking.terms_version != listing.terms_version:
        raise FormalError("These terms have changed. Review the invitation again.")
    if listing.invitation_type == "reciprocal" and booking.return_college != booking.guest.college:
        raise FormalError("The guest's college has changed. They must confirm new return arrangements with the host.")
    booking.status = "reserved"
    booking.reserved_at = timezone.now()
    booking.save(update_fields=["status", "reserved_at", "updated_at"])
    if listing.invitation_type == "reciprocal" and booking.purpose == "original":
        FormalExchange.objects.create(original_booking=booking, creditor=listing.host, debtor=booking.guest,
                                      return_college=booking.return_college, return_by=listing.return_by,
                                      return_places=booking.terms_snapshot.get("agreed_return_places", listing.return_places or booking.places),
                                      terms_snapshot=booking.terms_snapshot)


@ledger_write
def apply_for_place(actor, listing_id, terms_version, return_college="", can_host=False, accept_terms=False, places=1, companion_names=None):
    _member(actor)
    places = _places(places)
    companion_names = _companion_names(companion_names, places)
    listing = _listing(listing_id)
    _pair(actor, listing.host)
    previous = listing.bookings.filter(guest=actor).first()
    if previous:
        if previous.purpose == "original" and previous.status in ("requested", "reserved", "attended"):
            if previous.places != places:
                raise FormalError("You already have a booking for a different number of places. Contact the host to change it.")
            return previous
        raise FormalError("You already have a response recorded for this formal. Contact the host or choose another date.")
    if not listing.is_open:
        raise FormalError("Bookings have closed for this formal.")
    if places > listing.capacity:
        raise FormalError("This formal does not offer enough places for your group.")
    if not accept_terms or int(terms_version) != listing.terms_version:
        raise FormalError("Review and accept the current invitation terms before continuing.")
    if listing.invitation_type == "reciprocal":
        if not can_host or return_college not in dict(HOST_COLLEGES) or return_college != actor.college:
            raise FormalError("Confirm that you can host a return formal at the college on your profile.")
        if listing.allowed_return_colleges and return_college not in listing.allowed_return_colleges:
            raise FormalError("This host is seeking a return invitation at one of the listed colleges.")
    else:
        return_college = ""
    snapshot = _snapshot(listing)
    snapshot.update(return_college=return_college, can_host=bool(can_host), places=places,
                    agreed_return_places=(listing.return_places or places) if listing.invitation_type == "reciprocal" else 0,
                    accepted_at=timezone.now().isoformat())
    booking = FormalBooking.objects.create(listing=listing, guest=actor, return_college=return_college,
                                           places=places, companion_names=companion_names,
                                           terms_version=listing.terms_version, terms_snapshot=snapshot)
    if listing.booking_mode == "instant":
        _reserve(booking)
    _record(actor, "applied", f"{actor.get_full_name() or actor.username}: {booking.get_status_display()} ({places} place{'s' if places != 1 else ''}).", listing=listing)
    _tell([listing.host, actor], "A formal place has been reserved." if booking.status == "reserved" else "A formal guest request is awaiting host approval.", listing)
    return booking


@ledger_write
def decide_request(actor, booking_id, accept):
    booking = _booking(booking_id)
    _host(actor, booking.listing)
    target = "reserved" if accept else "declined"
    if booking.status == target:
        return booking
    if booking.status != "requested" or booking.purpose != "original":
        raise FormalError("This request has already been decided.")
    if accept:
        _reserve(booking)
    else:
        booking.status = "declined"
        booking.save(update_fields=["status", "updated_at"])
    _record(actor, "request_decided", f"Guest request {target}.", listing=booking.listing)
    _tell([booking.guest], f"Your formal guest request was {target}.", booking.listing)
    return booking


def _release_proposal(proposal, status="cancelled"):
    if proposal.status not in ("pending", "accepted"):
        return
    if proposal.booking.status == "attended" or proposal.booking.host_confirmed or proposal.booking.guest_confirmed:
        return
    proposal.status = status
    proposal.save(update_fields=["status"])
    proposal.booking.status = "cancelled"
    proposal.booking.save(update_fields=["status", "updated_at"])


def _cancel(booking, actor, reason):
    if booking.status in ("cancelled", "declined", "withdrawn"):
        return booking
    if booking.status == "attended" or booking.host_confirmed or booking.guest_confirmed:
        raise FormalError("Attendance has been reported. Ask the committee to review this booking.")
    if booking.status in ("reserved", "no_show") and booking.listing.event.start <= timezone.now() and not actor.is_portal_admin:
        raise FormalError("This formal has started. Ask the committee to review a cancellation or attendance issue.")
    if booking.purpose == "return":
        proposal = ReturnProposal.objects.select_related("booking").get(booking=booking)
        _release_proposal(proposal)
        _record(actor, "return_cancelled", reason or "Return invitation cancelled; any outstanding promise remains due.", exchange=proposal.exchange)
        _tell([proposal.exchange.creditor, proposal.exchange.debtor], "A return invitation was cancelled. The return promise remains in place.", proposal.exchange)
    else:
        exchange = FormalExchange.objects.filter(original_booking=booking).first()
        if exchange and exchange.proposals.filter(
            models.Q(booking__host_confirmed=True) | models.Q(booking__guest_confirmed=True)
        ).exists():
            raise FormalError("Attendance at a return dinner has been reported. The committee must review the exchange.")
        booking.status = "withdrawn" if booking.status == "requested" and actor.pk == booking.guest_id else "cancelled"
        booking.save(update_fields=["status", "updated_at"])
        if exchange:
            exchange.status = "closed"
            exchange.save(update_fields=["status", "updated_at"])
            for proposal in exchange.proposals.select_related("booking"):
                _release_proposal(proposal)
            _record(actor, "closed", "First booking cancelled before attendance; conditional return promise closed.", exchange=exchange)
    _record(actor, "booking_cancelled", reason or "Guest place cancelled.", listing=booking.listing)
    _tell([booking.listing.host, booking.guest], "A formal guest place was cancelled.", booking.listing)
    booking.refresh_from_db()
    return booking


@ledger_write
def cancel_booking(actor, booking_id, reason=""):
    booking = _booking(booking_id)
    _member(actor)
    if actor.pk != booking.guest_id:
        _host(actor, booking.listing)
    return _cancel(booking, actor, reason)


@ledger_write
def cancel_listing(actor, listing_id, reason=""):
    listing = _listing(listing_id)
    _host(actor, listing)
    if listing.event.is_cancelled:
        return listing
    if listing.event.start <= timezone.now():
        raise FormalError("This formal has started. Review individual attendance issues with the committee.")
    for booking in listing.bookings.select_related("listing__event__created_by", "listing__event__host", "guest"):
        _cancel(booking, actor, reason)
    listing.event.is_cancelled = True
    listing.event.save(update_fields=["is_cancelled", "updated_at"])
    _record(actor, "listing_cancelled", reason or "Invitation cancelled by host.", listing=listing)
    return listing


@ledger_write
def set_ticket_status(actor, booking_id, secured):
    booking = _booking(booking_id)
    _host(actor, booking.listing)
    if booking.status != "reserved":
        raise FormalError("College tickets can only be recorded for reserved places.")
    status = "secured" if secured else "pending"
    if booking.ticket_status != status:
        booking.ticket_status = status
        booking.save(update_fields=["ticket_status", "updated_at"])
        _record(actor, "ticket_updated", f"College ticket: {status}.", listing=booking.listing)
        _tell([booking.guest], f"Your formal's college ticket is now marked {status}.", booking.listing)
    return booking


@ledger_write
def update_guest_names(actor, booking_id, companion_names):
    booking = _booking(booking_id)
    _member(actor)
    if actor.pk != booking.guest_id:
        _host(actor, booking.listing)
    if booking.status not in ("requested", "held", "reserved", "no_show"):
        raise FormalError("Guest names cannot change after the booking has closed.")
    names = _companion_names(companion_names, booking.places)
    if booking.host_confirmed or booking.guest_confirmed:
        previous = booking.companion_names or []
        if any(name and (index >= len(names) or names[index] != name) for index, name in enumerate(previous)):
            raise FormalError("Attendance has been reported. You can add missing guest names, but cannot change a named guest.")
    if names != booking.companion_names:
        booking.companion_names = names
        booking.save(update_fields=["companion_names", "updated_at"])
        _record(actor, "guest_names_updated", f"Guest names {'completed' if booking.host_confirmed or booking.guest_confirmed else 'updated'} for booking #{booking.pk}.", listing=booking.listing)
    return booking


def _attendance_seats(booking, value):
    seats = list(range(booking.places)) if value is None else value
    if not isinstance(seats, list) or any(isinstance(seat, bool) or not isinstance(seat, int) or not 0 <= seat < booking.places for seat in seats) or len(set(seats)) != len(seats):
        raise FormalError("Choose only the guests on this booking who attended.")
    names = booking.companion_names or []
    if any(seat > 0 and (len(names) < seat or not names[seat - 1].strip()) for seat in seats):
        raise FormalError("Add the names of the additional guests who attended before confirming attendance.")
    return sorted(seats)


def _return_attendance_reported(exchange):
    return any(
        proposal.booking.confirmed_places or proposal.booking.host_attendance or proposal.booking.guest_attendance
        for proposal in exchange.proposals.select_related("booking")
    )


def _attendance_outcome(exchange):
    original = FormalBooking.objects.get(pk=exchange.original_booking_id)
    if original.status == "no_show" or exchange.proposals.filter(booking__status="no_show").exists():
        return "disputed"
    if original.status == "absent":
        return "disputed" if _return_attendance_reported(exchange) else "closed"
    if original.confirmed_places:
        return "completed" if exchange.remaining_return_places == 0 else "owed"
    return "conditional"


@ledger_write
def confirm_attendance(actor, booking_id, attended_places=None):
    booking = _booking(booking_id)
    _member(actor)
    if actor.pk not in (booking.guest_id, booking.listing.host.pk) and not actor.is_portal_admin:
        raise FormalError("Only the guest, host or committee can confirm attendance.")
    seats = _attendance_seats(booking, attended_places)
    if booking.status in ("attended", "absent"):
        if seats == booking.confirmed_seats:
            return booking
        raise FormalError("Attendance is already agreed. Ask the committee to review any correction.")
    if booking.status not in ("reserved", "no_show") or booking.listing.event.is_cancelled:
        raise FormalError("This booking cannot be marked attended.")
    if (booking.listing.event.end or booking.listing.event.start) > timezone.now():
        raise FormalError("Confirm attendance after the formal has taken place.")
    if actor.pk == booking.guest_id or actor.is_portal_admin:
        booking.guest_confirmed = True
        booking.guest_attendance = seats
    if actor.pk == booking.listing.host.pk or actor.is_portal_admin:
        booking.host_confirmed = True
        booking.host_attendance = seats
    if booking.host_confirmed and booking.guest_confirmed:
        if booking.host_attendance == booking.guest_attendance:
            booking.status = "attended" if seats else "absent"
        else:
            booking.status = "no_show"
    booking.save(update_fields=["host_confirmed", "guest_confirmed", "host_attendance", "guest_attendance", "status", "updated_at"])
    exchange = FormalExchange.objects.filter(original_booking=booking).first()
    if booking.purpose == "return":
        exchange = ReturnProposal.objects.select_related("exchange").get(booking=booking).exchange
    if exchange and booking.status in ("attended", "absent", "no_show") and exchange.status in ("conditional", "owed"):
        exchange.status = _attendance_outcome(exchange)
        exchange.save(update_fields=["status", "updated_at"])
        if exchange.status == "closed":
            for proposal in exchange.proposals.select_related("booking"):
                _release_proposal(proposal)
        _record(actor, "attendance", exchange.get_status_display(), exchange=exchange)
        _tell([exchange.creditor, exchange.debtor], exchange.get_status_display(), exchange)
        if exchange.status == "disputed":
            _tell(list(User.objects.filter(is_portal_admin=True, is_active=True)), "Attendance reports for a formal swap need committee review.", exchange)
    _record(actor, "attendance", f"Attendance reported for guest booking #{booking.pk}: {len(seats)} of {booking.places} places.", listing=booking.listing)
    return booking


def _live(exchange):
    if exchange.status not in ("conditional", "owed"):
        raise FormalError("This return promise is closed or under committee review.")


@ledger_write
def propose_return(actor, exchange_id, listing_id):
    exchange = _exchange(exchange_id)
    _participant(actor, exchange)
    _live(exchange)
    if actor.pk != exchange.debtor_id:
        raise FormalError("The member who owes the return invitation must propose it.")
    _pair(exchange.creditor, exchange.debtor)
    listing = _listing(listing_id)
    if listing.host.pk != actor.pk or listing.college != exchange.return_college:
        raise FormalError("Choose your own formal at the agreed return college.")
    original = exchange.original_booking.listing.event
    if listing.event.start <= (original.end or original.start) or timezone.localdate(listing.event.start) > exchange.return_by:
        raise FormalError("The return dinner must follow the first dinner and fall on or before the agreed deadline.")
    # Stale holds are released on every dependent operation, not just by cron.
    _expire()
    existing = exchange.proposals.filter(
        models.Q(status="pending") | models.Q(status="accepted", booking__status__in=["reserved", "no_show"])
    ).first()
    if existing:
        if existing.booking.listing_id == listing.pk:
            if existing.booking.places != exchange.remaining_return_places:
                raise FormalError("The held places no longer match this swap. Ask the host to review the invitation.")
            return existing
        raise FormalError("Cancel or finish the current return invitation before proposing another.")
    remaining_places = exchange.remaining_return_places
    if remaining_places < 1:
        raise FormalError("All agreed return places have already been attended.")
    _available(listing, remaining_places)
    if listing.bookings.filter(guest=exchange.creditor).exists():
        raise FormalError("This member already has a booking history for that formal. Choose another invitation.")
    booking = FormalBooking.objects.create(listing=listing, guest=exchange.creditor, status="held", purpose="return",
                                           places=remaining_places, terms_version=listing.terms_version,
                                           terms_snapshot={**_snapshot(listing), "places": remaining_places})
    proposal = ReturnProposal.objects.create(exchange=exchange, booking=booking,
                                             expires_at=min(timezone.now() + timedelta(hours=48), listing.booking_closes_at))
    _record(actor, "return_proposed", f"Return invitation offered at {listing.event.title}; {booking.places} place{'s' if booking.places != 1 else ''} held until {proposal.expires_at:%d %b %H:%M}.", exchange=exchange)
    _tell([exchange.creditor], "You have a return formal invitation. Accept before the held place expires.", exchange)
    return proposal


@ledger_write
def respond_to_proposal(actor, proposal_id, accept):
    proposal = ReturnProposal.objects.select_related("exchange", "booking__listing__event__created_by", "booking__listing__event__host").get(pk=proposal_id)
    exchange = proposal.exchange
    _participant(actor, exchange)
    if actor.pk != exchange.creditor_id:
        raise FormalError("Only the invited member can accept or decline their return formal.")
    target = "accepted" if accept else "declined"
    if proposal.status == target:
        return proposal
    _live(exchange)
    if proposal.status != "pending" or proposal.expires_at <= timezone.now():
        raise FormalError("This return invitation is no longer available.")
    if accept:
        _pair(exchange.creditor, exchange.debtor)
        listing = proposal.booking.listing
        if not listing.is_open:
            raise FormalError("Bookings have closed for the return formal.")
        if listing.host.college != listing.college:
            raise FormalError("The host's college has changed. Ask the host to review this return invitation.")
        if proposal.booking.places != exchange.remaining_return_places or listing.occupied_count > listing.capacity:
            raise FormalError("The held places no longer match this swap. Ask the host to review the invitation.")
        proposal.status = "accepted"
        proposal.save(update_fields=["status"])
        proposal.booking.status = "reserved"
        proposal.booking.reserved_at = timezone.now()
        proposal.booking.save(update_fields=["status", "reserved_at", "updated_at"])
    else:
        _release_proposal(proposal, "declined")
    _record(actor, "return_decided", f"Return invitation {target}.", exchange=exchange)
    _tell([exchange.debtor], f"Your return invitation was {target}.", exchange)
    return proposal


@ledger_write
def cancel_proposal(actor, proposal_id):
    proposal = ReturnProposal.objects.select_related("exchange", "booking__listing__event__created_by", "booking__listing__event__host").get(pk=proposal_id)
    _participant(actor, proposal.exchange)
    _cancel(proposal.booking, actor, "Return invitation cancelled; the promise is unchanged.")
    proposal.refresh_from_db()
    return proposal


@ledger_write
def waive_exchange(actor, exchange_id, reason=""):
    exchange = _exchange(exchange_id)
    _participant(actor, exchange)
    if actor.pk != exchange.creditor_id:
        raise FormalError("Only the member owed an invitation can waive it. The committee can resolve a dispute separately.")
    if exchange.status == "waived":
        return exchange
    if exchange.status not in ("conditional", "owed", "disputed"):
        raise FormalError("This promise is already closed.")
    for proposal in exchange.proposals.select_related("booking"):
        _release_proposal(proposal)
    exchange.status = "waived"
    exchange.save(update_fields=["status", "updated_at"])
    _record(actor, "waived", reason or "The member owed a return invitation waived their promise.", exchange=exchange)
    _tell([exchange.debtor], "Your return promise has been waived by the member you hosted with.", exchange)
    return exchange


@ledger_write
def request_extension(actor, exchange_id, return_by):
    exchange = _exchange(exchange_id)
    _participant(actor, exchange)
    _live(exchange)
    if actor.pk not in (exchange.creditor_id, exchange.debtor_id):
        raise FormalError("Only the members in this swap can agree an extension.")
    if return_by <= max(exchange.return_by, timezone.localdate()):
        raise FormalError("Choose a later return deadline.")
    if exchange.extension_date:
        raise FormalError("Respond to the existing deadline extension first.")
    exchange.extension_date = return_by
    exchange.extension_requested_by = actor
    exchange.save(update_fields=["extension_date", "extension_requested_by", "updated_at"])
    _record(actor, "extension_requested", f"Deadline extension requested to {return_by:%d %b %Y}.", exchange=exchange)
    _tell([exchange.creditor if actor.pk == exchange.debtor_id else exchange.debtor], "A later return deadline has been proposed for your swap.", exchange)
    return exchange


@ledger_write
def respond_to_extension(actor, exchange_id, accept):
    exchange = _exchange(exchange_id)
    _participant(actor, exchange)
    _live(exchange)
    if actor.pk not in (exchange.creditor_id, exchange.debtor_id) or actor.pk == exchange.extension_requested_by_id:
        raise FormalError("The other member must respond to the deadline extension.")
    if not exchange.extension_date:
        raise FormalError("There is no pending deadline extension.")
    if accept:
        exchange.return_by = exchange.extension_date
    exchange.extension_date = None
    exchange.extension_requested_by = None
    exchange.save(update_fields=["return_by", "extension_date", "extension_requested_by", "updated_at"])
    _record(actor, "extension_decided", f"Deadline extension {'accepted' if accept else 'declined'}; deadline {exchange.return_by:%d %b %Y}.", exchange=exchange)
    _tell([exchange.creditor, exchange.debtor], "The proposed return deadline has been decided.", exchange)
    return exchange


@ledger_write
def report_issue(actor, exchange_id, reason):
    exchange = _exchange(exchange_id)
    _participant(actor, exchange)
    if not reason.strip():
        raise FormalError("Describe the issue for the committee.")
    if exchange.status in ("closed", "waived", "completed"):
        raise FormalError("This promise is already closed. Contact the committee for further help.")
    exchange.status = "disputed"
    exchange.save(update_fields=["status", "updated_at"])
    _record(actor, "issue_reported", reason, exchange=exchange)
    _tell(list(User.objects.filter(is_portal_admin=True, is_active=True)) + [exchange.creditor, exchange.debtor],
          "A formal swap needs committee review.", exchange)
    return exchange


@ledger_write
def resolve_exchange(actor, exchange_id, resolution, note):
    _member(actor)
    if not actor.is_portal_admin:
        raise FormalError("Only the committee can resolve a disputed swap.")
    if not note.strip():
        raise FormalError("Record the reason for this committee decision.")
    exchange = _exchange(exchange_id)
    if exchange.status not in ("disputed", "owed", "conditional"):
        raise FormalError("This promise is already closed.")
    if resolution == "resume":
        outcome = _attendance_outcome(exchange)
        if outcome == "disputed":
            raise FormalError("Resolve the attendance reports before resuming this swap, or explicitly waive the outstanding promise.")
        exchange.status = outcome
    elif resolution == "waive":
        exchange.status = "waived"
    elif resolution == "close":
        if exchange.original_booking.confirmed_places or exchange.original_booking.host_confirmed or exchange.original_booking.guest_confirmed or _return_attendance_reported(exchange):
            raise FormalError("Attendance has been reported. Waive the return promise explicitly instead.")
        exchange.status = "closed"
    elif resolution == "complete":
        if not exchange.original_booking.confirmed_places or exchange.remaining_return_places:
            raise FormalError("Confirm the first dinner and every agreed return place before completing the swap.")
        exchange.status = "completed"
    else:
        raise FormalError("Choose a valid committee decision.")
    if exchange.status in ("closed", "waived"):
        for proposal in exchange.proposals.select_related("booking"):
            _release_proposal(proposal)
    exchange.save(update_fields=["status", "updated_at"])
    _record(actor, "committee_resolution", f"{exchange.get_status_display()}: {note}", exchange=exchange)
    _tell([exchange.creditor, exchange.debtor], "The committee has recorded a decision on your formal swap.", exchange)
    return exchange


def _expire():
    count = 0
    for proposal in ReturnProposal.objects.filter(status="pending", expires_at__lte=timezone.now()).select_related("booking", "exchange"):
        _release_proposal(proposal, "expired")
        _record(None, "return_expired", "The return invitation expired. Its held place was released; the promise is unchanged.", exchange=proposal.exchange)
        _tell([proposal.exchange.creditor, proposal.exchange.debtor], "A return invitation expired. A new date can be proposed.", proposal.exchange)
        count += 1
    return count


@ledger_write
def expire_proposals():
    return _expire()


def deliver_emails(limit=50):
    """Claim with a lease, send outside the lock, retry transient failures.

    Delivery is at least once: a worker crash after SMTP accepts but before
    sent_at is saved may duplicate an email, never a booking or promise.
    """
    sent = 0
    for _ in range(limit):
        with transaction.atomic():
            job = FormalEmail.objects.select_for_update().filter(sent_at__isnull=True, available_at__lte=timezone.now(), attempts__lt=5).order_by("pk").first()
            if job is None:
                break
            job.attempts += 1
            job.available_at = timezone.now() + timedelta(minutes=10)
            job.save(update_fields=["attempts", "available_at"])
        try:
            if job.recipient.is_active and not job.recipient.is_banned and job.recipient.email:
                delivered = send_mail(job.subject, job.body, settings.DEFAULT_FROM_EMAIL, [job.recipient.email], fail_silently=False)
                if not delivered:
                    raise RuntimeError("Mail backend did not accept the message.")
            FormalEmail.objects.filter(pk=job.pk).update(sent_at=timezone.now(), last_error="")
            sent += 1
        except Exception as exc:
            # Do not retain SMTP responses that could contain personal data.
            FormalEmail.objects.filter(pk=job.pk).update(last_error=type(exc).__name__, available_at=timezone.now() + timedelta(minutes=min(60, 2 ** job.attempts)))
    return sent
