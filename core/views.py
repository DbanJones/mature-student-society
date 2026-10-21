"""Public pages: homepage, About Us, Wellbeing, Community Policies, and the
Terms and Conditions.

All are readable without logging in. The homepage only ever shows
events through ``Event.objects.visible_to(request.user)`` so members_only
events never leak to anonymous visitors.
"""

import datetime
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core.forms import SitePageContentForm
from core.models import (
    Activity,
    CommitteeMember,
    SiteConfig,
    SitePage,
    SitePageRevision,
    TermsVersion,
)
from core.net import absolute_url, client_ip
from core.richtext import richtext_problem
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
        .select_related("category", "formal_listing")
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


PREVIEWS_PER_MINUTE = 40


@login_required
@require_POST
def preview_markdown(request):
    """Rendered HTML for the live preview in the editors, sanitised exactly
    as the pages themselves are. Input that would take the renderer too
    long is refused, and each member gets a modest number of previews a
    minute, so the preview can never tie up the server."""
    from .templatetags.md import richtext_filter

    text = request.POST.get("text", "")
    problem = richtext_problem(text)
    if problem:
        return HttpResponse(f"Can't preview: {problem}.", status=413, content_type="text/plain; charset=utf-8")
    key = f"preview-rate:{request.user.pk}"
    cache.add(key, 0, 60)
    try:
        count = cache.incr(key)
    except ValueError:  # the minute ended between the two calls
        cache.set(key, 1, 60)
        count = 1
    if count > PREVIEWS_PER_MINUTE:
        return HttpResponse("Too many previews in a minute; wait a moment.", status=429,
                            content_type="text/plain; charset=utf-8")
    return HttpResponse(richtext_filter(text))


def _preview_text(request):
    """The POSTed content for a preview, unless rendering it would be unsafe."""
    text = request.POST.get("content", "")
    problem = richtext_problem(text)
    if problem:
        messages.error(request, f"Can't preview: {problem}.")
        return ""
    return text


UNSUBSCRIBES_PER_MINUTE = 10
RESUBSCRIBE_SALT = "mailer-resubscribe"
RESUBSCRIBE_DAYS = 90
logger = logging.getLogger(__name__)


def _same_mailbox(email):
    """The address and, for a Cambridge one, its @hermes twin (one mailbox)."""
    from panel.models import OldSubscriber

    crsid = OldSubscriber(email=email).crsid
    return [f"{crsid}@{domain}" for domain in OldSubscriber.CRSID_DOMAINS] if crsid else [email]


def _any_email(emails):
    query = Q(pk__in=[])
    for address in emails:
        query |= Q(email__iexact=address)
    return query


def _allow(key, limit, seconds):
    """A simple counter in the cache: True while ``key`` has been used fewer
    than ``limit`` times in the last ``seconds``."""
    cache.add(key, 0, seconds)
    try:
        return cache.incr(key) <= limit
    except ValueError:  # the window ended between the two calls
        cache.set(key, 1, seconds)
        return True


def _only_on_the_list(email):
    """Someone on the society's list who has no account and isn't on the old
    list: the website's mailer never reached them, but the page promised the
    committee would take them off the list, so their address waits on Admin
    > People > Mailing list. As with everything here, the address itself is
    told (anyone can type any address in)."""
    from panel import listsync
    from panel.models import ListMember, ListSync

    if not ListMember.objects.filter(key=listsync.mailbox_key(email)).exists():
        return
    waiting = ListSync.objects.filter(kind=ListSync.Kind.REMOVE, done_at__isnull=True).values_list("addresses", flat=True)
    if any(email in addresses for addresses in waiting):
        return  # already waiting
    if listsync.queue_removal([], None, "someone asked to leave on the website's unsubscribe page",
                              addresses=[email]):
        try:
            send_mail(
                "You asked to leave the society's mailing list",
                f"Someone asked, on the website's unsubscribe page, for this address ({email}) to come off the "
                f"society's mailing list. The committee will take it off when it next tidies the list.\n\n"
                f"If that wasn't you, reply to this email and they'll leave you on it.\n",
                None, [email],
            )
        except Exception:
            logger.warning("Could not send the list-leaving confirmation", exc_info=True)


