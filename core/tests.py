"""Tests for the public pages.

The key rule: members_only events must never leak to anonymous visitors on
the homepage (the calendar itself is tested in the events app).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.templatetags.md import markdown_filter
from events.models import Category, Event

User = get_user_model()


class MarkdownSanitizerTests(TestCase):
    """Member-written Markdown must never yield executable HTML."""

    def _attrs_of_tags(self, html):
        import re
        return " ".join(re.findall(r"<[a-zA-Z][^>]*>", html)).lower()

    def test_attr_list_cannot_inject_event_handlers(self):
        out = str(markdown_filter("## Heading {: onmouseover=alert(1)}"))
        self.assertNotIn("onmouseover=", self._attrs_of_tags(out))

    def test_javascript_uri_links_are_stripped(self):
        out = str(markdown_filter("[click](javascript:alert(document.cookie))"))
        self.assertNotIn("javascript:", out.lower())

    def test_raw_html_is_escaped(self):
        out = str(markdown_filter("<script>alert(1)</script>"))
        self.assertNotIn("<script", out.lower())

    def test_img_onerror_is_neutralised(self):
        out = str(markdown_filter("<img src=x onerror=alert(1)>"))
        self.assertNotIn("<img", self._attrs_of_tags(out))
        self.assertNotIn("onerror=", self._attrs_of_tags(out))

    def test_legitimate_formatting_survives(self):
        out = str(markdown_filter("**bold** and [link](https://example.com)"))
        self.assertIn("<strong>bold</strong>", out)
        self.assertIn('href="https://example.com"', out)


def make_member(username, **extra):
    """A member with a complete profile (so middleware doesn't redirect)."""
    defaults = dict(
        first_name="Test", last_name="Member", college="wolfson",
        mobile="+44 7700 900000", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class PublicHomeTests(TestCase):
    def setUp(self):
        self.member = make_member("abc123")
        self.category = Category.objects.create(
            name="Pub Nights", slug="pub-nights", emoji="🍺"
        )
        soon = timezone.now() + datetime.timedelta(days=2)
        self.public_event = Event.objects.create(
            title="Open Pub Night", category=self.category,
            start=soon, created_by=self.member,
        )
        self.secret_event = Event.objects.create(
            title="Secret Members Gathering", category=self.category,
            start=soon + datetime.timedelta(days=1),
            created_by=self.member, members_only=True,
        )

    def test_home_renders_anonymously(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcoming Cambridge")
        self.assertContains(response, "Member login")

    def test_home_does_not_leak_members_only_events_to_anonymous(self):
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "Open Pub Night")
        self.assertNotContains(response, "Secret Members Gathering")

    def test_home_shows_members_only_events_when_logged_in(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "Secret Members Gathering")

    def test_home_hides_login_cta_for_members(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "My dashboard")

    def test_cancelled_events_hidden(self):
        self.public_event.is_cancelled = True
        self.public_event.save()
        response = self.client.get(reverse("core:home"))
        self.assertNotContains(response, "Open Pub Night")


class StaticPageTests(TestCase):
    def test_about_renders(self):
        response = self.client.get(reverse("core:about"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "committee")

    def test_wellbeing_renders(self):
        response = self.client.get(reverse("core:wellbeing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Talking Group")

    def test_policies_renders(self):
        response = self.client.get(reverse("core:policies"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Political neutrality")


class TabVisibilityAndPagesTests(TestCase):
    """Admin-controlled nav tabs and CMS pages."""

    def setUp(self):
        from core.models import SiteConfig
        self.config = SiteConfig.get()
        self.member = User.objects.create_user(
            username="tv001", password="pw", first_name="Tab", last_name="Member",
            college="wolfson", mobile="+44 7700 900001",
        )
        self.admin = User.objects.create_user(
            username="tv002", password="pw", first_name="Ada", last_name="Admin",
            college="darwin", mobile="+44 7700 900002", is_portal_admin=True,
        )

    NAV_SUPPER = "Supper Club</a>"
    NAV_BALL = "Winter Ball</a>"

    def test_hidden_tab_disappears_for_members_but_not_admins(self):
        self.config.tab_visibility = {"supper": "admins"}
        self.config.save()
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse("core:home")), self.NAV_SUPPER)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("core:home")), self.NAV_SUPPER)

    def test_members_only_tab_hidden_from_public(self):
        self.config.tab_visibility = {"ball": "members"}
        self.config.save()
        response = self.client.get(reverse("core:home"))
        self.assertNotContains(response, self.NAV_BALL)
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse("core:home")), self.NAV_BALL)

    def test_site_page_lifecycle(self):
        from core.models import SitePage
        page = SitePage.objects.create(
            title="Sponsors", slug="sponsors", content="Thank <b>you</b>.",
            nav_label="Sponsors", nav_visibility="public",
        )
        response = self.client.get(page.get_absolute_url())
        self.assertContains(response, "Thank <b>you</b>.")
        # In the nav for everyone…
        self.assertContains(self.client.get(reverse("core:home")), ">Sponsors</a>")
        # …until unpublished: page 404s for the public, stays up for admins.
        page.is_published = False
        page.save()
        self.assertEqual(self.client.get(page.get_absolute_url()).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(page.get_absolute_url()).status_code, 200)

    def test_admin_page_crud_via_panel(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("panel:page_create"), {
            "title": "House rules", "content": "Be kind.",
            "is_published": "on", "nav_label": "", "nav_visibility": "public",
            "sort_order": 100,
        })
        self.assertEqual(response.status_code, 302)
        from core.models import SitePage
        page = SitePage.objects.get(slug="house-rules")
        response = self.client.post(reverse("panel:page_delete", args=[page.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SitePage.objects.filter(pk=page.pk).exists())
