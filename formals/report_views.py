"""Committee-only reporting and downloads; no contact details are exported."""

import csv
from functools import wraps
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import HOST_COLLEGES
from .report_forms import statistics_filter
from .reporting import (
    EXCHANGE_LABELS, LONDON, attendance_ledger, booking_outcome, booking_report_row, member_name,
    report_exchanges, safe_csv_cell, statistics as get_statistics,
)
from .views import member_required


def committee_required(view):
    @wraps(view)
    @member_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_portal_admin:
            raise PermissionDenied("Only the committee can view Formal Exchange statistics.")
        return view(request, *args, **kwargs)
    return wrapped


def _filter_query(filters):
    return urlencode({name: (filters.get(name) or "") for name in ("date_from", "date_to", "college", "ledger")})


@committee_required
@require_GET
def statistics(request):
    form = statistics_filter(request.GET)
    context = {"nav_active": "formals", "formal_tab": "statistics", "form": form}
    if form.is_valid():
        now = timezone.now()
        filters = form.cleaned_data
        page = Paginator(attendance_ledger(filters, now), 25).get_page(request.GET.get("page"))
        context.update(get_statistics(filters, now))
        context.update({
            "page": page,
            "attendance_rows": [booking_report_row(booking, now) for booking in page],
            "filter_query": _filter_query(filters),
            "filters": filters,
            "college_name": dict(HOST_COLLEGES).get(filters.get("college"), "All host colleges"),
        })
    return render(request, "formals/statistics.html", context, status=200 if form.is_valid() else 400)


class _CsvBuffer:
    def write(self, value):
        return value


def _csv_rows(rows):
    writer = csv.writer(_CsvBuffer())
    yield "\ufeff"  # Excel recognises UTF-8 names and college punctuation.
    for row in rows:
        yield writer.writerow([safe_csv_cell(cell) for cell in row])


def _attendance_rows(filters):
    yield ["Booking ID", "Formal ID", "Dinner date (Europe/London)", "Formal", "Host college", "Host member ID", "Host", "Member organiser ID", "Member organiser", "Place type", "Status", "College ticket", "Host confirmed attendance", "Guest confirmed attendance", "Formal cancelled", "Places booked", "Confirmed guest visits", "Confirmed attendees", "Companions on booking"]
    now = timezone.now()
    for booking in attendance_ledger(filters, now).iterator(chunk_size=200):
        listing, event = booking.listing, booking.listing.event
        yield [
            booking.pk, listing.pk, timezone.localtime(event.start, LONDON).isoformat(),
            event.title, listing.get_college_display(), listing.host.pk, member_name(listing.host),
            booking.guest_id, member_name(booking.guest), booking.get_purpose_display(),
            booking_outcome(booking, now), booking.get_ticket_status_display(),
            "Yes" if booking.host_confirmed else "No", "Yes" if booking.guest_confirmed else "No",
            "Yes" if event.is_cancelled else "No",
            booking.places, booking.confirmed_places, "; ".join(booking.attended_names),
            "; ".join(booking.companion_names),
        ]


def _exchange_rows(filters):
    yield ["Exchange ID", "Original formal ID", "Original dinner date (Europe/London)", "Original formal", "Original host college", "Original host member ID", "Original host", "Return host member ID", "Return host", "Return college", "Return by", "Current outcome", "Original places booked", "Return places agreed"]
    for exchange in report_exchanges(filters).iterator(chunk_size=200):
        listing = exchange.original_booking.listing
        yield [
            exchange.pk, listing.pk, timezone.localtime(listing.event.start, LONDON).isoformat(),
            listing.event.title, listing.get_college_display(), exchange.creditor_id, member_name(exchange.creditor),
            exchange.debtor_id, member_name(exchange.debtor), exchange.get_return_college_display(),
            exchange.return_by.isoformat(), EXCHANGE_LABELS[exchange.status],
            exchange.original_booking.places, exchange.return_places,
        ]


def _member_rows(filters):
    yield ["Member ID", "Member", "Dinners hosted with confirmed guests", "Personal confirmed guest visits", "Completed exchanges involved in", "Completed as original host", "Completed as return host"]
    for row in get_statistics(filters)["member_rows"]:
        yield [
            row["member_id"], row["name"], row["hosted_dinners"], row["guest_visits"],
            row["completed_exchanges"], row["completed_original_host"], row["completed_return_host"],
        ]


@committee_required
@require_GET
def statistics_export(request):
    form = statistics_filter(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("The report filters are invalid. Correct the dates or college before exporting.")
    kind = request.GET.get("kind", "attendance")
    if kind not in ("attendance", "exchanges", "members"):
        return HttpResponseBadRequest("Choose an attendance, exchanges or members export.")
    rows = {"attendance": _attendance_rows, "exchanges": _exchange_rows, "members": _member_rows}[kind](form.cleaned_data)
    response = StreamingHttpResponse(_csv_rows(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="formal-{kind}-{timezone.localdate():%Y-%m-%d}.csv"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
