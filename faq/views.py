"""Guide toolbox views: the who-to-contact decision map (DB-backed and
admin-editable), and the college- and department-specific pages. All public.

The old standalone FAQ hub merged into the Guide — /faq/ redirects there,
and the quick-answers content lives in the Guide's FAQ section as ordinary
wiki pages.
"""

from django.http import Http404
from django.shortcuts import redirect, render

from accounts.models import COLLEGES, User

from .data import COLLEGES_INFO, CONTACT_ROLES, DEPARTMENTS_INFO
from .models import ContactNode


def index(request):
    """The FAQ hub is now part of the Guide."""
    return redirect("guide:index", permanent=True)


def contacts(request):
    root = ContactNode.get_root()
    tree = root.as_dict(request.user) if root else None
    return render(request, "faq/contacts.html", {
        "nav_active": "guide",
        "tree": tree,
        "roles": CONTACT_ROLES,
        "can_edit": request.user.is_authenticated and request.user.is_portal_admin,
    })


def colleges(request):
    entries = [dict(slug=slug, **info) for slug, info in COLLEGES_INFO.items()]
    entries.sort(key=lambda e: e["name"])
    mature = [e for e in entries if e["status"].startswith("Mature-only")]
    return render(request, "faq/colleges.html", {
        "nav_active": "guide",
        "entries": entries,
        "mature": mature,
    })


def college(request, slug):
    info = COLLEGES_INFO.get(slug)
    if info is None:
        raise Http404("No college page for that slug.")
    member_count = None
    if request.user.is_authenticated:
        member_count = User.objects.filter(
            college=slug, is_banned=False, is_shadow_banned=False
        ).count()
    college_display = dict(COLLEGES).get(slug, info["name"])
    return render(request, "faq/college.html", {
        "nav_active": "guide",
        "slug": slug,
        "info": info,
        "college_display": college_display,
        "member_count": member_count,
    })


def departments(request):
    entries = [dict(slug=slug, **info) for slug, info in DEPARTMENTS_INFO.items()]
    return render(request, "faq/departments.html", {
        "nav_active": "guide",
        "entries": entries,
    })


def department(request, slug):
    info = DEPARTMENTS_INFO.get(slug)
    if info is None:
        raise Http404("No department page for that slug.")
    return render(request, "faq/department.html", {
        "nav_active": "guide",
        "slug": slug,
        "info": info,
    })
