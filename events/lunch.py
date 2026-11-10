"""Lunch swaps: a member invites a few others to lunch at their college.

A lunch swap is an ordinary Event with the Lunch Swap tag. This module holds
the few things that are special about one: the tag, how the short form becomes
an event, the notices to the host and the "host one back" nudge. There is no
ledger: nobody is counted or chased.
"""
import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format

from accounts.models import COLLEGES

from .models import LUNCH_SWAP_SLUG, RSVP, Category, Event

TAG_NAME = "Lunch Swap"
TAG_DEFAULTS = {
    "name": TAG_NAME, "emoji": "🥪", "color": "#8a6d0b", "sort_order": 65,
    "description": "Invite members to lunch at your college, and get invited back.",
    "page_content": (
        "## How a lunch swap works\n\n"
        "A member invites a few others to lunch at their college. It is an easy way "
        "to see inside another college and meet people over an ordinary meal.\n\n"
        "- **Hosting:** pick a day, say how many guests you can take and where to "
        "meet. Check your college's guest rules first.\n"
        "- **Going:** take a place on the lunch's page. If your plans change, give "
        "the place back so someone else can have it.\n"
        "- **Swapping:** enjoyed it? Invite people back to lunch at your college. "
        "Nobody keeps score.\n\n"
        "Lunch swaps are for members only. Who pays is agreed for each lunch and "
        "shown on its page."
    ),
}

PLACES_MAX = 6
PLACES_DEFAULT = 2
LUNCH_LENGTH = datetime.timedelta(hours=1)
HOST_EMAILS_PER_HOUR = 6
# 11:00 to 14:30 in quarter hours.
TIMES = [f"{h:02d}:{m:02d}" for h in range(11, 15) for m in (0, 15, 30, 45)][:-1]
TIME_DEFAULT = "12:30"
HOST_COLLEGES = [(key, name) for key, name in COLLEGES if key != "other"]

PAYING = [
    ("own", "Everyone pays for their own"),
    ("pay_back", "I pay on my college card and guests pay me back"),
    ("treat", "My treat"),
]
PAYING_TEXT = {      # what guests read on the lunch's page
    "own": "Everyone pays for their own lunch.",
    "pay_back": "I'll pay on my college card and you pay me back on the day.",
    "treat": "Lunch is on me.",
}


def lunch_swap_tag():
    """The Lunch Swap tag. The migration makes it; this puts it back if it has
    been deleted, and adopts a tag someone made by hand with the same name."""
    tag = Category.objects.filter(slug=LUNCH_SWAP_SLUG).first()
    if tag is not None:
        return tag
    clash = Category.objects.filter(name__iexact=TAG_NAME).first()
    if clash is not None:
        clash.slug = LUNCH_SWAP_SLUG
        clash.save(update_fields=["slug"])
        return clash
    return Category.objects.get_or_create(slug=LUNCH_SWAP_SLUG, defaults=TAG_DEFAULTS)[0]


def college_name(key):
    return dict(COLLEGES).get(key, key)


def college_place(key):
    """"wolfson" -> "Wolfson College"; "trinity-hall" -> "Trinity Hall"."""
    name = college_name(key)
    return name if name.endswith(" Hall") or name == "Peterhouse" else f"{name} College"


def hosted_since(user, when, skip=None):
    """Has this member posted a lunch of their own since then?"""
    lunches = Event.objects.filter(
        category__slug=LUNCH_SWAP_SLUG, created_at__gt=when,
    ).filter(Q(created_by=user) | Q(host=user))
    if skip is not None:
        lunches = lunches.exclude(pk=skip.pk)
    return lunches.exists()


