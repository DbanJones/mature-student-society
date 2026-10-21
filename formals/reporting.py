"""Read-only statistics from the formal allocation and attendance ledger.

There are deliberately no stored counters. Dates select the dinner, not the
date somebody clicked a button. Exchange outcomes refer to the selected cohort
of original dinners and describe its current state, not completions in a period.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import HOST_COLLEGES, FormalBooking, FormalExchange, FormalListing

LONDON = ZoneInfo("Europe/London")
ACTIVE_EXCHANGES = ("conditional", "owed", "disputed")
EXCHANGE_LABELS = {
    "conditional": "First dinner pending",
    "owed": "Return dinner owed",
    "completed": "Exchanges completed",
    "closed": "Closed before first dinner",
    "waived": "Return promise waived",
    "disputed": "Committee review",
}


def _apply_dinner_filters(queryset, filters, prefix=""):
    if filters.get("date_from"):
        queryset = queryset.filter(**{
            f"{prefix}event__start__gte": datetime.combine(filters["date_from"], time.min, LONDON),
        })
    if filters.get("date_to"):
        queryset = queryset.filter(**{
            f"{prefix}event__start__lt": datetime.combine(filters["date_to"] + timedelta(days=1), time.min, LONDON),
        })
    if filters.get("college"):
        queryset = queryset.filter(**{f"{prefix}college": filters["college"]})
    return queryset


def report_listings(filters):
    return _apply_dinner_filters(FormalListing.objects.all(), filters)


def report_bookings(filters):
    return _apply_dinner_filters(
        FormalBooking.objects.select_related(
            "guest", "listing__event__host", "listing__event__created_by", "return_proposal",
        ), filters, prefix="listing__",
    ).order_by("-listing__event__start", "listing_id", "pk")


def report_exchanges(filters):
    return _apply_dinner_filters(
        FormalExchange.objects.select_related(
            "creditor", "debtor", "original_booking__listing__event",
        ), filters, prefix="original_booking__listing__",
    ).order_by("-original_booking__listing__event__start", "pk")


def dinner_finished(now=None, prefix="listing__event__"):
    """Use the recorded end, or start when the event has no end time."""
    now = now or timezone.now()
    return Q(**{f"{prefix}end__lte": now}) | Q(**{
        f"{prefix}end__isnull": True, f"{prefix}start__lte": now,
    })


def attendance_ledger(filters, now=None):
    bookings = report_bookings(filters)
    if filters.get("ledger", "attended") == "attended":
        bookings = bookings.filter(status=FormalBooking.Status.ATTENDED)
    elif filters.get("ledger") == "pending":
        bookings = bookings.filter(
            dinner_finished(now), status=FormalBooking.Status.RESERVED,
            listing__event__is_cancelled=False,
        )
    return bookings


def statistics(filters, now=None):
    now = now or timezone.now()
    listings = report_listings(filters)
    offered = listings.filter(event__is_cancelled=False)
    bookings = report_bookings(filters)
    exchanges = report_exchanges(filters)
    totals = offered.aggregate(formals_offered=Count("pk"), places_offered=Sum("capacity", default=0))
    totals["cancelled_formals"] = listings.filter(event__is_cancelled=True).count()
    totals.update({key: 0 for key in (
        "guest_visits", "companion_visits", "open_visits", "reciprocal_visits", "return_visits",
        "reserved_places", "pending_attendance", "attendance_review",
    )})
    unique_guests, supported_formals, unique_hosts = set(), set(), set()
    by_college = {}
    members = {}

    def participant(member):
        return members.setdefault(member.pk, {
            "member_id": member.pk, "name": member_name(member),
            "hosted_dinner_ids": set(), "guest_visits": 0,
            "completed_original_host": 0, "completed_return_host": 0,
            "completed_exchange_ids": set(),
        })

    # Derive attendance from the agreed seat identities, not the reserved group
    # size. A chunked pass keeps JSON handling consistent across database engines
    # and avoids storing counters that could diverge from attendance records.
    for booking in bookings.iterator(chunk_size=200):
        listing, event = booking.listing, booking.listing.event
        row = by_college.setdefault(listing.college, {
            "college": listing.college, "guest_visits": 0,
            "pending_attendance": 0, "guest_ids": set(), "listing_ids": set(),
        })
        if booking.status == FormalBooking.Status.RESERVED and not event.is_cancelled:
            totals["reserved_places"] += booking.places
            if (event.end or event.start) <= now:
                totals["pending_attendance"] += 1
                row["pending_attendance"] += 1
        elif booking.status == FormalBooking.Status.NO_SHOW:
            totals["attendance_review"] += 1
        visits = booking.confirmed_places
        if not visits:
            continue
        totals["guest_visits"] += visits
        totals["companion_visits"] += visits - int(booking.lead_attended)
        row["guest_visits"] += visits
        row["listing_ids"].add(listing.pk)
        supported_formals.add(listing.pk)
        unique_hosts.add(event.host_id or event.created_by_id)
        participant(listing.host)["hosted_dinner_ids"].add(listing.pk)
        if booking.lead_attended:
            unique_guests.add(booking.guest_id)
            row["guest_ids"].add(booking.guest_id)
            participant(booking.guest)["guest_visits"] += 1
        visit_type = "return" if booking.purpose == "return" else (
            "reciprocal" if listing.invitation_type == "reciprocal" else "open"
        )
        totals[f"{visit_type}_visits"] += visits
    totals.update(
        unique_guests=len(unique_guests), supported_formals=len(supported_formals),
        unique_hosts=len(unique_hosts),
    )
    status_counts = dict(exchanges.order_by().values_list("status").annotate(total=Count("pk")))
    totals["exchanges_total"] = sum(status_counts.values())
    totals["exchanges_completed"] = status_counts.get("completed", 0)
    totals["exchanges_active"] = sum(status_counts.get(status, 0) for status in ACTIVE_EXCHANGES)
    totals["exchanges_overdue"] = exchanges.filter(status__in=ACTIVE_EXCHANGES, return_by__lt=timezone.localdate(now)).count()
    exchange_statuses = [{"status": status, "label": EXCHANGE_LABELS[status], "count": status_counts.get(status, 0)} for status in FormalExchange.Status.values]
    for exchange in exchanges.filter(status="completed").iterator(chunk_size=200):
        for member, role in ((exchange.creditor, "original"), (exchange.debtor, "return")):
            row = participant(member)
            row[f"completed_{role}_host"] += 1
            row["completed_exchange_ids"].add(exchange.pk)
    for row in members.values():
        row["hosted_dinners"] = len(row.pop("hosted_dinner_ids"))
        row["completed_exchanges"] = len(row.pop("completed_exchange_ids"))
    member_rows = sorted(members.values(), key=lambda row: (
        -row["completed_exchanges"], -row["hosted_dinners"], -row["guest_visits"],
        row["name"].casefold(), row["member_id"],
    ))

    # Aggregate each table separately: joining bookings to listings before
    # summing capacity would multiply places by the number of guests.
    for row in offered.order_by().values("college").annotate(formals_offered=Count("pk"), places_offered=Sum("capacity")):
        by_college.setdefault(row["college"], {}).update(row)
    for row in by_college.values():
        row["unique_guests"] = len(row.pop("guest_ids", ()))
        row["supported_formals"] = len(row.pop("listing_ids", ()))
    college_names = dict(HOST_COLLEGES)
    defaults = {key: 0 for key in ("formals_offered", "places_offered", "guest_visits", "unique_guests", "supported_formals", "pending_attendance")}
    college_rows = [
        {**defaults, **row, "name": college_names.get(code, code)}
        for code, row in by_college.items()
    ]
    college_rows.sort(key=lambda row: (-row["guest_visits"], row["name"]))
    return {"totals": totals, "college_rows": college_rows, "exchange_statuses": exchange_statuses, "member_rows": member_rows}


def member_name(member):
    # An associate's username may be an email address: never use it as fallback.
    return member.get_full_name().strip() or f"Member #{member.pk}"


def booking_outcome(booking, now=None):
    now = now or timezone.now()
    if booking.listing.event.is_cancelled and booking.status in ("requested", "held", "reserved"):
        return "Formal cancelled"
    if booking.status == "requested" and (
        booking.listing.booking_closes_at <= now or booking.listing.event.start <= now
    ):
        return "Request closed without a place"
    if booking.status == "held":
        proposal = getattr(booking, "return_proposal", None)
        if proposal:
            if proposal.status == "expired" or (proposal.status == "pending" and proposal.expires_at <= now):
                return "Return invitation expired"
            if proposal.status in ("declined", "cancelled"):
                return f"Return invitation {proposal.status}"
    if booking.status == "reserved":
        if (booking.listing.event.end or booking.listing.event.start) <= now:
            return "Awaiting attendance confirmation"
    return booking.get_status_display()


def booking_report_row(booking, now=None):
    return {
        "booking": booking,
        "host_name": member_name(booking.listing.host),
        "guest_name": member_name(booking.guest),
        "attended_names": booking.attended_names,
        "outcome": booking_outcome(booking, now),
    }


def safe_csv_cell(value):
    """Quote formula-like cells, including payloads after leading whitespace."""
    text = str(value) if value is not None else ""
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text
