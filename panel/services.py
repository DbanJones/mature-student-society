"""Aggregation and mail-drafting helpers for the society admin panel.

Everything here is a pure function returning plain dicts/lists/strings so the
views stay thin and the mailer body is testable without a browser.
"""

import datetime

from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncMonth
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.formats import date_format

from accounts.models import User, WaitlistRequest, WhatsAppAccessRequest
from core.models import SiteConfig
from events.models import RSVP, Category, Event
from guide.models import GuidePage
from supper.models import RATING_DIMENSIONS, Restaurant

MAILER_WINDOW_DAYS = 14


# --- shared helpers -----------------------------------------------------------

def _with_pct(rows, key="count", scale=None):
    """Add a 0-100 ``pct`` to each row for the no-JS bar charts (bar-fill width).

    ``scale`` pins the 100% mark (e.g. 5 for star averages); otherwise the
    largest value in the list is full-width.
    """
    top = scale if scale is not None else max((row[key] for row in rows), default=0)
    for row in rows:
        row["pct"] = int(round(100 * row[key] / top)) if top else 0
    return rows


def current_term_start(today=None):
    """Approximate start of the current Cambridge term.

    Michaelmas ~1 Oct, Lent ~15 Jan, Easter ~22 Apr; between terms the most
    recent start is used. Close enough for the "new this term" stat tile.
    """
    today = today or timezone.localdate()
    starts = [
        datetime.date(today.year, 1, 15),
        datetime.date(today.year, 4, 22),
        datetime.date(today.year, 10, 1),
    ]
    past = [d for d in starts if d <= today]
    return past[-1] if past else datetime.date(today.year - 1, 10, 1)


# --- dashboard counts -----------------------------------------------------------

def home_counts():
    """Counts for the admin dashboard tiles and action-needed cards."""
    now = timezone.now()
    horizon = now + datetime.timedelta(days=14)
    return {
        "pending_waitlist": WaitlistRequest.objects.filter(
            status=WaitlistRequest.Status.PENDING
        ).count(),
        "open_whatsapp": WhatsAppAccessRequest.objects.filter(
            status=WhatsAppAccessRequest.Status.OPEN
        ).count(),
        "total_members": User.objects.filter(is_banned=False).count(),
        "members_this_term": User.objects.filter(
            is_banned=False, created_at__date__gte=current_term_start()
        ).count(),
        "events_14d": Event.objects.filter(
            is_cancelled=False, start__range=(now, horizon)
        ).count(),
        "rsvps_14d": RSVP.objects.filter(
            status=RSVP.Status.GOING,
            event__is_cancelled=False,
            event__start__range=(now, horizon),
        ).count(),
    }


# --- statistics page --------------------------------------------------------------

def stats_summary():
    """Headline numbers for the statistics page stat tiles."""
    now = timezone.now()
    horizon = now + datetime.timedelta(days=14)
    members = User.objects.filter(is_banned=False)
    return {
        "total_members": members.count(),
        "raven_members": members.filter(account_type=User.AccountType.RAVEN).count(),
        "associate_members": members.filter(
            account_type=User.AccountType.ASSOCIATE
        ).count(),
        "new_30d": members.filter(
            created_at__gte=now - datetime.timedelta(days=30)
        ).count(),
        "pending_waitlist": WaitlistRequest.objects.filter(
            status=WaitlistRequest.Status.PENDING
        ).count(),
        "total_events": Event.objects.filter(is_cancelled=False).count(),
        "events_14d": Event.objects.filter(
            is_cancelled=False, start__range=(now, horizon)
        ).count(),
        "total_rsvps": RSVP.objects.filter(status=RSVP.Status.GOING).count(),
        "guide_pages": GuidePage.objects.filter(is_published=True).count(),
        "restaurants_rated": Restaurant.objects.filter(
            visits__restaurant_ratings__isnull=False
        ).distinct().count(),
    }


def members_by_college(limit=12):
    """Top colleges by (non-banned) member count, with display names."""
    rows = (
        User.objects.filter(is_banned=False)
        .exclude(college="")
        .values("college")
        .annotate(count=Count("id"))
        .order_by("-count", "college")[:limit]
    )
    names = dict(User._meta.get_field("college").choices)
    return _with_pct(
        [{"label": names.get(r["college"], r["college"]), "count": r["count"]} for r in rows]
    )


def events_by_category():
    """All-time event counts per category (cancelled events excluded)."""
    rows = (
        Category.objects.annotate(
            count=Count("events", filter=Q(events__is_cancelled=False))
        )
        .filter(count__gt=0)
        .order_by("-count", "sort_order")
    )
    return _with_pct(
        [{"label": f"{c.emoji} {c.name}".strip(), "count": c.count} for c in rows]
    )