@transaction.atomic
def create_lunch(user, data, back=None):
    """Turn the short form's cleaned data into an ordinary members-only event."""
    start = data["start"]
    who = (user.first_name or user.username).strip()[:60]
    title = f"Lunch at {college_name(data['college'])} with {who}"[:140]
    tag = lunch_swap_tag()
    # A second press of the button should not make a second lunch.
    twin = Event.objects.filter(
        created_by=user, category=tag, start=start, title=title, is_cancelled=False,
    ).first()
    if twin is not None:
        return twin
    description = f"**Paying:** {PAYING_TEXT[data['paying']]}"
    note = (data.get("note") or "").strip()
    if note:
        description += "\n\n" + note
    event = Event.objects.create(
        title=title, category=tag, description=description,
        location=f"{data['meet'].strip()}, {college_place(data['college'])}",
        start=start, end=start + LUNCH_LENGTH, capacity=data["places"],
        created_by=user, host=user, members_only=True, show_attendees=True,
    )
    if back is not None:
        tell_returned(back, event, user)
    return event


def _when(event):
    local = timezone.localtime(event.start)
    return date_format(local, "D j M"), f"{date_format(local, 'l j F')} at {local:%H:%M}"


def tell_host(event, joined=(), left=()):
    """Tell a lunch's host who has taken or given back a place.
    Does nothing for any other kind of event."""
    if not (joined or left) or not event.is_lunch_swap:
        return
    from notifications.models import Notification
    from notifications.services import notify

    host = event.effective_host
    url = event.get_absolute_url()
    day, long_day = _when(event)
    going = event.rsvps.filter(status=RSVP.Status.GOING, user__is_shadow_banned=False).count()
    places = f"{going} of {event.capacity} places taken." if event.capacity else f"{going} coming so far."
    for guest, coming in [(g, True) for g in joined] + [(g, False) for g in left]:
        if guest.pk == host.pk or guest.is_shadow_banned:
            continue
        name = guest.get_full_name() or guest.username
        if coming:
            text = f"{name} is coming to your lunch on {day}. {places}"
            subject = f"{name} is coming to lunch on {day}"
            body = (f'{name} has taken a place at "{event.title}" on {long_day}. {places}\n\n'
                    "The lunch's page shows who is coming:")
        else:
            text = f"{name} can no longer come to your lunch on {day}. {places}"
            subject = f"{name} can no longer come to lunch on {day}"
            body = (f'{name} can no longer come to "{event.title}" on {long_day}. {places}\n\n'
                    "If anyone is on the waiting list, they get the place automatically. "
                    "The lunch's page shows who is coming:")
        recent = Notification.objects.filter(
            recipient=host, kind=Notification.Kind.EVENT, url=url,
            created_at__gte=timezone.now() - datetime.timedelta(hours=1),
        ).count()
        notify(
            [host], Notification.Kind.EVENT, text, url,
            email_subject=subject if recent < HOST_EMAILS_PER_HOUR else None,
            email_body=body,
        )


def tell_returned(original, new_event, guest):
    """A guest has posted a lunch of their own from "Host one back": tell the
    member who had them to lunch. Once only, however many lunches follow."""
    host = original.effective_host
    if host.pk == guest.pk or guest.is_shadow_banned:
        return
    if hosted_since(guest, original.start, skip=new_event):
        return
    from notifications.models import Notification
    from notifications.services import notify

    first = guest.first_name or guest.username
    old_day, _ = _when(original)
    day, long_day = _when(new_event)
    notify(
        [host], Notification.Kind.EVENT,
        f"{first} has invited you back: {new_event.title} on {day}.",
        new_event.get_absolute_url(),
        email_subject=f"{first} is hosting a lunch back",
        email_body=(f'You had {first} to lunch on {old_day}. {first} is now hosting one: '
                    f'"{new_event.title}" on {long_day}. Places go to whoever takes them '
                    "first, so take one soon if you would like to go:"),
    )


def return_target(user, slug):
    """The lunch a member is returning, only if they really were a guest at it."""
    if not slug:
        return None
    lunch = Event.objects.filter(
        slug=slug, category__slug=LUNCH_SWAP_SLUG, is_cancelled=False,
        start__lte=timezone.now(),
        rsvps__user=user, rsvps__status=RSVP.Status.GOING,
    ).select_related("host", "created_by").first()
    if lunch is None or lunch.effective_host.pk == user.pk:
        return None
    return lunch
