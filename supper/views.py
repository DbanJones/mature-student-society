from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from events.models import RSVP, Event

from .forms import RatingForm
from .models import Rating, Restaurant

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _next_supper(user):
    """The next upcoming Supper Club outing visible to this viewer."""
    return (
        Event.objects.visible_to(user)
        .upcoming()
        .filter(category__has_restaurant_ratings=True)
        .select_related("restaurant", "category")
        .first()
    )


def index(request):
    """Public leaderboard: every restaurant ranked by overall average.

    Anyone may browse the scores; only members who attended a visit can vote
    (enforced in ``rate``). Aggregate maths lives on the model.
    """
    rated, unrated = [], []
    for restaurant in Restaurant.objects.all():
        summary = restaurant.rating_summary(request.user)
        if summary:
            rated.append({"restaurant": restaurant, "summary": summary})
        else:
            unrated.append(restaurant)
    rated.sort(key=lambda row: (-row["summary"]["overall"], row["restaurant"].name.lower()))
    for rank, row in enumerate(rated, start=1):
        row["rank"] = rank
        row["medal"] = MEDALS.get(rank)

    return render(request, "supper/index.html", {
        "nav_active": "supper",
        "next_event": _next_supper(request.user),
        "rated": rated,
        "unrated": unrated,
    })


def restaurant(request, pk):
    """Public restaurant page: aggregate scorecard, visits, every rating.

    Visits (and the ratings shown with them) are limited to events visible to
    the viewer, so members-only outings stay off the public page.
    """
    restaurant = get_object_or_404(Restaurant, pk=pk)
    visits = (
        Event.objects.visible_to(request.user)
        .filter(restaurant=restaurant)
        .select_related("category")
        .annotate(rating_count=Count("restaurant_ratings"))
        .order_by("-start")
    )
    ratings = (
        Rating.objects.filter(event__in=visits)
        .select_related("user", "event")
        .order_by("-event__start", "-created_at")
    )

    # Past visits the viewer attended but hasn't scored yet -> prompt card.
    rate_prompts = []
    if request.user.is_authenticated:
        rate_prompts = list(
            Event.objects.filter(
                restaurant=restaurant,
                is_cancelled=False,
                start__lte=timezone.now(),
                rsvps__user=request.user,
                rsvps__status=RSVP.Status.GOING,
            )
            .exclude(restaurant_ratings__user=request.user)
            .order_by("-start")
        )

    return render(request, "supper/restaurant.html", {
        "nav_active": "supper",
        "restaurant": restaurant,
        "summary": restaurant.rating_summary(request.user),
        "visits": visits,
        "ratings": ratings,
        "rate_prompts": rate_prompts,
    })


@login_required
def rate(request, event_pk):
    """Create or update the viewer's rating for one Supper Club visit.

    Guards, in order:
    1. The event must exist, link a restaurant, and be in a Supper Club
       category (``has_restaurant_ratings``) — otherwise 404.
    2. The dinner must have started — otherwise redirect with a message.
    3. The viewer must have a GOING RSVP — otherwise a friendly 403.
    """
    event = get_object_or_404(
        Event.objects.select_related("restaurant", "category"),
        pk=event_pk,
        restaurant__isnull=False,
        category__has_restaurant_ratings=True,
        is_cancelled=False,
    )

    if timezone.now() < event.start:
        messages.info(request, "You can rate after the dinner — enjoy it first!")
        return redirect("supper:restaurant", pk=event.restaurant_id)

    attended = event.rsvps.filter(
        user=request.user, status=RSVP.Status.GOING
    ).exists()
    if not attended:
        return render(request, "supper/not_attendee.html", {
            "nav_active": "supper",
            "event": event,
            "next_event": _next_supper(request.user),
        }, status=403)

    existing = Rating.objects.filter(event=event, user=request.user).first()
    if request.method == "POST":
        form = RatingForm(request.POST, instance=existing)
        if form.is_valid():
            rating = form.save(commit=False)
            rating.event = event
            rating.user = request.user
            rating.save()
            overall = event.restaurant.rating_summary(request.user)["overall"]
            if existing:
                messages.success(
                    request,
                    f"Rating updated — {event.restaurant.name} now averages "
                    f"{overall:.1f}★ overall.",
                )
            else:
                messages.success(
                    request,
                    f"Thanks for rating! {event.restaurant.name} now averages "
                    f"{overall:.1f}★ overall.",
                )
            return redirect("supper:restaurant", pk=event.restaurant_id)
    else:
        form = RatingForm(instance=existing)

    return render(request, "supper/rate.html", {
        "nav_active": "supper",
        "event": event,
        "restaurant": event.restaurant,
        "form": form,
        "is_update": existing is not None,
    })
