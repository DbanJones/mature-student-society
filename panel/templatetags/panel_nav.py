from django import template
from django.urls import reverse

from panel.nav import PANEL_GROUPS, badge_counts

register = template.Library()


@register.inclusion_tag("panel/_nav.html", takes_context=True)
def panel_nav(context):
    """Render the panel's two-tier navigation for the current viewer.

    Reads ``panel_tab`` from the view context to decide which group is
    active and which second-row pages to show.
    """
    user = context["request"].user
    active_tab = context.get("panel_tab", "")
    if not user.is_portal_admin:
        # Tag owners who aren't society admins get exactly one page.
        return {"tag_owner_only": True, "groups": [], "subtabs": []}

    counts = badge_counts()
    groups, subtabs = [], []
    for key, label, pages in PANEL_GROUPS:
        if key == "superadmin" and not user.is_super_admin:
            continue
        active = any(tab == active_tab for tab, _, _ in pages)
        groups.append({
            "key": key,
            "label": label,
            "url": reverse(pages[0][2]),
            "active": active,
            "badge": counts.get(key, 0),
        })
        if active and len(pages) > 1:
            subtabs = [
                {
                    "label": page_label,
                    "url": reverse(url_name),
                    "active": tab == active_tab,
                }
                for tab, page_label, url_name in pages
            ]
    return {"tag_owner_only": False, "groups": groups, "subtabs": subtabs}


@register.filter
def get_item(mapping, key):
    """``{{ counts|get_item:value }}`` for dict lookups with a variable key."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None
