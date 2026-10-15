"""Views for the Mature Students Guide — the community wiki.

Reading is public (published pages only for anonymous visitors); any
logged-in member may write or edit pages. Every save snapshots the page
into a GuideRevision, so the history list reads newest-state-first and any
older version can be restored (which is itself just a normal edit).
"""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from core.richtext import richtext_problem

from .forms import GuidePageForm
from .models import SECTIONS, GuidePage, GuideRevision

SECTION_EMOJI = {
    "arriving": "🧳",
    "colleges": "🏛️",
    "study": "📖",
    "money": "💷",
    "family": "👨‍👩‍👧",
    "living": "🏡",
    "social": "🥂",
    "faq": "❓",
}

# Slugs that would shadow guide URLs — never assign them to pages.
RESERVED_SLUGS = {"new"}


def _display_name(user):
    """A member's display name; editors deleted since are 'a former member'."""
    if user is None:
        return "a former member"
    return user.get_full_name() or user.username


def _unique_slug(title):
    """Slugify the title, de-duping with -2, -3... suffixes.

    The base is truncated well under the 150-char slug limit so a suffix
    always fits.
    """
    base = slugify(title)[:140] or "page"
    slug = base
    n = 2
    while slug in RESERVED_SLUGS or GuidePage.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _search_guide(pages, q):
    """Ranked multi-word search with highlighted snippets.

    Every term must appear somewhere in the title or content (AND); results
    are ordered by weighted hit count (title hits count treble). Each result
    carries a plain-text snippet around the first match with the terms
    wrapped in <mark>.
    """
    terms = [t for t in q.split() if t][:6]
    if not terms:
        return []
    cond = Q()
    for term in terms:
        cond &= Q(title__icontains=term) | Q(content__icontains=term)
    highlight = re.compile(
        "(" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + ")",
        re.IGNORECASE,
    )
    results = []
    for page in pages.filter(cond):
        title_l, content_l = page.title.lower(), page.content.lower()
        score = sum(
            3 * title_l.count(t.lower()) + content_l.count(t.lower())
            for t in terms
        )
        results.append({
            "page": page,
            "score": score,
            "snippet": _snippet(page.content, terms, highlight),
            "title_html": mark_safe(highlight.sub(
                r"<mark>\1</mark>", escape(page.title)
            )),
        })
    results.sort(key=lambda r: (-r["score"], r["page"].title))
    return results


def _snippet(content, terms, highlight, radius=110):
    """~2 lines of plain text around the first term hit, terms marked."""
    text = re.sub(r"[#*_`>\[\]|]", " ", content)  # strip markdown syntax
    text = re.sub(r"\(https?://\S+\)", "", text)  # and bare link targets
    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower()
    hits = [low.find(t.lower()) for t in terms]
    pos = min((h for h in hits if h != -1), default=0)
    start, end = max(0, pos - radius), min(len(text), pos + radius * 2)
    # Snap to word boundaries so the snippet doesn't start mid-word.
    if start > 0:
        start = text.find(" ", start) + 1
    excerpt = text[start:end]
    parts = highlight.split(excerpt)  # odd indices are the matched terms
    built = "".join(
        f"<mark>{escape(part)}</mark>" if i % 2 else escape(part)
        for i, part in enumerate(parts)
    )
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return mark_safe(f"{prefix}{built}{suffix}")


def index(request):
    """PUBLIC. Front page: pages grouped by section, or search results (?q=)."""
    q = request.GET.get("q", "").strip()
    pages = GuidePage.objects.filter(is_published=True).select_related("updated_by")
    context = {"nav_active": "guide", "q": q}
    if q:
        context["results"] = _search_guide(pages, q)
    else:
        by_section = {}
        for guide_page in pages:  # Meta.ordering gives section, then title
            by_section.setdefault(guide_page.section, []).append(guide_page)
        context["sections"] = [
            {
                "key": key,
                "label": label,
                "emoji": SECTION_EMOJI.get(key, "📄"),
                "pages": by_section.get(key, []),
            }
            for key, label in SECTIONS
        ]
    return render(request, "guide/index.html", context)