def unsubscribe(request):
    """Stop the What's On mailer for an address: a member's profile setting
    and the old mailing list both. Anyone can ask for any address (there
    is no login), so the address itself is told by email about every
    change, with a link that undoes exactly what was changed; an address on
    neither list gets the same page and no mail."""
    from core.mailinglist import leave_note
    from panel.models import OldSubscriber

    done, email = False, ""
    list_note = leave_note(SiteConfig.get().mailing_list_address)
    if request.method == "POST":
        if not _allow(f"unsubscribe-rate:{client_ip(request)}", UNSUBSCRIBES_PER_MINUTE, 60):
            return HttpResponse("Too many requests; try again in a minute.", status=429,
                                content_type="text/plain; charset=utf-8")
        email = request.POST.get("email", "").strip().lower()
        if "@" in email:
            # A Cambridge address and its @hermes twin are the same person.
            emails = _same_mailbox(email)
            # Accounts are matched by the address itself, never by CRSid alone:
            # a member who reads the mailer at another address keeps it.
            users = get_user_model().objects.filter(wants_mailer=True).filter(_any_email(emails))
            user_ids = list(users.values_list("pk", flat=True))
            users.update(wants_mailer=False)
            now = timezone.now()
            rows = OldSubscriber.objects.filter(email__in=emails, unsubscribed_at__isnull=True)
            row_ids = list(rows.values_list("pk", flat=True))
            rows.update(unsubscribed_at=now)
            if not user_ids and not row_ids and list_note:
                _only_on_the_list(email)
            if user_ids or row_ids:
                token = signing.dumps(
                    {"e": email, "t": now.isoformat(), "u": user_ids, "o": row_ids},
                    salt=RESUBSCRIBE_SALT, compress=True,
                )
                undo = absolute_url(request, reverse("core:resubscribe", args=[token]))
                also = f"{list_note}\n\n" if list_note else ""
                try:
                    send_mail(
                        "You've been unsubscribed from the What's On mailer",
                        f"This address ({email}) won't get the website's What's On mailer any more.\n\n"
                        f"{also}"
                        f"If that wasn't you, or you change your mind, put it back with this link "
                        f"(it works for {RESUBSCRIBE_DAYS} days):\n{undo}\n",
                        None, [email],
                    )
                except Exception:
                    logger.warning("Could not send the unsubscribe confirmation", exc_info=True)
            done = True
    return render(request, "core/unsubscribe.html", {"done": done, "email": email, "list_note": list_note})


def resubscribe(request, token):
    """The undo link from the unsubscribe email: puts back exactly what that
    unsubscribe changed."""
    from panel.models import OldSubscriber

    try:
        data = signing.loads(token, salt=RESUBSCRIBE_SALT, max_age=RESUBSCRIBE_DAYS * 24 * 3600)
        email = data["e"]
    except (signing.BadSignature, KeyError, TypeError):
        return render(request, "core/unsubscribe.html", {"expired": True}, status=400)
    if request.method == "POST":
        # Only what that unsubscribe changed, and only if it's still that way:
        # an account still opted out at that address, a row still carrying
        # that unsubscribe's time. Using the link twice changes nothing more.
        emails = _same_mailbox(email)
        get_user_model().objects.filter(pk__in=data.get("u") or [], wants_mailer=False).filter(
            _any_email(emails)).update(wants_mailer=True)
        try:
            when = datetime.datetime.fromisoformat(data.get("t", ""))
        except (TypeError, ValueError):
            when = None
        if when is not None:
            OldSubscriber.objects.filter(pk__in=data.get("o") or [], unsubscribed_at=when).update(unsubscribed_at=None)
        messages.success(request, f"{email} is back on the What's On mailer.")
        return redirect("core:home")
    return render(request, "core/unsubscribe.html", {"resubscribe": True, "email": email})


def menu(request):
    """Every section of the site on one page: the menu on a phone, and a
    map of the site for everyone. The lists come from the nav context."""
    return render(request, "core/menu.html", {"nav_active": "menu"})


def winter_ball(request):
    """The Winter Ball's own page — the society's flagship night.

    If a Winter Ball event exists on the calendar, the page wires its RSVP
    button (and capacity counter) to it; otherwise it renders as a save-the-
    date page.
    """
    ball = (
        Event.objects.visible_to(request.user)
        .filter(title__icontains="winter ball", formal_listing__isnull=True)
        .select_related("formal_listing")
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
        "preview_content": _preview_text(request) if previewing else "",
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
            .select_related("category", "formal_listing").order_by("-start")[:10]
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
