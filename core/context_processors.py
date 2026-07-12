from core.models import BUILTIN_TABS, SiteConfig, SitePage, visible_to


def site_config(request):
    return {"site_config": SiteConfig.get()}


def navigation(request):
    """Which nav tabs this viewer sees.

    ``visible_tabs`` — set of built-in tab keys (supper, ball, about,
    members, messages) after applying the admin-controlled visibility.
    ``nav_pages`` — published CMS pages that ask for a nav slot.
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
    nav_pages = [p for p in nav_pages if visible_to(p.nav_visibility, user)]
    return {"visible_tabs": visible, "nav_pages": nav_pages}
