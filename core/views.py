"""Public pages: homepage, About Us, Wellbeing, Community Policies.

All four pages are readable without logging in. The homepage only ever shows
events through ``Event.objects.visible_to(request.user)`` so members_only
events never leak to anonymous visitors.
"""

from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from core.models import SitePage
from events.models import RSVP, Event
from guide.models import GuidePage
from supper.models import Restaurant

# "What We Do" — migrated from the old site (CONTENT.md).
ACTIVITIES = [
    ("🏫", "College lunches & tours", "Get to know the University's 31 colleges."),
    ("🍺", "Pub nights", "Explore Cambridge's pub scene."),
    ("🕯️", "Formals and formal-swaps", "The traditional Cambridge dining experience."),
    ("🎤", "Research “flashtalks”", "Present your research and listen to others."),
    ("💬", "50+ sub-groups", "From parenting lawyers to LGBT+."),
    ("🤝", "Networking", "Events for making useful contacts."),
    ("🥾", "Walks & running", "Explore the local area, jog together."),
    ("🍽️", "Supper Clubs", "Cambridge's range of international cheap-eat cuisines."),
    ("☕", "Wellbeing coffee meets", "Share concerns and help others."),
    ("🖼️", "Outings", "Museum/gallery trips and getting out of Cambridge."),
    ("📣", "Advice and advocacy", "Hive-mind help and official representation."),
]

# Committee 2026-27 — migrated from the old site (CONTENT.md).
COMMITTEE = [
    ("Basma Al Ghamdi", "Undergrad liaison"),
    ("David Jones", "Tech"),
    ("Charlotte York", "Clubs/Events"),
    ("Samantha Neville", "Partners' liaison"),
    ("Tricia Postle", "Communications"),
    ("Oliver Samuel", "Postgrad liaison"),
    ("Richard Senior", "Treasurer Emeritus"),
    ("Tricia Sutton", "Staff/Well-being"),
    ("Barbara Toninato", "Staff liaison"),
    ("Rob Kelsey", "Treasurer"),
    ("Georgina Glasby", "External Relations"),
    ("Jess Mann", "Well-being"),
    ("Jen Greggs", "Events/alumni liaison"),
]


def home(request):
    """The society's front door: hero, next events, activities, teasers."""
    next_events = (
        Event.objects.visible_to(request.user)
        .upcoming()
        .select_related("category")
        .annotate(
            num_going=Count("rsvps", filter=Q(rsvps__status=RSVP.Status.GOING))
        )
        .order_by("-is_official", "start")[:4]
    )

    guide_pages = (
        GuidePage.objects.filter(is_published=True)
        .order_by("-updated_at")[:3]
    )

    # Top-rated Supper Club restaurants (only ones that have been rated).
    rated = []
    for restaurant in Restaurant.objects.all():
        summary = restaurant.rating_summary(request.user)
        if summary:
            rated.append({"restaurant": restaurant, "summary": summary})
    rated.sort(key=lambda item: item["summary"]["overall"], reverse=True)

    return render(request, "core/home.html", {
        "next_events": next_events,
        "activities": ACTIVITIES,
        "guide_pages": guide_pages,
        "top_restaurants": rated[:3],
    })


def winter_ball(request):
    """The Winter Ball's own page — the society's flagship night.

    If a Winter Ball event exists on the calendar, the page wires its RSVP
    button (and capacity counter) to it; otherwise it renders as a save-the-
    date page.
    """
    ball = (
        Event.objects.visible_to(request.user)
        .filter(title__icontains="winter ball")
        .upcoming()
        .first()
    )
    going_count = spots_left = None
    is_going = False
    if ball:
        going_count = ball.going_count
        if ball.capacity:
            spots_left = max(0, ball.capacity - going_count)
        rsvp = ball.user_rsvp(request.user)
        is_going = bool(rsvp and rsvp.status == RSVP.Status.GOING)
    return render(request, "core/winter_ball.html", {
        "nav_active": "ball",
        "ball": ball,
        "going_count": going_count,
        "spots_left": spots_left,
        "is_going": is_going,
    })


def about(request):
    return render(request, "core/about.html", {
        "nav_active": "about",
        "committee": COMMITTEE,
    })


def site_page(request, slug):
    """An admin-managed CMS page (see panel → Content)."""
    page = get_object_or_404(SitePage, slug=slug)
    if not page.is_published and not (
        request.user.is_authenticated and request.user.is_portal_admin
    ):
        raise Http404("No page found.")
    return render(request, "core/site_page.html", {
        "nav_active": f"page-{page.slug}",
        "page": page,
    })


def wellbeing(request):
    return render(request, "core/wellbeing.html")


def policies(request):
    return render(request, "core/policies.html")
