"""The page editor (Admin > Content > Pages > "Edit this page"), the registry
of wording behind it (core/blocks.py), and pictures added from an editor."""

import datetime
import importlib
import re
import tempfile
from io import BytesIO
from unittest import mock

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core import blocks
from core.models import Picture, SiteConfig, SitePage, TextBlock
from core.templatetags.md import richtext_filter

User = get_user_model()

TEXT_TAG = re.compile(r"""\{%\s*text\s+["']([\w.]+)["']""")
EMPTY_HEADING = re.compile(r"<h[1-6][^>]*>\s*</h[1-6]>")


def make_user(username, **extra):
    """Someone with a finished profile, so no page sends them off to complete it."""
    defaults = dict(first_name="Mia", last_name="Member", college="wolfson", mobile="+44 7700 900001")
    defaults.update(extra)
    return User.objects.create_user(username=username, password="pw", **defaults)


def picture_file(name="garden.png", size=(40, 30), fmt="PNG", content_type="image/png", padding=0):
    buffer = BytesIO()
    Image.new("RGB", size, "green").save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue() + b"\0" * padding, content_type=content_type)


class TempMediaMixin:
    """Uploads go to a folder that is thrown away, and the rate limits start afresh."""

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        override = override_settings(MEDIA_ROOT=folder.name)
        override.enable()
        self.addCleanup(override.disable)
        cache.clear()
        self.addCleanup(cache.clear)


