"""The Winter Ball page and the calendar event behind it.

One event at a time is "the Winter Ball". Links to that event around the
site open the Winter Ball page instead of the ordinary event page, for
anyone who can see the Winter Ball tab.
"""

import datetime

from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfig, tab_visible_to
from events.models import Event

TITLE_MATCH = "winter ball"
# The page keeps showing the event until the morning after.
GRACE = datetime.timedelta(hours=12)


def candidates():
    """Events that could be the Winter Ball: not cancelled, not a formal
    listing, not over. Plain comparisons on aware datetimes only: the
    SRCF's MySQL has no timezone tables."""
    return (
        Event.objects
        .filter(
            is_cancelled=False,
            formal_listing__isnull=True,
            created_by__is_shadow_banned=False,
            start__gte=timezone.now() - GRACE,
        )
        .exclude(category__slug="formal-exchange")
        .select_related("category")
        .order_by("start")
    )


def find_event(config=None):
    """The admin's choice if it is still a candidate; otherwise the next
    super event with "Winter Ball" in its title; otherwise None."""
    config = config or SiteConfig.get()
    if config.ball_event_id:
        chosen = candidates().filter(pk=config.ball_event_id).first()
        if chosen:
            return chosen
    return candidates().filter(is_super=True, title__icontains=TITLE_MATCH).first()


def current(request=None, config=None):
    """The Winter Ball event, looked up once per request."""
    if request is None:
        return find_event(config)
    if not hasattr(request, "_ball_event"):
        request._ball_event = find_event(config)
    return request._ball_event


def page_for(event, user=None, *, audience=None, request=None, config=None):
    """The Winter Ball page's address if links to ``event`` should open it
    for this viewer, else "".

    ``audience`` stands in for a reader we cannot know: "public" for a
    link anyone may follow (a WhatsApp share), "members" for the mailer.
    """
    if event is None:
        return ""
    ball = current(request, config)
    if ball is None or ball.pk != event.pk:
        return ""
    config = config or SiteConfig.get()
    visibility = config.tab_visibility_for("ball")
    if audience == "public":
        shown = visibility == "public"
    elif audience == "members":
        shown = visibility in ("public", "members")
    else:
        shown = tab_visible_to(config, "ball", user or AnonymousUser())
    return reverse("core:winter_ball") if shown else ""


def event_url(event, user=None, *, audience=None, request=None, config=None):
    """Where a link to ``event`` should go."""
    return (
        page_for(event, user, audience=audience, request=request, config=config)
        or event.get_absolute_url()
    )
