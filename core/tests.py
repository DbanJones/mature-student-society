"""Tests for the public pages.

The key rule: members_only events must never leak to anonymous visitors on
the homepage (the calendar itself is tested in the events app).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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

    NAV_SUPPER = "Restaurants and ratings"
    NAV_BALL = "Winter Ball</a>"

    def test_hidden_tab_disappears_for_members_but_not_admins(self):
        from events.models import Category

        Category.objects.create(name="Supper Club", slug="supper-club", has_restaurant_ratings=True)
        group = reverse("events:tag_page", args=["supper-club"])
        self.config.tab_visibility = {"supper": "admins"}
        self.config.save()
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(group), self.NAV_SUPPER)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(group), self.NAV_SUPPER)

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


class SitePageAudienceAndEditorTests(TestCase):
    """Admins own pages; named editors may change the words; the audience
    applies to the page itself, not just its navigation link."""

    def setUp(self):
        from core.models import SitePage
        self.editor = User.objects.create_user(
            username="pe001", password="pw", first_name="Pat", last_name="Editor",
            college="wolfson", mobile="+44 7700 900011",
        )
        self.other = User.objects.create_user(
            username="pe002", password="pw", first_name="Ol", last_name="Other",
            college="wolfson", mobile="+44 7700 900012",
        )
        self.page = SitePage.objects.create(
            title="Partners handbook", slug="partners-handbook", content="Welcome.",
            nav_label="Partners", nav_visibility="members",
        )
        self.page.editors.add(self.editor)
        self.edit_url = reverse("core:site_page_edit", args=[self.page.slug])

    def test_members_only_page_is_not_readable_logged_out(self):
        self.assertEqual(self.client.get(self.page.get_absolute_url()).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.page.get_absolute_url()).status_code, 200)

    def test_hidden_page_is_readable_by_link_but_not_in_nav(self):
        self.page.nav_visibility = "hidden"
        self.page.save()
        self.assertEqual(self.client.get(self.page.get_absolute_url()).status_code, 200)
        self.assertNotContains(self.client.get(reverse("core:home")), ">Partners</a>")

    def test_editor_sees_edit_button_and_can_change_the_content(self):
        self.client.force_login(self.editor)
        self.assertContains(self.client.get(self.page.get_absolute_url()), self.edit_url)
        response = self.client.post(
            self.edit_url, {"title": "Partners handbook", "content": "Updated **welcome**."}
        )
        self.assertEqual(response.status_code, 302)
        self.page.refresh_from_db()
        self.assertEqual(self.page.content, "Updated **welcome**.")
        self.assertEqual(self.page.updated_by, self.editor)
        self.assertEqual(self.page.revisions.count(), 1)
        self.assertEqual(self.page.revisions.first().editor, self.editor)

    def test_editor_cannot_change_publishing_audience_or_settings(self):
        self.client.force_login(self.editor)
        self.client.post(self.edit_url, {
            "title": "Partners handbook", "content": "x",
            "is_published": "", "nav_visibility": "public",
        })
        self.page.refresh_from_db()
        self.assertTrue(self.page.is_published)
        self.assertEqual(self.page.nav_visibility, "members")
        self.assertEqual(
            self.client.get(reverse("panel:page_edit", args=[self.page.pk])).status_code,
            403,
        )

    def test_non_editor_gets_403_and_no_edit_button(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.assertNotContains(self.client.get(self.page.get_absolute_url()), self.edit_url)

    def test_history_lists_versions_and_restore_puts_one_back(self):
        self.client.force_login(self.editor)
        self.client.post(self.edit_url, {"title": "Partners handbook", "content": "Version one."})
        self.client.post(self.edit_url, {"title": "Partners handbook", "content": "Version two."})
        first = self.page.revisions.order_by("created_at").first()
        response = self.client.get(reverse("core:site_page_history", args=[self.page.slug]))
        self.assertContains(response, "Pat Editor")
        response = self.client.post(
            reverse("core:site_page_restore", args=[self.page.slug, first.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.page.refresh_from_db()
        self.assertEqual(self.page.content, "Version one.")
        self.assertEqual(self.page.revisions.first().action, "restored")

    def test_preview_renders_without_saving(self):
        self.client.force_login(self.editor)
        response = self.client.post(self.edit_url, {
            "title": "Partners handbook", "content": "Draft **bold**", "preview": "1",
        })
        self.assertContains(response, "<strong>bold</strong>")
        self.page.refresh_from_db()
        self.assertEqual(self.page.content, "Welcome.")

    def test_dashboard_lists_pages_you_can_edit(self):
        self.client.force_login(self.editor)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "Pages you can edit")
        self.assertContains(response, "Partners handbook")


class SearchBannerAndAboutTests(TestCase):
    def setUp(self):
        from core.models import Activity, CommitteeMember, SiteConfig, SitePage
        self.config = SiteConfig.get()
        self.member = User.objects.create_user(
            username="sb001", password="pw", first_name="Sam", last_name="Searcher",
            college="wolfson", mobile="+44 7700 900021", course="MPhil Archaeology",
        )
        category = Category.objects.create(name="Walks", slug="walks")
        Event.objects.create(
            title="Grantchester meadows walk", category=category,
            start=timezone.now() + datetime.timedelta(days=2), created_by=self.member,
        )
        SitePage.objects.create(title="Sponsors", slug="sponsors", content="Thanks to Grantchester Bakery.")
        CommitteeMember.objects.create(name="Pat President", role="President")
        Activity.objects.create(emoji="🎲", name="Board games", blurb="Monthly games night.")

    def test_search_finds_events_pages_and_members_for_members_only(self):
        response = self.client.get(reverse("core:search") + "?q=grantchester")
        self.assertContains(response, "Grantchester meadows walk")
        self.assertContains(response, "Sponsors")
        self.assertNotContains(response, ">Members<")
        self.client.force_login(self.member)
        response = self.client.get(reverse("core:search") + "?q=archaeology")
        self.assertContains(response, "Sam Searcher")

    def test_banner_shows_until_expiry_and_can_be_dismissed(self):
        self.config.banner_text = "Freshers Fair volunteers needed"
        self.config.save()
        self.assertContains(self.client.get(reverse("core:home")), "Freshers Fair volunteers")
        self.client.post(reverse("core:dismiss_banner"), {"next": "/"})
        self.assertNotContains(self.client.get(reverse("core:home")), "Freshers Fair volunteers")
        # A new banner text shows again even after a dismissal.
        self.config.banner_text = "Winter Ball tickets on sale"
        self.config.save()
        self.assertContains(self.client.get(reverse("core:home")), "Winter Ball tickets")
        self.config.banner_until = timezone.now() - datetime.timedelta(hours=1)
        self.config.save()
        self.assertNotContains(self.client.get(reverse("core:home")), "Winter Ball tickets")

    def test_about_and_home_read_committee_and_activities_from_the_database(self):
        from core.models import CommitteeMember
        self.assertContains(self.client.get(reverse("core:about")), "Pat President")
        # The seed migration carried the old hardcoded list over; stepping
        # someone down hides them without deleting the row.
        CommitteeMember.objects.filter(name="Basma Al Ghamdi").update(is_active=False)
        self.assertNotContains(self.client.get(reverse("core:about")), "Basma Al Ghamdi")
        self.assertContains(self.client.get(reverse("core:home")), "Board games")


class PreviewAndImageTests(TestCase):
    def test_admin_previews_a_members_page_as_the_public(self):
        from core.models import SitePage
        admin = User.objects.create_user(
            username="pv001", password="pw", first_name="Ada", last_name="Admin",
            college="darwin", mobile="+44 7700 900041", is_portal_admin=True,
        )
        page = SitePage.objects.create(title="Secret", slug="secret", content="Shh.", nav_visibility="members")
        self.client.force_login(admin)
        response = self.client.get(page.get_absolute_url() + "?as=public")
        self.assertContains(response, "page not found")
        self.assertNotContains(response, "Shh.")
        response = self.client.get(page.get_absolute_url() + "?as=member")
        self.assertContains(response, "Shh.")
        self.assertContains(response, "they can read this page")

    def test_shrink_image_resizes_and_strips_to_jpeg(self):
        from io import BytesIO

        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        from core.images import shrink_image

        buffer = BytesIO()
        Image.new("RGB", (2400, 1200), "red").save(buffer, format="PNG")
        upload = SimpleUploadedFile("big.png", buffer.getvalue(), content_type="image/png")
        small = shrink_image(upload, 512)
        self.assertEqual(small.name, "big.jpg")
        self.assertEqual(Image.open(small).size, (512, 256))
        with self.assertRaises(Exception):
            shrink_image(SimpleUploadedFile("x.png", b"not an image", content_type="image/png"), 512)


class PageDiffTests(TestCase):
    def test_diff_shows_added_and_removed_lines(self):
        from core.diff import line_diff, summary
        from core.models import SitePage, SitePageRevision

        rows = line_diff("a" + chr(10) + "b", "a" + chr(10) + "c")
        self.assertEqual([k for k, _ in rows], ["same", "del", "ins"])
        self.assertEqual(summary(rows), {"added": 1, "removed": 1})

        admin = User.objects.create_user(
            username="df001", password="pw", first_name="Ada", last_name="Admin",
            college="darwin", mobile="+44 7700 900051", is_portal_admin=True,
        )
        page = SitePage.objects.create(title="Rules", slug="rules", content="Be kind.")
        first = page.save_revision(admin, SitePageRevision.Action.CREATED)
        page.content = "Be kinder."
        page.save()
        second = page.save_revision(admin, SitePageRevision.Action.EDITED)
        self.client.force_login(admin)
        response = self.client.get(reverse("core:site_page_diff", args=["rules", second.pk]))
        self.assertContains(response, "Be kinder.")
        self.assertContains(response, "diff-del")
        self.assertContains(self.client.get(reverse("core:site_page_history", args=["rules"])), "What changed")


class TextBlockAndPagesTreeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="tb001", password="pw", first_name="Ada", last_name="Admin",
            college="darwin", mobile="+44 7700 900002", is_portal_admin=True,
        )
        self.member = User.objects.create_user(
            username="tb002", password="pw", first_name="Mia", last_name="Member",
            college="wolfson", mobile="+44 7700 900001",
        )

    def test_fixed_pages_show_the_built_in_wording_until_an_admin_edits_it(self):
        from panel.models import AuditLog

        about = reverse("core:about")
        self.assertContains(self.client.get(about), "Founded in September 2024")
        edit = reverse("panel:text_edit", args=["about.body"])
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(edit, {"text": "We are **new** words", "preview": "1"}), "<strong>new</strong>")
        self.assertContains(self.client.get(about), "Founded in September 2024")  # a preview saves nothing
        self.client.post(edit, {"text": "We are **new** words"})
        page = self.client.get(about)
        self.assertContains(page, "<strong>new</strong>")
        self.assertNotContains(page, "Founded in September 2024")
        self.assertTrue(AuditLog.objects.filter(action="edit_text", detail="about.body").exists())
        self.assertContains(self.client.get(reverse("panel:pages")), "edited")
        self.client.post(edit, {"text": "", "reset": "1"})
        self.assertContains(self.client.get(about), "Founded in September 2024")
        # A "lines" block feeds a loop in the template.
        self.client.post(reverse("panel:text_edit", args=["home.stats"]), {"text": "7 | dragons\n9 | lives"})
        home = self.client.get(reverse("core:home"))
        self.assertContains(home, "dragons")
        self.assertNotContains(home, "youngest member")
        self.assertEqual(self.client.get(reverse("panel:text_edit", args=["no.such"])).status_code, 404)
        self.client.post(reverse("panel:text_edit", args=["ball.timeline"]), {"text": "21:00|Carriages"})
        self.assertContains(self.client.get(reverse("core:winter_ball")), '<span class="ball-time">21:00</span><span class="ball-what">Carriages</span>')

    def test_the_pages_panel_is_a_tree_with_controls(self):
        from core.models import SitePage

        first = SitePage.objects.create(title="Sponsors", slug="sponsors", content="x", nav_label="Sponsors", sort_order=10)
        second = SitePage.objects.create(title="Alumni", slug="alumni", content="x", nav_label="Alumni", sort_order=20)
        third = SitePage.objects.create(title="Study tips", slug="study-tips", content="x", nav_label="Study tips", section="guide")
        self.client.force_login(self.admin)
        body = self.client.get(reverse("panel:pages")).content.decode()
        for expected in ("Winter Ball", "Community policies", "Who to contact", "Sponsors", "Alumni", "Study tips", "Add a page here", "Rename &amp; settings"):
            self.assertIn(expected, body)
        self.assertLess(body.index('id="page-custom-%d"' % first.pk), body.index('id="page-custom-%d"' % second.pk))
        self.client.post(reverse("panel:page_move", args=[second.pk]), {"direction": "up"})
        body = self.client.get(reverse("panel:pages")).content.decode()
        self.assertLess(body.index('id="page-custom-%d"' % second.pk), body.index('id="page-custom-%d"' % first.pk))
        self.client.post(reverse("panel:page_section", args=[third.pk]), {"section": "members"})
        third.refresh_from_db()
        self.assertEqual(third.section, "members")
        # The menus follow the sections.
        self.client.force_login(self.member)
        nav = self.client.get(reverse("core:home")).content.decode()
        members_menu = nav[nav.index("Members portal"):nav.index("Log out")]
        self.assertIn("Study tips", members_menu)
        about_start = nav.index(">About</summary>")
        about_menu = nav[about_start:nav.index("</details>", about_start)]
        self.assertIn("Alumni", about_menu)
        self.assertNotIn("Study tips", about_menu)

    def test_edit_text_links_show_for_admins_only(self):
        for name in ("core:policies", "core:about", "core:winter_ball", "guide:index", "events:groups"):
            self.client.force_login(self.member)
            self.assertNotContains(self.client.get(reverse(name)), "Edit text")
            self.client.force_login(self.admin)
            self.assertContains(self.client.get(reverse(name)), "Edit text", msg_prefix=name)


class MenuPageTests(TestCase):
    def test_the_menu_page_lists_the_site_and_the_header_has_no_burger(self):
        page = self.client.get(reverse("core:menu"))
        self.assertContains(page, "Event Calendar")
        self.assertContains(page, "Member login")
        self.assertNotContains(page, "nav-burger")
        self.assertNotContains(page, "☰")
        self.assertContains(page, 'class="nav-menu-link"')
        member = User.objects.create_user(
            username="mn001", password="pw", first_name="Mia", last_name="Member",
            college="wolfson", mobile="+44 7700 900001",
        )
        self.client.force_login(member)
        page = self.client.get(reverse("core:menu"))
        self.assertContains(page, "Dashboard")
        self.assertContains(page, "Log out")
        self.assertContains(page, "<span>Menu</span>")  # the fifth tab on phones

    def test_the_tab_bar_honours_the_tab_visibility_setting(self):
        from django.core.cache import cache

        from core.models import SiteConfig

        member = User.objects.create_user(
            username="mn002", password="pw", first_name="Tab", last_name="Member",
            college="wolfson", mobile="+44 7700 900003",
        )
        self.client.force_login(member)
        self.assertContains(self.client.get(reverse("core:menu")), 'href="/messages/"')
        config = SiteConfig.get()
        config.tab_visibility = {"messages": "hidden"}
        config.save()
        cache.clear()
        self.assertNotContains(self.client.get(reverse("core:menu")), 'href="/messages/"')


class PicturesAndEditorTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="pc001", password="pw", first_name="Ada", last_name="Admin",
            college="darwin", mobile="+44 7700 900002", is_portal_admin=True,
        )
        self.member = User.objects.create_user(
            username="pc002", password="pw", first_name="Mia", last_name="Member",
            college="wolfson", mobile="+44 7700 900001",
        )

    def _png(self):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (40, 30), "green").save(buffer, format="PNG")
        return SimpleUploadedFile("garden.png", buffer.getvalue(), content_type="image/png")

    def test_admins_upload_pictures_that_everyone_can_see(self):
        import os
        import tempfile

        from django.test import override_settings

        from core.models import Picture

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.client.force_login(self.member)
            self.assertEqual(self.client.get(reverse("panel:pictures")).status_code, 403)
            self.client.force_login(self.admin)
            response = self.client.post(reverse("panel:pictures"), {"image": self._png(), "alt": "The garden", "caption": ""})
            self.assertRedirects(response, reverse("panel:pictures"))
            picture = Picture.objects.get()
            self.assertTrue(picture.image.name.startswith("public/pictures/"))
            self.assertTrue(os.path.exists(os.path.join(media, picture.image.name)))
            self.assertContains(self.client.get(reverse("panel:pictures")), picture.markdown())
            self.client.logout()
            served = self.client.get(picture.image.url)
            self.assertEqual(served.status_code, 200)  # public pages need public pictures
            served.close()  # Windows cannot delete a file that is still open
            self.assertEqual(self.client.get("/media/profiles/nope.jpg").status_code, 302)  # the rest stays members-only
            self.assertEqual(self.client.get("/media/public/../profiles/nope.jpg").status_code, 302)
            self.client.force_login(self.admin)
            self.client.post(reverse("panel:picture_delete", args=[picture.pk]))
            self.assertFalse(Picture.objects.exists())
            self.assertFalse(os.path.exists(os.path.join(media, picture.image.name)))

    def test_the_editor_has_a_toolbar_hook_and_a_live_preview_endpoint(self):
        from core.models import SitePage

        page = SitePage.objects.create(title="Sponsors", slug="sponsors", content="x")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("core:site_page_edit", args=[page.slug])), 'data-editor="/preview/"')
        self.assertContains(self.client.get(reverse("panel:page_edit", args=[page.pk])), 'data-editor="/preview/"')
        preview = self.client.post(reverse("core:preview"), {"text": "**bold** <script>alert(1)</script> ![a](/media/public/pictures/a.jpg)"})
        self.assertContains(preview, "<strong>bold</strong>")
        self.assertNotContains(preview, "<script>")
        self.assertContains(preview, 'src="/media/public/pictures/a.jpg"')
        self.client.logout()
        self.assertEqual(self.client.post(reverse("core:preview"), {"text": "x"}).status_code, 302)

    def test_markdown_that_would_stall_the_renderer_is_refused(self):
        from django.core.cache import cache

        from core.models import SitePage

        self.client.force_login(self.member)
        heavy = "`" * 700
        refused = self.client.post(reverse("core:preview"), {"text": heavy})
        self.assertEqual(refused.status_code, 413)
        self.assertIn("too many", refused.content.decode())
        page = SitePage.objects.create(title="Sponsors", slug="sponsors-2", content="x")
        page.editors.add(self.member)
        saved = self.client.post(reverse("core:site_page_edit", args=[page.slug]), {"title": "Sponsors", "content": heavy})
        self.assertEqual(saved.status_code, 200)  # shown again with the error
        self.assertContains(saved, "Not saved")
        page.refresh_from_db()
        self.assertEqual(page.content, "x")
        for _ in range(45):
            self.client.post(reverse("core:preview"), {"text": "fine"})
        self.assertEqual(self.client.post(reverse("core:preview"), {"text": "fine"}).status_code, 429)
        cache.delete(f"preview-rate:{self.member.pk}")


class UnsubscribeTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # the rate limits live in the cache

    def test_anyone_can_stop_the_mailer_for_an_address_and_undo_it(self):
        from django.core import mail

        from panel.models import OldSubscriber

        member = User.objects.create_user(
            username="un001", password="pw", first_name="Mia", last_name="Member",
            college="wolfson", mobile="+44 7700 900001", email="Mia@cam.ac.uk",
        )
        old = OldSubscriber.objects.create(email="mia@cam.ac.uk")
        self.assertContains(self.client.get(reverse("core:unsubscribe")), "Stop the")
        page = self.client.post(reverse("core:unsubscribe"), {"email": "MIA@cam.ac.uk"})
        self.assertContains(page, "won't get the")
        member.refresh_from_db()
        old.refresh_from_db()
        self.assertFalse(member.wants_mailer)
        self.assertIsNotNone(old.unsubscribed_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["mia@cam.ac.uk"])
        link = next(word for word in mail.outbox[0].body.split() if "/resubscribe/" in word)
        path = link.split("testserver", 1)[1]
        self.assertContains(self.client.get(path), "back on")
        self.client.post(path)
        member.refresh_from_db()
        old.refresh_from_db()
        self.assertTrue(member.wants_mailer)
        self.assertIsNone(old.unsubscribed_at)
        page = self.client.post(reverse("core:unsubscribe"), {"email": "nobody@example.org"})
        self.assertContains(page, "won't get the")  # the same page, and no email, for an unknown address
        self.assertEqual(len(mail.outbox), 1)
        broken = self.client.get("/resubscribe/not-a-token/")
        self.assertEqual(broken.status_code, 400)
        self.assertContains(broken, "run out", status_code=400)

    def test_the_undo_link_puts_back_only_what_was_changed(self):
        from django.core import mail

        from panel.models import OldSubscriber

        member = User.objects.create_user(
            username="un002", password="pw", first_name="Ola", last_name="Out", college="wolfson",
            mobile="+44 7700 900002", email="ola@cam.ac.uk", wants_mailer=False,  # opted out already
        )
        old = OldSubscriber.objects.create(email="ola@cam.ac.uk")
        self.client.post(reverse("core:unsubscribe"), {"email": "ola@cam.ac.uk"})
        link = next(word for word in mail.outbox[-1].body.split() if "/resubscribe/" in word)
        self.client.post(link.split("testserver", 1)[1])
        member.refresh_from_db()
        old.refresh_from_db()
        self.assertIsNone(old.unsubscribed_at)  # put back
        self.assertFalse(member.wants_mailer)  # their own earlier choice stands

    def test_the_rate_limit_is_per_visitor_behind_the_proxy(self):
        for _ in range(10):
            self.client.post(reverse("core:unsubscribe"), {"email": "x@example.org"}, HTTP_X_FORWARDED_FOR="9.9.9.9, 1.2.3.4")
        blocked = self.client.post(reverse("core:unsubscribe"), {"email": "x@example.org"}, HTTP_X_FORWARDED_FOR="9.9.9.9, 1.2.3.4")
        self.assertEqual(blocked.status_code, 429)
        other = self.client.post(reverse("core:unsubscribe"), {"email": "x@example.org"}, HTTP_X_FORWARDED_FOR="9.9.9.9, 5.6.7.8")
        self.assertEqual(other.status_code, 200)

    def test_a_cambridge_address_and_its_hermes_twin_are_one_person(self):
        from panel import services
        from panel.models import OldSubscriber

        OldSubscriber.objects.create(email="ab123@cam.ac.uk")
        OldSubscriber.objects.create(email="ab123@hermes.cam.ac.uk")
        self.assertEqual(services.mailer_recipients("old")[1], ["ab123@cam.ac.uk"])  # one copy
        self.client.post(reverse("core:unsubscribe"), {"email": "ab123@cam.ac.uk"})
        self.assertEqual(services.mailer_recipients("old")[1], [])  # and not the twin instead
        self.assertFalse(OldSubscriber.objects.filter(unsubscribed_at__isnull=True).exists())

    def test_an_account_reading_the_mailer_elsewhere_keeps_it(self):
        member = User.objects.create_user(
            username="ab124", password="pw", first_name="Al", last_name="B", college="wolfson",
            mobile="+44 7700 900006", email="alice@gmail.com", crsid="ab124",
        )
        self.client.post(reverse("core:unsubscribe"), {"email": "ab124@cam.ac.uk"})
        member.refresh_from_db()
        self.assertTrue(member.wants_mailer)  # their CRSid address isn't the one they read it at

    def test_the_undo_link_only_puts_back_what_is_still_as_it_left_it(self):
        from django.core import mail
        from django.utils import timezone

        from panel.models import OldSubscriber

        member = User.objects.create_user(
            username="un006", password="pw", first_name="Fam", last_name="Ily", college="wolfson",
            mobile="+44 7700 900007", email="family@example.org",
        )
        row = OldSubscriber.objects.create(email="family@example.org")
        self.client.post(reverse("core:unsubscribe"), {"email": "family@example.org"})
        path = next(w for w in mail.outbox[-1].body.split() if "/resubscribe/" in w).split("testserver", 1)[1]
        member.refresh_from_db()
        member.email = "bob@example.net"  # the account has moved on, and opted out there
        member.wants_mailer = False
        member.save()
        self.client.post(path)
        member.refresh_from_db()
        row.refresh_from_db()
        self.assertFalse(member.wants_mailer)
        self.assertIsNone(row.unsubscribed_at)  # the row was still as the unsubscribe left it
        row.unsubscribed_at = timezone.now()  # unsubscribed again later, by an admin
        row.save()
        self.client.post(path)  # the old link, used again
        row.refresh_from_db()
        self.assertIsNotNone(row.unsubscribed_at)

    def test_every_unsubscribe_that_changes_something_is_told(self):
        from django.core import mail

        User.objects.create_user(
            username="un004", password="pw", first_name="Eve", last_name="F", college="wolfson",
            mobile="+44 7700 900004", email="ef789@cam.ac.uk",
        )
        self.client.post(reverse("core:unsubscribe"), {"email": "ef789@cam.ac.uk"})
        link = next(word for word in mail.outbox[-1].body.split() if "/resubscribe/" in word)
        self.client.post(link.split("testserver", 1)[1])
        self.client.post(reverse("core:unsubscribe"), {"email": "ef789@cam.ac.uk"})
        self.assertEqual(len(mail.outbox), 2)  # the second unsubscribe is told too

    @override_settings(SITE_URL="https://www.example.org")
    def test_emailed_links_use_the_sites_own_address(self):
        from django.core import mail

        from panel.models import OldSubscriber

        OldSubscriber.objects.create(email="site@example.org")
        self.client.post(reverse("core:unsubscribe"), {"email": "site@example.org"}, HTTP_HOST="testserver:1")
        self.assertIn("https://www.example.org/resubscribe/", mail.outbox[-1].body)

    def test_members_who_have_not_accepted_the_terms_can_still_unsubscribe(self):
        from django.core import mail
        from django.core.cache import cache

        from core.models import TermsVersion

        TermsVersion.objects.filter(number=1).update(is_published=True)
        cache.clear()
        member = User.objects.create_user(
            username="un005", password="pw", first_name="Gil", last_name="H", college="wolfson",
            mobile="+44 7700 900005", email="gh456@cam.ac.uk",
        )
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("core:menu")).status_code, 302)  # the terms gate is on
        self.assertEqual(self.client.get(reverse("core:unsubscribe")).status_code, 200)
        self.client.post(reverse("core:unsubscribe"), {"email": "gh456@cam.ac.uk"})
        member.refresh_from_db()
        self.assertFalse(member.wants_mailer)
        link = next(word for word in mail.outbox[-1].body.split() if "/resubscribe/" in word)
        self.assertEqual(self.client.get(link.split("testserver", 1)[1]).status_code, 200)
        cache.clear()

    def test_members_mid_signup_can_still_unsubscribe(self):
        member = User.objects.create_user(username="un003", password="pw", email="mid@cam.ac.uk")  # no profile yet
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("core:unsubscribe")).status_code, 200)


class StaticDeployTests(TestCase):
    def test_sync_static_copies_by_content_and_never_deletes(self):
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        from core.storage import static_sources

        total = len(static_sources())
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root):
            out = StringIO()
            call_command("sync_static", stdout=out)
            self.assertIn(f"{total} copied, 0 unchanged", out.getvalue())
            self.assertTrue(os.path.exists(os.path.join(root, "admin", "css", "base.css")))
            base = os.path.join(root, "css", "base.css")
            with open(base, "r+b") as handle:  # same size, different bytes...
                first = handle.read(1)
                handle.seek(0)
                handle.write(b"X" if first != b"X" else b"Y")
            stat = os.stat(base)
            os.utime(base, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**12))  # ...and newer than the source
            leftover = os.path.join(root, "css", "only-on-the-server.css")
            open(leftover, "w").close()
            out = StringIO()
            call_command("sync_static", stdout=out)
            self.assertIn(f"1 copied, {total - 1} unchanged", out.getvalue())  # collectstatic would skip it
            with open(base, "rb") as copy, open(static_sources()["css/base.css"], "rb") as source:
                self.assertEqual(copy.read(), source.read())
            self.assertTrue(os.path.exists(leftover))  # nothing is ever deleted

    def test_a_live_site_finds_the_folder_apache_serves_without_being_told(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        from config import settings as live

        with tempfile.TemporaryDirectory() as parent:
            app = Path(parent) / "mature-student-society"
            app.mkdir()
            served = Path(parent) / "public_html" / "static"
            with mock.patch.object(live, "BASE_DIR", app), mock.patch.dict(os.environ, {"DJANGO_STATIC_ROOT": ""}):
                with mock.patch.object(live, "DEBUG", False):
                    self.assertEqual(live._served_static_folder(), "")  # nothing beside it: nothing guessed
                    served.mkdir(parents=True)
                    self.assertEqual(live._served_static_folder(), str(served.resolve()))
                    with mock.patch.dict(os.environ, {"DJANGO_STATIC_ROOT": "/elsewhere/static"}):
                        self.assertEqual(live._served_static_folder(), "/elsewhere/static")  # .env wins
                with mock.patch.object(live, "DEBUG", True):
                    self.assertEqual(live._served_static_folder(), "")  # never on a development copy

    def test_the_srcf_launcher_fails_closed_and_keeps_static_files_current(self):
        from django.conf import settings

        script = (settings.BASE_DIR / "run_on_srcf.sh").read_bytes().decode("utf-8")
        for needed in ("set -euo pipefail", "[ -r .env ]", '"${RAVEN_MODE:-dev}" = "header"', '"${DB_ENGINE:-sqlite}"',
                       "manage.py check", "manage.py migrate", "manage.py sync_static", "-c gunicorn.conf.py",
                       ".venv/bin/gunicorn"):
            self.assertIn(needed, script)
        # .env is read tolerantly (as before), then strict again
        self.assertLess(script.index("set +eu"), script.index(". ./.env"))
        self.assertLess(script.index(". ./.env"), script.index("set -eu\n"))
        self.assertNotIn("seed_demo", script)  # it re-created deleted tags on every restart
        self.assertNotIn("runserver", script)
        self.assertNotIn("\r", script)  # a CRLF launcher won't run on Linux

    def test_the_socket_is_handed_to_the_society_or_left_open_with_a_warning(self):
        import importlib.util
        import os
        import tempfile
        from unittest import mock

        from django.conf import settings

        spec = importlib.util.spec_from_file_location("gunicorn_conf", settings.BASE_DIR / "gunicorn.conf.py")
        conf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(conf)
        self.assertEqual(conf.umask, 0o007)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "web.sock")
            open(path, "w").close()
            server = mock.Mock(LISTENERS=[mock.Mock(cfg_addr=path), mock.Mock(cfg_addr=("127.0.0.1", 8000))])
            with mock.patch.dict(os.environ, {"SRCF_SOCIETY": "no-such-group-mss"}):
                conf.SOCIETY = "no-such-group-mss"  # no such group here, so it can't be handed over
                conf.when_ready(server)
            server.log.warning.assert_called_once()  # ...and the site keeps working, open as before
            if os.name == "posix":
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o777)

    def test_with_the_served_folder_configured_stamps_follow_what_is_served(self):
        import hashlib
        import os
        import tempfile

        from django.templatetags.static import static

        from core import storage

        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "css"))
            with open(os.path.join(root, "css", "base.css"), "wb") as handle:
                handle.write(b"september")  # what Apache would hand out
            storage._version.cache_clear()
            try:
                with override_settings(STATIC_ROOT=root, STATIC_VERSION_FROM_ROOT=True):
                    served = hashlib.md5(storage.STAMP + b"september", usedforsecurity=False).hexdigest()[:10]
                    self.assertTrue(static("css/base.css").endswith(f"?v={served}"))
                    self.assertRegex(static("css/core.css"), r"\?v=[0-9a-f]{10}$")  # not copied yet: from the source
            finally:
                storage._version.cache_clear()

    def test_every_static_url_carries_the_current_stamp(self):
        import hashlib

        from django.templatetags.static import static

        from core.storage import STAMP, static_sources

        with open(static_sources()["css/base.css"], "rb") as handle:
            expected = hashlib.md5(STAMP + handle.read(), usedforsecurity=False).hexdigest()[:10]
        self.assertTrue(static("css/base.css").endswith(f"?v={expected}"))