def page(request, slug):
    """PUBLIC. A single guide page.

    Unpublished pages 404 for anonymous visitors but stay visible (with a
    banner) to logged-in members, so drafts can be worked on collaboratively.
    """
    guide_page = get_object_or_404(
        GuidePage.objects.select_related("created_by", "updated_by"), slug=slug
    )
    if not guide_page.is_published and not request.user.is_authenticated:
        raise Http404("No guide page found matching the query.")
    contributor_count = (
        guide_page.revisions.filter(editor__isnull=False)
        .values("editor").distinct().count()
    )
    return render(request, "guide/page.html", {
        "nav_active": "guide",
        "page": guide_page,
        "section_emoji": SECTION_EMOJI.get(guide_page.section, "📄"),
        "last_editor_name": _display_name(guide_page.updated_by),
        "contributor_count": contributor_count,
    })


def _preview_text(request):
    """The POSTed content for a preview, unless rendering it would be unsafe."""
    text = request.POST.get("content", "")
    problem = richtext_problem(text)
    if problem:
        messages.error(request, f"Can't preview: {problem}.")
        return ""
    return text


@login_required
def new(request):
    """Members only. Write a new page; slug auto-generated from the title."""
    form = GuidePageForm(request.POST or None)
    previewing = False
    if request.method == "POST":
        if "preview" in request.POST:
            previewing = True
        elif form.is_valid():
            guide_page = form.save(commit=False)
            guide_page.slug = _unique_slug(guide_page.title)
            guide_page.created_by = request.user
            guide_page.updated_by = request.user
            guide_page.save()
            # Snapshot AFTER saving so the initial revision records the
            # page's first published state.
            guide_page.save_revision(request.user)
            messages.success(
                request, f"Thanks — “{guide_page.title}” has been added to the guide."
            )
            return redirect(guide_page)
    return render(request, "guide/form.html", {
        "nav_active": "guide",
        "form": form,
        "page": None,
        "previewing": previewing,
        "preview_content": _preview_text(request) if previewing else "",
    })


@login_required
def edit(request, slug):
    """Members only. Edit a page; the slug never changes."""
    guide_page = get_object_or_404(GuidePage, slug=slug)
    form = GuidePageForm(request.POST or None, instance=guide_page)
    previewing = False
    if request.method == "POST":
        if "preview" in request.POST:
            previewing = True
        elif form.is_valid():
            guide_page = form.save(commit=False)
            guide_page.updated_by = request.user
            guide_page.save()
            # save_revision snapshots CURRENT field values, so it must run
            # after the new content is saved — history is newest-state-first.
            guide_page.save_revision(request.user)
            messages.success(request, "Your changes have been saved. Thank you!")
            return redirect(guide_page)
    return render(request, "guide/form.html", {
        "nav_active": "guide",
        "form": form,
        "page": guide_page,
        "previewing": previewing,
        "preview_content": _preview_text(request) if previewing else "",
    })


@login_required
def history(request, slug):
    """Members only. Revision list, newest first, with character deltas."""
    guide_page = get_object_or_404(GuidePage, slug=slug)
    revisions = list(guide_page.revisions.select_related("editor"))
    for i, rev in enumerate(revisions):
        older_len = len(revisions[i + 1].content) if i + 1 < len(revisions) else 0
        rev.delta = len(rev.content) - older_len
        rev.editor_name = _display_name(rev.editor)
    return render(request, "guide/history.html", {
        "nav_active": "guide",
        "page": guide_page,
        "revisions": revisions,
    })


@login_required
def revision(request, slug, revision_id):
    """Members only. One revision, rendered, with a restore button."""
    guide_page = get_object_or_404(GuidePage, slug=slug)
    rev = get_object_or_404(
        GuideRevision.objects.select_related("editor"),
        pk=revision_id, page=guide_page,
    )
    latest = guide_page.revisions.first()
    return render(request, "guide/revision.html", {
        "nav_active": "guide",
        "page": guide_page,
        "revision": rev,
        "editor_name": _display_name(rev.editor),
        "is_current": latest is not None and latest.pk == rev.pk,
    })


@login_required
@require_POST
def restore(request, slug, revision_id):
    """Members only. Restore an old revision — just a normal edit that applies
    the old title/content, then snapshots the result as a new revision."""
    guide_page = get_object_or_404(GuidePage, slug=slug)
    rev = get_object_or_404(GuideRevision, pk=revision_id, page=guide_page)
    guide_page.title = rev.title
    guide_page.content = rev.content
    guide_page.updated_by = request.user
    guide_page.save()
    guide_page.save_revision(request.user)
    when = date_format(timezone.localtime(rev.created_at), "j M Y, H:i")
    messages.success(request, f"Restored the version from {when}.")
    return redirect(guide_page)
