import hashlib

from django.utils import timezone

from core.models import BUILTIN_TABS, SiteConfig, SitePage, visible_to


def banner_key(config=None):
    """A short fingerprint of the current banner text, so dismissing one
    banner doesn't hide the next."""
    config = config or SiteConfig.get()
    return hashlib.sha1(config.banner_text.encode("utf-8")).hexdigest()[:12]


def site_config(request):
    config = SiteConfig.get()
    banner = ""
    if config.banner_text and (
        config.banner_until is None or config.banner_until > timezone.now()
    ):
        if request.session.get("banner_dismissed") != banner_key(config):
            banner = config.banner_text
    return {"site_config": config, "site_banner": banner}


def navigation(request):
    """Which nav tabs this viewer sees.

    ``visible_tabs`` — set of built-in tab keys (supper, ball, about,
    members, messages) after applying the admin-controlled visibility.
    ``nav_pages`` — published CMS pages that ask for a nav slot, under
    About; ``nav_pages_guide`` and ``nav_pages_members`` the same for the
    other two menus.
    ``nav_groups`` — every event tag, listed under About as Groups.
    Admins always see everything that isn't 'hidden', so they can check the
    site without logging out.
    """
    config = SiteConfig.get()
    user = request.user
    visible = set()
    for key, _label, _default in BUILTIN_TABS:
        visibility = config.tab_visibility_for(key)
        if visible_to(visibility, user):
            visible.add(key)
        elif (
            visibility != "hidden"
            and user.is_authenticated
            and user.is_portal_admin
        ):
            visible.add(key)
    nav_pages = SitePage.objects.filter(is_published=True).exclude(nav_label="")
    by_section = {"about": [], "guide": [], "members": []}
    for page in nav_pages:
        if visible_to(page.nav_visibility, user):
            by_section.setdefault(page.section, []).append(page)
    from events.models import Category

    nav_groups = list(Category.objects.only("name", "slug", "emoji"))
    return {
        "visible_tabs": visible,
        "nav_pages": by_section["about"],
        "nav_pages_guide": by_section["guide"],
        "nav_pages_members": by_section["members"],
        "nav_groups": nav_groups,
    }