class PageEditorTests(TestCase):
    def setUp(self):
        self.admin = make_user("pe001", first_name="Ada", last_name="Admin", is_portal_admin=True)
        self.member = make_user("pe002")
        self.client.force_login(self.admin)
        self.url = reverse("panel:page_text", args=["about"])

    def form(self, page_key="about", **changes):
        """What a browser would send back from the editing screen: every box
        as it was shown, with its hidden fingerprint, plus the changes."""
        stored = blocks.stored_texts()
        data = {}
        for item in blocks.layout_for(page_key):
            if item["kind"] != "text":
                continue
            key = item["key"]
            text_block = blocks.BLOCKS[key]
            shown = blocks.normalise(text_block["format"], stored.get(key, text_block["default"]))
            data[f"block-{key}"] = shown
            data[f"was-{key}"] = blocks.token(shown)
        for name, value in changes.items():
            data[f"block-{page_key}.{name}"] = value
        return data

    def test_sections_are_listed_in_the_order_of_the_page(self):
        for page_key in ("about", "home", "ball", "supper"):
            body = self.client.get(reverse("panel:page_text", args=[page_key])).content.decode()
            places = [
                body.index(f'name="block-{item["key"]}"') if item["kind"] == "text"
                else body.index(f'<strong>{item["label"]}</strong>')
                for item in blocks.layout_for(page_key)
            ]
            self.assertEqual(places, sorted(places), page_key)
        self.assertContains(self.client.get(self.url), "Edit the committee")
        self.assertContains(self.client.get(self.url), "automatic")

    def test_one_save_changes_several_sections_and_stores_nothing_else(self):
        response = self.client.post(self.url, self.form(
            lede="Older and wiser.", contact_heading="Say hello", body="A **new** start."))
        self.assertRedirects(response, self.url)
        self.assertEqual(
            dict(TextBlock.objects.values_list("key", "text")),
            {"about.lede": "Older and wiser.", "about.contact_heading": "Say hello",
             "about.body": "A **new** start."},
        )
        page = self.client.get(reverse("core:about"))
        for expected in ("Older and wiser.", "Say hello", "<strong>new</strong>", "About Us"):
            self.assertContains(page, expected)
        self.assertContains(self.client.get(reverse("panel:pages")), "3 edited")
        # Pressing Save again with nothing changed stores nothing more.
        self.client.post(self.url, self.form())
        self.assertEqual(TextBlock.objects.count(), 3)

    def test_saving_the_default_wording_removes_the_stored_row(self):
        from panel.models import AuditLog

        self.client.post(self.url, self.form(lede="Older and wiser."))
        self.assertTrue(TextBlock.objects.filter(key="about.lede").exists())
        self.client.post(self.url, self.form(lede=blocks.BLOCKS["about.lede"]["default"]))
        self.assertFalse(TextBlock.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="reset_text", detail="about.lede").exists())

    def test_one_bad_section_means_nothing_is_saved(self):
        response = self.client.post(self.url, self.form(title="", lede="Kept in the box"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing was saved")
        self.assertContains(response, "this one can&#x27;t be left empty")
        self.assertContains(response, 'value="Kept in the box"')  # nothing typed is lost
        self.assertFalse(TextBlock.objects.exists())
        self.assertContains(self.client.get(reverse("core:about")), "About Us")

    def test_wording_that_is_too_long_or_too_heavy_is_refused(self):
        response = self.client.post(self.url, self.form(lede="x" * (blocks.PLAIN_MAX + 1)))
        self.assertContains(response, "keep it to 400 characters or fewer")
        response = self.client.post(self.url, self.form(associates="y" * (blocks.INLINE_MAX + 1)))
        self.assertContains(response, "keep it to 600 characters or fewer")
        home = reverse("panel:page_text", args=["home"])
        response = self.client.post(home, self.form("home", stats="1 | " + "`" * 400))
        self.assertContains(response, "too many")
        response = self.client.post(home, self.form("home", stats="1 | " + "z" * 61000))
        self.assertContains(response, "too long")
        self.assertFalse(TextBlock.objects.exists())

    def test_a_clash_is_refused_once_and_shows_their_wording(self):
        stale = self.form(lede="Mine")
        TextBlock.objects.create(key="about.lede", text="Theirs")
        response = self.client.post(self.url, stale)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "someone else changed")
        self.assertContains(response, "Theirs")
        self.assertContains(response, 'value="Mine"')
        self.assertEqual(TextBlock.objects.get(key="about.lede").text, "Theirs")
        # The screen now holds the new fingerprint, so a second Save goes through.
        self.assertContains(response, 'name="was-about.lede" value="%s"' % blocks.token("Theirs"))
        self.client.post(self.url, self.form(lede="Mine"))
        self.assertEqual(TextBlock.objects.get(key="about.lede").text, "Mine")

    def test_an_untouched_box_never_undoes_a_newer_edit(self):
        stale = self.form(contact_heading="Say hello")
        TextBlock.objects.create(key="about.lede", text="Theirs")
        self.assertRedirects(self.client.post(self.url, stale), self.url)
        self.assertEqual(TextBlock.objects.get(key="about.lede").text, "Theirs")
        self.assertEqual(TextBlock.objects.get(key="about.contact_heading").text, "Say hello")

    def test_back_to_the_default_fills_the_box_and_saves_nothing(self):
        TextBlock.objects.create(key="about.lede", text="Custom line")
        data = self.form(contact_heading="Typed but not saved")
        data["reset"] = "about.lede"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Welcoming Cambridge&#x27;s mature students."')
        self.assertContains(response, "Press Save to keep it.")
        self.assertContains(response, 'value="Typed but not saved"')
        self.assertEqual(dict(TextBlock.objects.values_list("key", "text")), {"about.lede": "Custom line"})
        self.assertContains(self.client.get(reverse("core:about")), "Custom line")

    def test_preview_shows_the_wording_and_saves_nothing(self):
        data = self.form(body="A **bold** claim")
        data["preview"] = "1"
        response = self.client.post(self.url, data)
        self.assertContains(response, "How it will look:")
        self.assertContains(response, "<strong>bold</strong>")
        self.assertFalse(TextBlock.objects.exists())

    def test_only_admins_reach_the_editor(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.form(lede="Hacked")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertFalse(TextBlock.objects.exists())

    def test_pages_with_nothing_to_edit_and_unknown_pages(self):
        self.assertEqual(self.client.get(reverse("panel:page_text", args=["nosuch"])).status_code, 404)
        dashboard = self.client.get(reverse("panel:page_text", args=["dashboard"]))
        self.assertContains(dashboard, "nothing to edit")
        self.assertNotContains(dashboard, 'name="preview"')  # no boxes, so no Save or Preview
        pages = self.client.get(reverse("panel:pages")).content.decode()
        for key in ("supper", "formals", "login", "waitlist", "about"):
            self.assertIn(reverse("panel:page_text", args=[key]), pages)

    def test_an_emptied_optional_heading_leaves_no_empty_heading(self):
        self.client.post(self.url, self.form(contact_heading="", lede=""))
        page = self.client.get(reverse("core:about")).content.decode()
        self.assertNotIn("Get in touch", page)
        self.assertNotIn('<p class="lede"></p>', page)
        self.assertIsNone(EMPTY_HEADING.search(page))
        contacts = reverse("panel:page_text", args=["contacts"])
        self.client.post(contacts, self.form("contacts", confidentiality_heading="", confidentiality=""))
        page = self.client.get(reverse("faq:contacts")).content.decode()
        self.assertNotIn("Confidentiality", page)
        self.assertIsNone(EMPTY_HEADING.search(page))

    def test_the_wellbeing_main_text_starts_from_the_built_in_wording(self):
        SitePage.objects.filter(slug="wellbeing").delete()
        self.assertContains(self.client.get(reverse("core:wellbeing")), "Samaritans")
        editor = self.client.get(reverse("panel:page_text", args=["wellbeing"]))
        self.assertContains(editor, "Create the main text")
        create = reverse("panel:page_body_create", args=["wellbeing"])
        self.assertEqual(self.client.get(create).status_code, 405)
        self.assertFalse(SitePage.objects.filter(slug="wellbeing").exists())
        response = self.client.post(create)
        self.assertRedirects(response, reverse("core:site_page_edit", args=["wellbeing"]))
        page = SitePage.objects.get(slug="wellbeing")
        self.assertIn("Samaritans", page.content)
        self.assertTrue(page.is_published)
        self.assertEqual(page.revisions.count(), 1)
        self.assertContains(self.client.get(reverse("core:wellbeing")), "Samaritans")
        # A second press makes no second page, and the editor now links to it.
        self.client.post(create)
        self.assertEqual(SitePage.objects.filter(slug="wellbeing").count(), 1)
        self.assertContains(self.client.get(reverse("panel:page_text", args=["wellbeing"])), "Edit the main text")
        self.assertEqual(self.client.post(reverse("panel:page_body_create", args=["about"])).status_code, 404)
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(create).status_code, 403)


class BlockRegistryTests(TestCase):
    def test_templates_and_the_registry_agree(self):
        used = set()
        for template in (settings.BASE_DIR / "templates").rglob("*.html"):
            used.update(TEXT_TAG.findall(template.read_text(encoding="utf-8")))
        self.assertEqual(used - set(blocks.BLOCKS), set(), "a template names a block that does not exist")
        self.assertEqual(set(blocks.BLOCKS) - used, set(), "a block no template shows")

    def test_every_block_is_in_its_own_pages_layout_exactly_once(self):
        placed = {}
        for page_key, layout in blocks.LAYOUT.items():
            self.assertIn(page_key, blocks.PAGES_BY_KEY)
            for item in layout:
                self.assertIn(item["kind"], ("text", "link", "auto", "body"))
                if item["kind"] == "text":
                    placed.setdefault(item["key"], []).append(page_key)
                if item["kind"] == "link":
                    self.assertTrue(reverse(item["url_name"]).startswith("/"))
        for key, text_block in blocks.BLOCKS.items():
            self.assertEqual(placed.get(key), [text_block["page"]], key)
        self.assertEqual(set(placed) - set(blocks.BLOCKS), set())
        for page in blocks.PAGES:
            self.assertIn(page["key"], blocks.LAYOUT, "every fixed page has a layout")
            self.assertTrue(reverse(page["url"]).startswith("/"))

    def test_every_default_is_wording_the_editor_would_accept(self):
        for key, text_block in blocks.BLOCKS.items():
            fmt = text_block["format"]
            self.assertIn(fmt, blocks.FORMATS, key)
            default = blocks.normalise(fmt, text_block["default"])
            if fmt == "plain":
                self.assertLessEqual(len(default), blocks.PLAIN_MAX, key)
            if fmt == "inline":
                self.assertLessEqual(len(default), blocks.INLINE_MAX, key)
            if text_block["required"]:
                self.assertTrue(default, key)
            self.assertIsNone(blocks.problem_with(text_block, default), key)

    def test_with_the_defaults_the_pages_read_as_before(self):
        cache.clear()
        expected = (
            ("core:about", ("About Us", "Get in touch", "Welcoming Cambridge&#x27;s mature students.")),
            ("accounts:login", ("Pick your door below.",)),
            ("accounts:waitlist", ("join the waitlist",)),
            ("core:wellbeing", ("Wellbeing",)),
            ("core:policies", ("Community policies",)),
        )
        for name, phrases in expected:
            page = self.client.get(reverse(name))
            for phrase in phrases:
                self.assertContains(page, phrase, msg_prefix=name)
            self.assertIsNone(EMPTY_HEADING.search(page.content.decode()), name)
        self.client.force_login(make_user("br001"))
        expected = (
            ("faq:contacts", ("Confidentiality", "Who to contact")),
            ("faq:departments", ("Whatever your subject",)),
            ("faq:colleges", ("Colleges",)),
            ("supper:index", ("Supper Club",)),
            ("formals:index", ("Formal Exchange",)),
            ("guide:index", ("Guide",)),
            ("testimonials:index", ("Testimonials",)),
        )
        for name, phrases in expected:
            page = self.client.get(reverse(name))
            self.assertEqual(page.status_code, 200, name)
            for phrase in phrases:
                self.assertContains(page, phrase, msg_prefix=name)
            self.assertIsNone(EMPTY_HEADING.search(page.content.decode()), name)


class PictureUploadTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("pu001", first_name="Ada", last_name="Admin", is_portal_admin=True)
        self.member = make_user("pu002")
        self.url = reverse("core:picture_upload")

    def upload(self, **data):
        data.setdefault("image", picture_file())
        data.setdefault("alt", "The garden")
        return self.client.post(self.url, data)

    def test_an_admin_adds_a_picture_and_everyone_can_see_it(self):
        from panel.models import AuditLog

        self.client.force_login(self.admin)
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        picture = Picture.objects.get()
        self.assertEqual(picture.uploaded_by, self.admin)
        self.assertTrue(picture.image.name.startswith("public/pictures/"))
        answer = response.json()
        self.assertEqual(answer["markdown"], f"![The garden]({picture.image.url})")
        self.assertEqual(answer["url"], picture.image.url)
        self.assertTrue(AuditLog.objects.filter(action="upload_picture").exists())
        self.client.logout()
        served = self.client.get(picture.image.url)
        self.assertEqual(served.status_code, 200)
        served.close()  # Windows cannot delete a file that is still open

    def test_page_editors_and_group_owners_may_add_pictures_but_members_may_not(self):
        from events.models import Category

        self.client.force_login(self.member)
        refused = self.upload()
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"], "You can't add pictures here.")
        self.assertFalse(Picture.objects.exists())
        editor = make_user("pu003")
        SitePage.objects.create(title="Sponsors", slug="sponsors", content="x").editors.add(editor)
        self.client.force_login(editor)
        self.assertEqual(self.upload().status_code, 200)
        owner = make_user("pu004")
        Category.objects.create(name="Choir", slug="choir").owners.add(owner)
        self.client.force_login(owner)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(Picture.objects.count(), 2)
        self.client.logout()
        anonymous = self.upload()
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/accounts/login/", anonymous.url)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_bad_uploads_get_a_plain_message_and_store_nothing(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.upload(alt="  ").json()["error"], "Please describe the picture.")
        missing = self.client.post(self.url, {"alt": "No file"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"], "Choose a picture first.")
        for name, content_type in (("notes.txt", "text/plain"), ("notes.jpg", "image/jpeg")):
            text = SimpleUploadedFile(name, b"just some words", content_type=content_type)
            self.assertEqual(self.upload(image=text).status_code, 400, name)
        too_big = picture_file(padding=settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024)
        refused = self.upload(image=too_big)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("MB or smaller", refused.json()["error"])
        for fmt, name, content_type in (("BMP", "garden.bmp", "image/bmp"), ("TIFF", "garden.tiff", "image/tiff")):
            refused = self.upload(image=picture_file(name, fmt=fmt, content_type=content_type))
            self.assertEqual(refused.status_code, 400, fmt)
            self.assertEqual(refused.json()["error"], "Please use a JPEG, PNG, WebP or GIF picture.", fmt)
        with mock.patch("core.images.MAX_PIXELS", 1000):
            refused = self.upload()  # 40 x 30 is 1,200 pixels
        self.assertEqual(refused.status_code, 400)
        self.assertIn("too large", refused.json()["error"])
        self.assertFalse(Picture.objects.exists())

    def test_the_twenty_first_picture_in_an_hour_is_refused(self):
        self.client.force_login(self.admin)
        for _ in range(20):
            self.assertEqual(self.upload().status_code, 200)
        refused = self.upload()
        self.assertEqual(refused.status_code, 429)
        self.assertIn("Try again in an hour", refused.json()["error"])
        self.assertEqual(Picture.objects.count(), 20)

    def test_square_brackets_in_the_description_cannot_break_the_line(self):
        self.client.force_login(self.admin)
        answer = self.upload(alt="A [lovely](https://evil.example) garden").json()
        picture = Picture.objects.get()
        self.assertEqual(answer["markdown"], f"![A (lovely)(https://evil.example) garden]({picture.image.url})")
        html = str(richtext_filter(answer["markdown"]))
        self.assertIn(f'src="{picture.image.url}"', html)
        self.assertNotIn("<a ", html)

    def test_the_editors_offer_add_a_picture_to_those_who_may(self):
        from events.models import Category

        page = SitePage.objects.create(title="Sponsors", slug="sponsors", content="x")
        tag = Category.objects.create(name="Choir", slug="choir")
        marker = 'data-upload="/pictures/upload/"'
        self.client.force_login(self.admin)
        for url in (
            reverse("core:site_page_edit", args=[page.slug]),
            reverse("panel:page_edit", args=[page.pk]),
            reverse("panel:page_create"),
            reverse("panel:page_text", args=["about"]),
            reverse("events:tag_edit", args=[tag.slug]),
            reverse("guide:new"),
        ):
            self.assertContains(self.client.get(url), marker, msg_prefix=url)
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse("guide:new")), "data-upload")
        # The mailer writes emails, which carry no pictures.
        self.client.force_login(self.admin)
        mailer = self.client.get(reverse("panel:mailer"))
        self.assertNotContains(mailer, "data-upload")
        self.assertContains(mailer, 'data-editor-tools="Bold,Italic,Heading,List,Quote,Link"')


class PictureRenderingTests(TestCase):
    def test_pictures_are_marked_for_the_stylesheet_and_load_lazily(self):
        html = str(richtext_filter("![The garden](/media/public/pictures/a.jpg)"))
        self.assertIn('src="/media/public/pictures/a.jpg"', html)
        self.assertIn('alt="The garden"', html)
        self.assertIn('class="rich-img"', html)
        self.assertIn('loading="lazy"', html)
        self.assertIn('decoding="async"', html)

    def test_a_class_or_handler_typed_by_the_writer_is_dropped(self):
        html = str(richtext_filter(
            '<img src="/media/public/pictures/a.jpg" alt="x" class="hero" loading="eager" onerror="alert(1)">'))
        self.assertNotIn("hero", html)
        self.assertNotIn("eager", html)
        self.assertNotIn("onerror", html)
        self.assertEqual(html.count("class="), 1)
        self.assertEqual(html.count("loading="), 1)

    def test_an_inline_block_keeps_its_link_and_has_no_paragraph(self):
        text_block = blocks.BLOCKS["about.associates"]
        self.assertEqual(text_block["format"], "inline")
        html = str(blocks.render_value(text_block, "Ask [the committee](https://example.org/x) **today**"))
        self.assertIn('<a href="https://example.org/x"', html)
        self.assertIn("<strong>today</strong>", html)
        self.assertNotIn("<p>", html)
        self.assertNotIn("<script>", str(blocks.render_value(text_block, "<script>alert(1)</script>")))
        plain = blocks.render_value(blocks.BLOCKS["about.lede"], "Two\nlines   here")
        self.assertEqual(plain, "Two lines here")


class PicturesInUseTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("pi001", first_name="Ada", last_name="Admin", is_portal_admin=True)
        self.picture = Picture.objects.create(image="public/pictures/garden.jpg", alt="The garden")
        self.other = Picture.objects.create(image="public/pictures/pond.jpg", alt="The pond")
        self.line = self.picture.markdown()
        self.client.force_login(self.admin)

    def test_uses_are_found_wherever_a_page_can_show_a_picture(self):
        from core.pictures import picture_uses
        from events.models import Category, Event
        from guide.models import GuidePage

        self.assertEqual(picture_uses([self.picture, self.other]), {self.picture.pk: [], self.other.pk: []})
        now = timezone.now()
        tag = Category.objects.create(name="Choir", slug="choir", page_content=f"Rehearsals\n\n{self.line}")
        TextBlock.objects.create(key="about.body", text=f"Hello\n\n{self.line}")
        SitePage.objects.create(title="Sponsors", slug="sponsors", content=self.line)
        GuidePage.objects.create(title="Gardens", slug="gardens", section="colleges", content=self.line)
        Event.objects.create(title="Summer picnic", category=tag, created_by=self.admin,
                             start=now + datetime.timedelta(days=3), description=self.line)
        Event.objects.create(title="Last year's picnic", category=tag, created_by=self.admin,
                             start=now - datetime.timedelta(days=30), description=self.line)
        uses = picture_uses([self.picture, self.other])
        self.assertEqual(
            sorted(uses[self.picture.pk]),
            ["About MSS", "Event: Summer picnic", "Group: Choir", "Guide: Gardens", "Sponsors"],
        )
        self.assertEqual(uses[self.other.pk], [])

    def test_a_picture_in_use_cannot_be_deleted_until_it_is_taken_off(self):
        block = TextBlock.objects.create(key="about.body", text=f"Hello\n\n{self.line}")
        tab = self.client.get(reverse("panel:pictures"))
        self.assertContains(tab, "Used on: About MSS")
        self.assertContains(tab, "Not used on any page.")
        self.assertNotContains(tab, reverse("panel:picture_delete", args=[self.picture.pk]))
        self.assertContains(tab, reverse("panel:picture_delete", args=[self.other.pk]))
        response = self.client.post(reverse("panel:picture_delete", args=[self.picture.pk]), follow=True)
        self.assertContains(response, "is used on About MSS")
        self.assertTrue(Picture.objects.filter(pk=self.picture.pk).exists())
        self.client.post(reverse("panel:picture_delete", args=[self.other.pk]))
        self.assertFalse(Picture.objects.filter(pk=self.other.pk).exists())
        block.delete()
        self.client.post(reverse("panel:picture_delete", args=[self.picture.pk]))
        self.assertFalse(Picture.objects.exists())


class AboutLedeMigrationTests(TestCase):
    """0018 keeps a customised tagline showing under the About page's title."""

    def setUp(self):
        self.migration = importlib.import_module("core.migrations.0018_about_lede_block")
        self.config = SiteConfig.get()
        self.addCleanup(cache.clear)

    def run_it(self, tagline):
        SiteConfig.objects.filter(pk=self.config.pk).update(tagline=tagline)
        self.migration.copy_tagline(django_apps, None)

    def test_a_customised_tagline_is_copied(self):
        self.run_it("  For students   over 21. ")
        self.assertEqual(TextBlock.objects.get(key="about.lede").text, "For students over 21.")
        cache.clear()
        self.assertContains(self.client.get(reverse("core:about")), '<p class="lede">For students over 21.</p>')

    def test_the_default_or_an_empty_tagline_is_not(self):
        self.assertEqual(self.migration.DEFAULT, blocks.BLOCKS["about.lede"]["default"])
        self.assertEqual(self.migration.DEFAULT, SiteConfig._meta.get_field("tagline").default)
        self.run_it(self.migration.DEFAULT)
        self.run_it("")
        self.assertFalse(TextBlock.objects.exists())

    def test_an_existing_line_is_left_alone(self):
        TextBlock.objects.create(key="about.lede", text="Already chosen")
        self.run_it("Something else")
        self.assertEqual(TextBlock.objects.get(key="about.lede").text, "Already chosen")

    def test_nothing_happens_without_a_settings_row(self):
        SiteConfig.objects.all().delete()
        self.migration.copy_tagline(django_apps, None)
        self.assertFalse(TextBlock.objects.exists())
