"""Public pages: homepage, About Us, Wellbeing, Community Policies, and the
Terms and Conditions.

All are readable without logging in. The homepage only ever shows
events through ``Event.objects.visible_to(request.user)`` so members_only
events never leak to anonymous visitors.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core.forms import SitePageContentForm
from core.models import (
    Activity,
    CommitteeMember,
    SitePage,
    SitePageRevision,
    TermsVersion,
)
from events.models import RSVP, Event
from guide.models import GuidePage
from supper.models import Restaurant
from testimonials.models import Testimonial

# The SitePage slug holding the editable body of the Wellbeing page.
WELLBEING_SLUG = "wellbeing"

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
        .by_promotion()[:4]
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

    activities = [(a.emoji, a.name, a.blurb) for a in Activity.objects.all()] or ACTIVITIES
    return render(request, "core/home.html", {
        "next_events": next_events,
        "activities": activities,
        "guide_pages": guide_pages,
        "top_restaurants": rated[:3],
        "testimonials": Testimonial.objects.approved().featured_first()[:3],
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
    committee = [
        (m.name, m.role) for m in CommitteeMember.objects.filter(is_active=True)
    ] or COMMITTEE
    return render(request, "core/about.html", {
        "nav_active": "about",
        "committee": committee,
    })


# SitePages that also back a fixed URL of their own. They are edited in
# panel → Content like any other page, but are served from their canonical
# path, so /pages/<slug>/ redirects there rather than publishing the same
# content at two addresses.
CANONICAL_PAGE_SLUGS = {"wellbeing": "core:wellbeing"}


def _page_url(page):
    """Where a page lives: its canonical path if it has one, else /pages/."""
    if page.slug in CANONICAL_PAGE_SLUGS:
        return reverse(CANONICAL_PAGE_SLUGS[page.slug])
    return page.get_absolute_url()


def site_page(request, slug):
    """An admin-managed CMS page (see panel → Content → Pages).

    The page's audience applies here, not just to its navigation link: a
    members-only page 404s for anonymous visitors.
    """
    if slug in CANONICAL_PAGE_SLUGS:
        return redirect(CANONICAL_PAGE_SLUGS[slug], permanent=True)
    page = get_object_or_404(SitePage, slug=slug)
    preview_as = request.GET.get("as") if (
        request.user.is_authenticated and request.user.is_portal_admin
    ) else None
    if preview_as in ("public", "member"):
        viewer = _PreviewViewer(authenticated=preview_as == "member")
        return render(request, "core/site_page.html", {
            "nav_active": f"page-{page.slug}",
            "page": page,
            "can_edit": False,
            "preview_as": preview_as,
            "preview_visible": page.is_visible_to(viewer),
        })
    if not page.is_visible_to(request.user):
        raise Http404("No page found.")
    return render(request, "core/site_page.html", {
        "nav_active": f"page-{page.slug}",
        "page": page,
        "can_edit": page.can_edit(request.user),
    })


class _PreviewViewer:
    """Stands in for a visitor of another audience when an admin previews
    a page. Only what ``SitePage.is_visible_to`` looks at."""

    is_portal_admin = False

    def __init__(self, authenticated):
        self.is_authenticated = authenticated


def wellbeing(request):
    """The Wellbeing page.

    The body is an admin-editable SitePage (slug ``wellbeing``) so the
    committee can change it from panel → Content without a deploy. The page
    header and the crisis-support panel stay in the template: those are the
    parts nobody should be able to delete by accident.
    """
    page = SitePage.objects.filter(slug=WELLBEING_SLUG).first()
    if page and not page.is_visible_to(request.user):
        page = None
    return render(request, "core/wellbeing.html", {
        "page": page,
        "can_edit": page.can_edit(request.user) if page else False,
    })


def policies(request):
    return render(request, "core/policies.html")


def terms(request):
    """The current terms and conditions, readable by anyone.

    Public so that people can read what they will be agreeing to before they
    request an account — and so the accept page has something to link to.
    """
    return render(request, "core/terms.html", {
        "terms": TermsVersion.current(),
    })


# --- editing admin-managed pages ----------------------------------------------
# Admins and a page's named editors edit from the page itself; every save is
# snapshotted so the history page can show, and restore, any earlier state.


def tell_page_editors(page, actor):
    """Everyone who looks after a page hears when someone else changes it."""
    from notifications.models import Notification
    from notifications.services import notify

    who = actor.get_full_name() or actor.username
    notify(
        page.editors.all(), Notification.Kind.PAGE,
        f"“{page.title}” was edited by {who}.",
        reverse("core:site_page_history", args=[page.slug]),
        exclude=[actor],
    )


def _editable_page_or_403(request, slug):
    page = get_object_or_404(SitePage, slug=slug)
    if not page.can_edit(request.user):
        raise PermissionDenied("Only this page's editors or an admin can edit it.")
    return page


@login_required
def site_page_edit(request, slug):
    page = _editable_page_or_403(request, slug)
    form = SitePageContentForm(request.POST or None, instance=page)
    previewing = False
    if request.method == "POST":
        if "preview" in request.POST:
            previewing = True
        elif form.is_valid():
            page = form.save(commit=False)
            page.updated_by = request.user
            page.save()
            page.save_revision(request.user, SitePageRevision.Action.EDITED)
            tell_page_editors(page, request.user)
            messages.success(request, f"Saved “{page.title}”.")
            return redirect(_page_url(page))
    return render(request, "core/site_page_form.html", {
        "nav_active": f"page-{page.slug}",
        "page": page,
        "page_url": _page_url(page),
        "form": form,
        "previewing": previewing,
        "preview_content": request.POST.get("content", "") if previewing else "",
    })


@login_required
def site_page_history(request, slug):
    page = _editable_page_or_403(request, slug)
    revisions = list(page.revisions.select_related("editor"))
    for i, rev in enumerate(revisions):
        older_len = len(revisions[i + 1].content) if i + 1 < len(revisions) else 0
        rev.delta = len(rev.content) - older_len
    return render(request, "core/site_page_history.html", {
        "nav_active": f"page-{page.slug}",
        "page": page,
        "page_url": _page_url(page),
        "revisions": revisions,
    })


@login_required
def site_page_revision(request, slug, revision_id):
    page = _editable_page_or_403(request, slug)
    rev = get_object_or_404(
        SitePageRevision.objects.select_related("editor"),
        pk=revision_id, page=page,
    )
    latest = page.revisions.first()
    return render(request, "core/site_page_revision.html", {
        "nav_active": f"page-{page.slug}",
        "page": page,
        "page_url": _page_url(page),
        "revision": rev,
        "is_current": latest is not None and latest.pk == rev.pk,
    })


@login_required
@require_POST
def site_page_restore(request, slug, revision_id):
    """Put an earlier title and body back: just an edit that copies the old
    text, snapshotted like any other save."""
    page = _editable_page_or_403(request, slug)
    rev = get_object_or_404(SitePageRevision, pk=revision_id, page=page)
    page.title = rev.title
    page.content = rev.content
    page.updated_by = request.user
    page.save()
    page.save_revision(request.user, SitePageRevision.Action.RESTORED)
    when = date_format(timezone.localtime(rev.created_at), "j M Y, H:i")
    messages.success(request, f"Restored the version from {when}.")
    return redirect(_page_url(page))


# --- site-wide search ---------------------------------------------------------


def search(request):
    """One box for events, the Guide, managed pages and (for members) people."""
    from accounts.views import _members_visible_to
    from events.models import Event
    from guide.views import _search_guide

    q = request.GET.get("q", "").strip()[:80]
    results = {"events": [], "guide": [], "pages": [], "members": []}
    if q:
        results["events"] = list(
            Event.objects.visible_to(request.user).search(q)
            .select_related("category").order_by("-start")[:10]
        )
        results["guide"] = _search_guide(
            GuidePage.objects.filter(is_published=True), q
        )[:10]
        results["pages"] = [
            page for page in SitePage.objects.filter(
                Q(title__icontains=q) | Q(content__icontains=q)
            )[:20]
            if page.is_visible_to(request.user)
        ][:10]
        if request.user.is_authenticated:
            results["members"] = list(
                _members_visible_to(request.user).filter(
                    Q(first_name__icontains=q) | Q(last_name__icontains=q)
                    | Q(course__icontains=q) | Q(college__icontains=q)
                    | Q(work__icontains=q) | Q(interests__icontains=q)
                    | Q(talk_to_me_about__icontains=q)
                ).order_by("first_name", "last_name")[:10]
            )
    total = sum(len(v) for v in results.values())
    return render(request, "core/search.html", {
        "nav_active": "search", "q": q, "results": results, "total": total,
    })


@require_POST
def dismiss_banner(request):
    """Hide the announcement banner for the rest of this session."""
    from core.context_processors import banner_key

    request.session["banner_dismissed"] = banner_key()
    next_url = request.POST.get("next", "")
    from django.utils.http import url_has_allowed_host_and_scheme
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
        return redirect(next_url)
    return redirect("core:home")


@login_required
def site_page_diff(request, slug, revision_id):
    """What one save changed, compared with the save before it (or ``?against=``)."""
    from core.diff import line_diff, summary

    page = _editable_page_or_403(request, slug)
    newer = get_object_or_404(SitePageRevision, pk=revision_id, page=page)
    against = request.GET.get("against")
    if against and against.isdigit():
        older = get_object_or_404(SitePageRevision, pk=against, page=page)
    else:
        older = (
            page.revisions.filter(created_at__lt=newer.created_at).order_by("-created_at").first()
        )
    old_text = older.content if older else ""
    rows = line_diff(old_text, newer.content)
    return render(request, "core/site_page_diff.html", {
        "nav_active": f"page-{page.slug}",
        "page": page,
        "older": older or newer,
        "newer": newer,
        "rows": rows,
        "diff_summary": summary(rows),
    })
