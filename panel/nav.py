"""The admin panel's two-tier navigation.

The top row has one tab per group; the second row lists the pages of the
active group (groups with a single page show no second row). Pages are
keyed by the ``panel_tab`` value every panel view already puts in its
context, so adding a page to the panel is one line here plus a view.
"""

from accounts.models import WaitlistRequest, WhatsAppAccessRequest
from testimonials.models import Testimonial

# (group key, group label, [(panel_tab, page label, url name), ...])
PANEL_GROUPS = [
    ("overview", "Overview", [
        ("home", "Dashboard", "panel:home"),
        ("audit", "Audit log", "panel:audit"),
    ]),
    ("people", "People", [
        ("members", "Members", "panel:members"),
        ("waitlist", "Waitlist", "panel:waitlist"),
        ("whatsapp", "WhatsApp", "panel:whatsapp_requests"),
        ("surveys", "Surveys", "panel:surveys"),
    ]),
    ("events", "Events", [
        ("events", "All events", "panel:events"),
        ("tagged", "Tagged events", "panel:tagged_events"),
        ("polls", "Polls", "panel:polls"),
    ]),
    ("messages", "Messages", [
        ("messages", "Messages", "panel:messages"),
    ]),
    ("content", "Content", [
        ("pages", "Pages", "panel:pages"),
        ("testimonials", "Testimonials", "panel:testimonials"),
        ("navigation", "Navigation", "panel:navigation"),
        ("about", "About & committee", "panel:about"),
        ("content", "Contacts", "panel:content"),
        ("terms", "Terms", "panel:terms"),
    ]),
    ("mailer", "Mailer", [
        ("mailer", "Mailer", "panel:mailer"),
    ]),
    ("stats", "Stats", [
        ("stats", "Stats", "panel:stats"),
    ]),
    ("superadmin", "Super admin", [
        ("superadmin", "Super admin", "panel:superadmin"),
    ]),
]


def group_for_tab(panel_tab):
    """The group key a ``panel_tab`` value belongs to, or None."""
    for key, _label, pages in PANEL_GROUPS:
        if any(tab == panel_tab for tab, _, _ in pages):
            return key
    return None


def badge_counts():
    """Items waiting for an admin, keyed by group, for the tab badges."""
    return {
        "people": (
            WaitlistRequest.objects.filter(
                status=WaitlistRequest.Status.PENDING
            ).count()
            + WhatsAppAccessRequest.objects.filter(
                status=WhatsAppAccessRequest.Status.OPEN
            ).count()
        ),
        "content": Testimonial.objects.pending().count(),
    }