def rsvps_per_month(months=6):
    """GOING RSVPs created per calendar month, oldest first, zero-filled."""
    now = timezone.localtime()
    keys = []
    year, month = now.year, now.month
    for _ in range(months):
        keys.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    keys.reverse()
    start = timezone.make_aware(datetime.datetime(keys[0][0], keys[0][1], 1))
    counts = {}
    rows = (
        RSVP.objects.filter(status=RSVP.Status.GOING, created_at__gte=start)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(count=Count("id"))
    )
    for row in rows:
        local = timezone.localtime(row["month"])
        counts[(local.year, local.month)] = counts.get((local.year, local.month), 0) + row["count"]
    return _with_pct(
        [
            {
                "label": date_format(datetime.date(y, m, 1), "M Y"),
                "count": counts.get((y, m), 0),
            }
            for y, m in keys
        ]
    )


def top_hosts(limit=8):
    """Members who have created the most (non-cancelled) events."""
    rows = (
        User.objects.annotate(
            count=Count("events_created", filter=Q(events_created__is_cancelled=False))
        )
        .filter(count__gt=0)
        .order_by("-count", "first_name", "last_name")[:limit]
    )
    return _with_pct(
        [{"label": u.get_full_name() or u.username, "count": u.count} for u in rows]
    )


def top_restaurants(limit=5):
    """Top restaurants by overall average rating (mean of the four dimensions)."""
    annotations = {
        f"{key}_avg": Avg(f"visits__restaurant_ratings__{key}")
        for key, _ in RATING_DIMENSIONS
    }
    rows = (
        Restaurant.objects.annotate(
            ratings_count=Count("visits__restaurant_ratings"), **annotations
        ).filter(ratings_count__gt=0)
    )
    results = []
    for restaurant in rows:
        overall = sum(
            getattr(restaurant, f"{key}_avg") for key, _ in RATING_DIMENSIONS
        ) / len(RATING_DIMENSIONS)
        results.append(
            {
                "label": restaurant.name,
                "overall": overall,
                "count": restaurant.ratings_count,
            }
        )
    results.sort(key=lambda row: (-row["overall"], row["label"]))
    return _with_pct(results[:limit], key="overall", scale=5)


# --- What's On mailer ----------------------------------------------------------

def whats_on_events():
    """Events in the mailer window: next 14 days, not cancelled.

    Members-only events are included — the mailing list is members.
    """
    return (
        Event.objects.in_next_days(MAILER_WINDOW_DAYS)
        .filter(is_cancelled=False)
        .select_related("category")
    )


def build_whats_on_email(request):
    """Draft the "What's On" mailer. Returns ``(subject, body)``, body plain text.

    Official events lead in their own section; everything else follows
    chronologically. RSVP links are absolute URLs so they work in inboxes.
    """
    config = SiteConfig.get()
    today = timezone.localdate()
    until = today + datetime.timedelta(days=MAILER_WINDOW_DAYS)
    subject = (
        f"{config.short_name}: What's On — "
        f"{date_format(today, 'j M')} to {date_format(until, 'j M')}"
    )

    events = list(whats_on_events())
    official = [e for e in events if e.is_official]
    other = [e for e in events if not e.is_official]

    lines = [
        "Hello all,",
        "",
        f"Here's what's on with the {config.society_name} over the next two weeks.",
        "",
    ]
    if not events:
        lines += [
            "Nothing in the diary yet — keep an eye on the calendar, or add",
            "something yourself!",
            "",
        ]
    if official:
        lines += ["⭐ OFFICIAL EVENTS", "-----------------", ""]
        for event in official:
            lines += _event_lines(request, event, official=True)
    if other:
        lines += ["ALSO ON", "-------", ""]
        for event in other:
            lines += _event_lines(request, event, official=False)
    lines += [
        "Full calendar (and where to add your own events):",
        _absolute_url(request, "events:calendar", fallback="/events/"),
        "",
        f"Questions, ideas or something to plug? Write to {config.contact_email}.",
        "",
        f"— The {config.short_name} committee",
    ]
    return subject, "\n".join(lines)


def _event_lines(request, event, official):
    start = timezone.localtime(event.start)
    tag = f"{event.category.emoji} {event.category.name}".strip()
    title = f"★ [OFFICIAL] {event.title}" if official else f"• {event.title}"
    lines = [title, f"  {tag} — {date_format(start, 'D j M, H:i')}"]
    if event.location:
        lines.append(f"  📍 {event.location}")
    lines.append(
        "  RSVP: "
        + _absolute_url(
            request, "events:detail", args=[event.pk], fallback=f"/events/{event.pk}/"
        )
    )
    lines.append("")
    return lines


def _absolute_url(request, name, args=None, fallback="/"):
    """Absolute URL for a named route; falls back to a literal path while the
    other apps' URLconfs are still being built."""
    try:
        path = reverse(name, args=args)
    except NoReverseMatch:
        path = fallback
    return request.build_absolute_uri(path)
