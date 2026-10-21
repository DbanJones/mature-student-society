"""Private member screens; domain services own every state transition."""

from datetime import datetime, time
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from events.models import Event
from inbox.policy import can_message

from . import services
from .forms import (
    AdditionalDatesFormSet, ApplicationForm, AttendanceForm, BrowseForm, ExtensionForm, GuestNamesForm, IssueForm, ListingForm,
    ReasonForm, ResolutionForm, ReturnProposalForm,
)
from .models import FormalBooking, FormalEmail, FormalExchange, FormalListing, ReturnProposal
from .sharing import whatsapp_share_url


def member_required(view):
    @wraps(view)
    @never_cache
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or request.user.is_banned:
            raise PermissionDenied("An active society membership is required.")
        response = view(request, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
    return wrapped


def _listings():
    occupied = FormalBooking.objects.filter(
        Q(status__in=["reserved", "attended"])
        | Q(status="held", return_proposal__status="pending", return_proposal__expires_at__gt=timezone.now())
    ).only("places", "listing_id")
    return FormalListing.objects.select_related("event", "event__created_by", "event__host", "event__category").prefetch_related(
        Prefetch("bookings", queryset=occupied, to_attr="_occupied_bookings")
    )


def _prepare_booking(request, booking):
    booking.agreed_return_places = booking.terms_snapshot.get("agreed_return_places") or booking.listing.return_places or booking.places
    booking.attendee_form = GuestNamesForm(booking=booking, auto_id=f"names-{booking.pk}-%s")
    booking.attendance_form = AttendanceForm(booking=booking, user=request.user, auto_id=f"attendance-{booking.pk}-%s")
    peer = booking.listing.host if request.user.pk == booking.guest_id else booking.guest
    booking.can_message_peer = can_message(request.user, peer)
    if booking.status == "no_show":
        names = booking.seat_names
        booking.host_reported_names = [names[index] for index in booking.host_attendance or [] if 0 <= index < len(names)]
        booking.guest_reported_names = [names[index] for index in booking.guest_attendance or [] if 0 <= index < len(names)]
    return booking


def _application(request, listing, mine=None):
    if listing.host.pk == request.user.pk or mine or not listing.is_open or not listing.spots_left:
        return None, ""
    form = ApplicationForm(listing=listing, user=request.user, auto_id=f"booking-{listing.pk}-%s")
    if listing.invitation_type == "reciprocal" and not form.fields["return_college"].choices:
        return None, "Your profile must name an eligible college where you can host the return places."
    return form, ""


def _host_query(user, prefix="event__"):
    return Q(**{prefix + "host": user}) | Q(**{prefix + "host__isnull": True, prefix + "created_by": user})


def _listing(request, pk, manage=False):
    listing = get_object_or_404(_listings(), pk=pk)
    is_host = listing.host.pk == request.user.pk
    is_admin = request.user.is_portal_admin
    if manage:
        if not (is_host or is_admin):
            raise PermissionDenied("Only this formal's host or the committee can manage it.")
    elif not (is_host or is_admin or listing.bookings.filter(guest=request.user).exists()):
        if not Event.objects.visible_to(request.user).filter(pk=listing.event_id).exists():
            raise Http404("No formal here.")
        if listing.host.is_banned or not listing.host.is_active:
            raise Http404("No formal here.")
    return listing


def _exchange(request, pk):
    exchange = get_object_or_404(
        FormalExchange.objects.select_related(
            "creditor", "debtor", "original_booking__listing__event",
            "original_booking__listing__event__created_by", "original_booking__listing__event__host",
            "extension_requested_by",
        ), pk=pk,
    )
    if not (request.user.is_portal_admin or request.user.pk in (exchange.creditor_id, exchange.debtor_id)):
        raise Http404("No exchange here.")
    return exchange


def _booking(request, pk):
    booking = get_object_or_404(FormalBooking.objects.select_related("listing__event__created_by", "listing__event__host", "guest"), pk=pk)
    if not (request.user.is_portal_admin or request.user.pk in (booking.guest_id, booking.listing.host.pk)):
        raise Http404("No reservation here.")
    return booking


def _errors(form, error):
    for message in error.messages:
        form.add_error(None, message)


def _flash_error(request, error):
    messages.error(request, " ".join(error.messages))


def _render(request, template, context=None, **kwargs):
    return render(request, f"formals/{template}.html", {"nav_active": "formals", **(context or {})}, **kwargs)


@member_required
def index(request):
    form = BrowseForm(request.GET)
    listings = _listings().filter(
        event__in=Event.objects.visible_to(request.user),
        event__start__gt=timezone.now(), booking_closes_at__gt=timezone.now(),
    ).filter(
        Q(event__host__isnull=True, event__created_by__is_active=True, event__created_by__is_banned=False)
        | Q(event__host__is_active=True, event__host__is_banned=False)
    )
    if form.is_valid():
        for name in ("college", "invitation_type", "booking_mode"):
            if form.cleaned_data.get(name):
                listings = listings.filter(**{name: form.cleaned_data[name]})
        if form.cleaned_data.get("after"):
            after = timezone.make_aware(datetime.combine(form.cleaned_data["after"], time.min))
            listings = listings.filter(event__start__gte=after)
        if form.cleaned_data.get("before"):
            before = timezone.make_aware(datetime.combine(form.cleaned_data["before"], time.max))
            listings = listings.filter(event__start__lte=before)
        if form.cleaned_data.get("min_places", 1) > 1:
            listings = [listing for listing in listings if listing.spots_left >= form.cleaned_data["min_places"]]
    page = Paginator(listings, 18).get_page(request.GET.get("page"))
    page_listings = list(page)
    own_bookings = {b.listing_id: b for b in FormalBooking.objects.filter(listing__in=page_listings, guest=request.user)}
    for listing in page_listings:
        listing.my_booking = own_bookings.get(listing.pk)
        listing.application, listing.eligibility_message = _application(request, listing, listing.my_booking)
        listing.can_manage = listing.host.pk == request.user.pk or request.user.is_portal_admin
        listing.whatsapp_url = whatsapp_share_url(request, listing)
    page.object_list = page_listings
    query = request.GET.copy()
    query.pop("page", None)
    return _render(request, "index", {"formal_tab": "browse", "form": form, "page": page, "filter_query": query.urlencode(), "filters_active": any(request.GET.get(k) for k in form.fields)})


@member_required
def create(request):
    return_exchange = None
    return_for = request.POST.get("return_for") if request.method == "POST" else request.GET.get("return_for")
    if return_for:
        if not return_for.isdecimal() or len(return_for) > 18:
            raise Http404("No exchange here.")
        return_exchange = _exchange(request, int(return_for))
        if request.user.pk != return_exchange.debtor_id:
            raise PermissionDenied("Only the member offering the return invitation can create it.")
        if return_exchange.status not in ("conditional", "owed"):
            messages.info(request, "This swap is closed or under review. Check its current arrangements before offering a return dinner.")
            return redirect(return_exchange)
        if return_exchange.return_by < timezone.localdate():
            messages.info(request, "Agree a later return deadline before offering a new dinner.")
            return redirect(return_exchange)
    form = ListingForm(request.POST or None, user=request.user)
    # Missing management data remains compatible with the ordinary single-date form.
    dates_data = request.POST if request.method == "POST" and "dates-TOTAL_FORMS" in request.POST else None
    date_formset = AdditionalDatesFormSet(dates_data, prefix="dates") if return_exchange is None else None
    dates_valid = date_formset is None or dates_data is None or date_formset.is_valid()
    if request.method == "POST" and form.is_valid() and dates_valid:
        if return_exchange:
            original = return_exchange.original_booking.listing.event
            if form.cleaned_data["start"] <= (original.end or original.start):
                form.add_error("start", "Your return formal must be after the original dinner ends.")
            elif timezone.localdate(form.cleaned_data["start"]) > return_exchange.return_by:
                form.add_error("start", "Choose a date on or before the agreed return deadline.")
            if form.cleaned_data["college"] != return_exchange.return_college:
                form.add_error("college", "This swap needs a return dinner at the agreed college. Contact the other member if your hosting arrangements have changed.")
            if form.cleaned_data["capacity"] < return_exchange.remaining_return_places:
                form.add_error("capacity", "Offer enough places for the remaining return invitation.")
        if form.errors:
            return _render(request, "form", {"formal_tab": "create", "form": form, "return_exchange": return_exchange, "date_formset": date_formset})
        batches = [form.cleaned_data]
        seen_dates = {timezone.localdate(form.cleaned_data["start"])}
        if dates_data is not None and date_formset:
            original_start = timezone.localtime(form.cleaned_data["start"])
            original_end = timezone.localtime(form.cleaned_data["end"]) if form.cleaned_data.get("end") else None
            closes = timezone.localtime(form.cleaned_data["booking_closes_at"])
            for date_form in date_formset:
                day = date_form.cleaned_data.get("date")
                if not day:
                    continue
                if day in seen_dates:
                    date_form.add_error("date", "Each dinner date must be different.")
                    continue
                seen_dates.add(day)
                delta = day - original_start.date()
                values = request.POST.copy()
                try:
                    values["start"] = (original_start + delta).strftime("%Y-%m-%dT%H:%M")
                    values["end"] = (original_end + delta).strftime("%Y-%m-%dT%H:%M") if original_end else ""
                    values["booking_closes_at"] = (closes + delta).strftime("%Y-%m-%dT%H:%M")
                except (OverflowError, ValueError):
                    date_form.add_error("date", "Choose a date that allows time for booking and the whole dinner.")
                    continue
                extra_form = ListingForm(values, user=request.user)
                if extra_form.is_valid():
                    batches.append(extra_form.cleaned_data)
                else:
                    for field, errors in extra_form.errors.items():
                        for error in errors:
                            date_form.add_error("date", f"{extra_form.fields[field].label if field in extra_form.fields else 'Date'}: {error}")
            if any(date_form.errors for date_form in date_formset):
                return _render(request, "form", {"formal_tab": "create", "form": form, "date_formset": date_formset})
        try:
            with transaction.atomic():
                created = [services.create_listing(request.user, **data) for data in batches]
            listing = created[0]
        except services.FormalError as error:
            _errors(form, error)
        else:
            if return_exchange:
                messages.success(request, "Your formal is published. Choose it below to send your return invitation.")
                return redirect(return_exchange)
            if len(created) > 1:
                messages.success(request, f"{len(created)} dates published. Manage each dinner from My plans.")
                return redirect("formals:mine")
            messages.success(request, "Your formal is now available to members.")
            return redirect(listing)
    return _render(request, "form", {"formal_tab": "create", "form": form, "return_exchange": return_exchange, "date_formset": date_formset})


@member_required
def edit(request, pk):
    listing = _listing(request, pk, manage=True)
    if listing.bookings.exists():
        messages.info(request, "The invitation terms are fixed once a member has responded. Cancel this formal if the arrangements can no longer be honoured.")
        return redirect(listing)
    form = ListingForm(request.POST or None, user=request.user, listing=listing)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_listing(request.user, listing.pk, **form.cleaned_data)
        except services.FormalError as error:
            _errors(form, error)
        else:
            messages.success(request, "Formal updated.")
            return redirect(listing)
    return _render(request, "form", {"form": form, "listing": listing})


@member_required
def detail(request, pk):
    listing = _listing(request, pk)
    return _detail(request, listing)


def _detail(request, listing, bound_application=None, bound_booking=None, bound_reason=None, status=200):
    can_manage = listing.host.pk == request.user.pk or request.user.is_portal_admin
    bookings = listing.bookings.select_related("guest", "exchange", "return_proposal__exchange").annotate(
        action_order=Case(
            When(status="requested", then=Value(0)),
            When(status="reserved", then=Value(1)),
            When(status="held", then=Value(2)),
            When(status="attended", then=Value(3)),
            default=Value(4), output_field=IntegerField(),
        )
    ).order_by("action_order", "created_at", "pk")
    bookings = list(bookings)
    mine = next((b for b in bookings if b.guest_id == request.user.pk), None)
    application, eligibility_message = _application(request, listing, mine)
    if bound_application is not None and mine is None and not eligibility_message and listing.host.pk != request.user.pk:
        application = bound_application
    for booking in bookings:
        booking.listing = listing
        if can_manage or booking is mine:
            _prepare_booking(request, booking)
            if bound_booking and booking.pk == bound_booking[0]:
                setattr(booking, bound_booking[1], bound_booking[2])
    messaging_members = []
    if not listing.event.is_cancelled and listing.event.start > timezone.now() and (can_manage or mine and mine.status == "reserved"):
        participants = {listing.host.pk: listing.host}
        participants.update({b.guest_id: b.guest for b in bookings if b.status == "reserved"})
        messaging_members = [member for member in participants.values() if member.is_active and not member.is_banned and not member.is_shadow_banned and can_message(request.user, member)]
    return _render(request, "detail", {
        "listing": listing, "can_manage": can_manage,
        "can_edit": can_manage and not listing.bookings.exists() and not listing.event.is_cancelled,
        "my_booking": mine, "application": application,
        "eligibility_message": eligibility_message,
        "bookings": bookings if can_manage else [],
        "reason_form": bound_reason if bound_reason is not None else ReasonForm(), "now": timezone.now(),
        "whatsapp_url": whatsapp_share_url(request, listing), "messaging_members": messaging_members,
        "activity": listing.activity.select_related("actor")[:30] if can_manage else [],
    }, status=status)


@member_required
@require_POST
def apply(request, pk):
    listing = _listing(request, pk)
    form = ApplicationForm(request.POST, listing=listing, user=request.user, auto_id=f"booking-{listing.pk}-%s")
    if form.is_valid():
        try:
            booking = services.apply_for_place(request.user, listing.pk, **form.cleaned_data)
        except services.FormalError as error:
            _errors(form, error)
        else:
            if booking.status == FormalBooking.Status.REQUESTED:
                messages.success(request, f"Request sent for {booking.places} place{'s' if booking.places != 1 else ''}. The host will confirm.")
            else:
                messages.success(request, f"{booking.places} place{'s' if booking.places != 1 else ''} reserved. The host will confirm the college tickets.")
            if request.POST.get("next") == "browse":
                return redirect("formals:index")
            return redirect(listing)
    return _detail(request, listing, bound_application=form, status=400)


@member_required
@require_POST
def request_action(request, pk, action):
    booking = _booking(request, pk)
    if booking.listing.host.pk != request.user.pk and not request.user.is_portal_admin:
        raise PermissionDenied("Only the host or committee can decide requests.")
    if action not in ("accept", "decline"):
        return HttpResponseBadRequest("Unknown request action.")
    try:
        services.decide_request(request.user, booking.pk, accept=action == "accept")
    except services.FormalError as error:
        _flash_error(request, error)
    else:
        messages.success(request, f"{booking.places} place{'s' if booking.places != 1 else ''} reserved; please arrange the college tickets." if action == "accept" else "Request declined.")
    return redirect(booking.listing)


@member_required
@require_POST
def booking_action(request, pk, action):
    booking = _booking(request, pk)
    recovery_field = None
    try:
        if action == "cancel":
            form = ReasonForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Please keep the cancellation reason under 1,000 characters.")
                return redirect(booking.listing)
            services.cancel_booking(request.user, booking.pk, reason=form.cleaned_data["reason"])
            message = "Reservation cancelled. Any return invitation already owed is still recorded in your swaps."
        elif action in ("ticket-secured", "ticket-pending"):
            services.set_ticket_status(request.user, booking.pk, secured=action == "ticket-secured")
            message = "College ticket status updated."
        elif action == "guest-names":
            recovery_field = "attendee_form"
            form = GuestNamesForm(request.POST, booking=booking, auto_id=f"names-{booking.pk}-%s")
            if not form.is_valid():
                return _detail(request, _listing(request, booking.listing_id), bound_booking=(booking.pk, recovery_field, form), status=400)
            services.update_guest_names(request.user, booking.pk, **form.cleaned_data)
            message = "Guest names saved."
        elif action == "attendance":
            attended_places = None
            if booking.places > 1 or "attendance_submitted" in request.POST:
                recovery_field = "attendance_form"
                form = AttendanceForm(request.POST, booking=booking, user=request.user, auto_id=f"attendance-{booking.pk}-%s")
                if not form.is_valid():
                    return _detail(request, _listing(request, booking.listing_id), bound_booking=(booking.pk, recovery_field, form), status=400)
                attended_places = form.cleaned_data["attended_places"]
            services.confirm_attendance(request.user, booking.pk, attended_places=attended_places)
            message = "Attendance report saved. Both sides must agree before visits are counted."
        else:
            return HttpResponseBadRequest("Unknown reservation action.")
    except services.FormalError as error:
        if recovery_field:
            _errors(form, error)
            return _detail(request, _listing(request, booking.listing_id), bound_booking=(booking.pk, recovery_field, form), status=400)
        _flash_error(request, error)
    else:
        messages.success(request, message)
    return redirect(booking.listing)


@member_required
@require_POST
def cancel(request, pk):
    listing = _listing(request, pk, manage=True)
    form = ReasonForm(request.POST)
    if form.is_valid():
        try:
            services.cancel_listing(request.user, listing.pk, reason=form.cleaned_data["reason"])
        except services.FormalError as error:
            _errors(form, error)
        else:
            messages.success(request, "Formal cancelled. Affected members have been notified.")
            return redirect(listing)
    return _detail(request, _listing(request, pk, manage=True), bound_reason=form, status=400)


@member_required
def mine(request):
    now = timezone.now()
    listings = _listings().filter(_host_query(request.user))
    bookings = FormalBooking.objects.filter(guest=request.user).select_related("listing__event", "listing__event__created_by", "listing__event__host", "exchange", "return_proposal__exchange")
    exchanges = FormalExchange.objects.filter(Q(creditor=request.user) | Q(debtor=request.user)).select_related("creditor", "debtor", "original_booking__listing__event")
    active_listings = listings.filter(event__is_cancelled=False).filter(
        Q(event__start__gt=now) | Q(bookings__status__in=["reserved", "no_show"])
    ).distinct()
    active_bookings = bookings.filter(listing__event__is_cancelled=False).filter(
        Q(status__in=["reserved", "no_show"])
        | Q(status="requested", listing__event__start__gt=now, listing__booking_closes_at__gt=now)
        | Q(status="held", return_proposal__status="pending", return_proposal__expires_at__gt=now)
    )
    active_exchanges = exchanges.filter(status__in=["conditional", "owed", "disputed"])
    past_listings = Paginator(listings.exclude(pk__in=active_listings.values("pk")).order_by("-event__start", "-pk"), 12).get_page(request.GET.get("past_hosting_page"))
    past_bookings = Paginator(bookings.exclude(pk__in=active_bookings.values("pk")).order_by("-listing__event__start", "-pk"), 12).get_page(request.GET.get("past_booking_page"))
    past_exchanges = Paginator(exchanges.exclude(pk__in=active_exchanges.values("pk")).order_by("-created_at", "-pk"), 12).get_page(request.GET.get("past_exchange_page"))
    hosted_page = Paginator(active_listings.order_by("event__start", "pk"), 12).get_page(request.GET.get("hosting_page"))
    guest_page = Paginator(active_bookings.order_by("listing__event__start", "pk"), 12).get_page(request.GET.get("booking_page"))
    for page in (guest_page, past_bookings):
        page.object_list = [_prepare_booking(request, booking) for booking in page]
    for page in (hosted_page, past_listings):
        page.object_list = list(page)
        for listing in page:
            listing.whatsapp_url = whatsapp_share_url(request, listing)
    return _render(request, "mine", {
        "formal_tab": "mine",
        "listings": hosted_page,
        "bookings": guest_page,
        "exchanges": Paginator(active_exchanges.order_by("return_by", "pk"), 12).get_page(request.GET.get("exchange_page")),
        "past_listings": past_listings, "past_bookings": past_bookings, "past_exchanges": past_exchanges,
        "history_count": sum(p.paginator.count for p in (past_listings, past_bookings, past_exchanges)),
        "show_history": any(request.GET.get(key) for key in ("past_hosting_page", "past_booking_page", "past_exchange_page")),
        "now": now,
    })


@member_required
def exchange(request, pk):
    swap = _exchange(request, pk)
    return _exchange_detail(request, swap)


def _exchange_detail(request, swap, bound_form=None, status=200):
    forms = {
        "issue_form": IssueForm(auto_id="issue_%s"),
        "reason_form": ReasonForm(auto_id="waive_%s"),
        "extension_form": ExtensionForm(initial={"return_by": swap.return_by}, auto_id="extension_%s"),
        "resolution_form": ResolutionForm(auto_id="resolution_%s"),
    }
    if bound_form is not None:
        forms[bound_form[0]] = bound_form[1]
    proposals = list(swap.proposals.select_related("booking__listing__event", "booking__listing__event__created_by", "booking__listing__event__host"))
    now = timezone.now()
    active = swap.status in ("conditional", "owed")
    for proposal in proposals:
        proposal.actionable = active and proposal.status == "pending" and proposal.expires_at > now and proposal.booking.listing.is_open
        proposal.expired = proposal.status == "pending" and proposal.expires_at <= now
    current_proposals = [p for p in proposals if (
        p.status == "pending" and p.expires_at > now
        or p.status == "accepted" and p.booking.status in ("reserved", "no_show")
        or p.status == "accepted" and p.booking.status == "attended" and not swap.remaining_return_places
    )]
    current_ids = {p.pk for p in current_proposals}
    can_propose = active and swap.remaining_return_places > 0 and request.user.pk == swap.debtor_id and not any(p.actionable or (p.status == "accepted" and p.booking.status in ("reserved", "no_show")) for p in proposals)
    candidates = []
    if can_propose:
        candidates = [listing for listing in _listings().filter(
            _host_query(request.user), college=swap.return_college,
            event__is_cancelled=False, event__start__gt=max(now, swap.original_booking.listing.event.end or swap.original_booking.listing.event.start),
            event__start__lte=timezone.make_aware(datetime.combine(swap.return_by, time.max)), booking_closes_at__gt=now,
        ).exclude(bookings__guest=swap.creditor) if listing.spots_left >= swap.remaining_return_places]
    return _render(request, "exchange", {
        "exchange": swap, "formal_tab": "mine", "proposals": proposals,
        "current_proposals": current_proposals,
        "past_proposals": [p for p in proposals if p.pk not in current_ids],
        "is_creditor": request.user.pk == swap.creditor_id,
        "is_debtor": request.user.pk == swap.debtor_id,
        "can_message_peer": can_message(request.user, swap.debtor if request.user.pk == swap.creditor_id else swap.creditor),
        "active": active, "can_propose": can_propose,
        "can_waive": request.user.pk == swap.creditor_id and swap.status in ("conditional", "owed", "disputed"),
        "proposal_form": ReturnProposalForm(listings=candidates) if candidates else None,
        **forms, "recovery_form": bound_form[1] if bound_form is not None else None,
        "activity": swap.activity.select_related("actor")[:60],
        "now": now,
    }, status=status)


@member_required
@require_POST
def exchange_action(request, pk, action):
    swap = _exchange(request, pk)
    recovery_field = None
    try:
        if action == "propose":
            listing_id = request.POST.get("listing_id", "")
            if not listing_id.isdecimal() or len(listing_id) > 18:
                messages.error(request, "Choose a return formal.")
                return redirect(swap)
            candidate = get_object_or_404(_listings(), pk=int(listing_id))
            proposal = services.propose_return(request.user, swap.pk, candidate.pk)
            message = f"Return invitation sent. {proposal.booking.places} place{'s' if proposal.booking.places != 1 else ''} held while awaiting a response."
        elif action in ("waive", "issue"):
            recovery_field = "issue_form" if action == "issue" else "reason_form"
            form = IssueForm(request.POST, auto_id="issue_%s") if action == "issue" else ReasonForm(request.POST, auto_id="waive_%s")
            if not form.is_valid():
                return _exchange_detail(request, swap, bound_form=(recovery_field, form), status=400)
            if action == "waive":
                services.waive_exchange(request.user, swap.pk, reason=form.cleaned_data["reason"])
                message = "The return promise has been waived."
            else:
                services.report_issue(request.user, swap.pk, reason=form.cleaned_data["reason"])
                message = "The committee has been asked to review this exchange."
        elif action == "extension":
            recovery_field = "extension_form"
            form = ExtensionForm(request.POST, auto_id="extension_%s")
            if not form.is_valid():
                return _exchange_detail(request, swap, bound_form=(recovery_field, form), status=400)
            services.request_extension(request.user, swap.pk, form.cleaned_data["return_by"])
            message = "New deadline proposed. The other member must agree before it changes."
        elif action in ("accept-extension", "decline-extension"):
            services.respond_to_extension(request.user, swap.pk, accept=action == "accept-extension")
            message = "Deadline response recorded."
        elif action == "resolve":
            if not request.user.is_portal_admin:
                raise PermissionDenied("Committee access required.")
            recovery_field = "resolution_form"
            form = ResolutionForm(request.POST, auto_id="resolution_%s")
            if not form.is_valid():
                return _exchange_detail(request, swap, bound_form=(recovery_field, form), status=400)
            services.resolve_exchange(request.user, swap.pk, **form.cleaned_data)
            message = "Committee decision recorded."
        else:
            return HttpResponseBadRequest("Unknown exchange action.")
    except services.FormalError as error:
        if recovery_field:
            _errors(form, error)
            return _exchange_detail(request, _exchange(request, pk), bound_form=(recovery_field, form), status=400)
        _flash_error(request, error)
    else:
        messages.success(request, message)
    return redirect(swap)


@member_required
@require_POST
def proposal_action(request, pk, action):
    proposal = get_object_or_404(ReturnProposal.objects.select_related("exchange"), pk=pk)
    swap = _exchange(request, proposal.exchange_id)
    try:
        if action in ("accept", "decline"):
            services.respond_to_proposal(request.user, proposal.pk, accept=action == "accept")
            message = "Return invitation accepted. The host will confirm the college ticket." if action == "accept" else "Return invitation declined. The return promise remains open."
        elif action == "cancel":
            services.cancel_proposal(request.user, proposal.pk)
            message = "Return invitation withdrawn. Any return promise already owed is still open."
        else:
            return HttpResponseBadRequest("Unknown proposal action.")
    except services.FormalError as error:
        _flash_error(request, error)
    else:
        messages.success(request, message)
    return redirect(swap)


@member_required
def committee(request):
    if not request.user.is_portal_admin:
        raise PermissionDenied("Committee access required.")
    swaps = FormalExchange.objects.select_related("creditor", "debtor", "original_booking__listing__event")
    attention = swaps.filter(Q(status="disputed") | Q(status__in=("conditional", "owed"), return_by__lt=timezone.localdate())).order_by("return_by")
    return _render(request, "committee", {
        "formal_tab": "committee", "attention": Paginator(attention, 25).get_page(request.GET.get("page")),
        "disputed_count": swaps.filter(status="disputed").count(),
        "overdue_count": swaps.filter(status__in=("conditional", "owed", "disputed"), return_by__lt=timezone.localdate()).count(),
        "open_count": swaps.filter(status__in=("conditional", "owed", "disputed")).count(),
        "failed_email_count": FormalEmail.objects.filter(sent_at__isnull=True, attempts__gte=5).count(),
    })
