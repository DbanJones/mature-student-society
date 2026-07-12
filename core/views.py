"""Public pages: homepage, About Us, Wellbeing, Community Policies.

All four pages are readable without logging in. The homepage only ever shows
events through ``Event.objects.visible_to(request.user)`` so members_only
events never leak to anonymous visitors.
"""

from django.db.models import Count, Q
from django.shortcuts import render

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


def about(request):
    return render(request, "core/about.html", {
        "nav_active": "about",
        "committee": COMMITTEE,
    })


def wellbeing(request):
    return render(request, "core/wellbeing.html")


def policies(request):
    return render(request, "core/policies.html")
