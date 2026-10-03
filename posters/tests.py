"""Posters: the layout, the studio, printing, the short link and the map."""

import datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import SiteConfig
from events.models import Category, Event
from panel.models import AuditLog

from . import geocode, layout
from .models import EventPoster, PosterScan


def make_user(username, **extra):
    defaults = dict(
        first_name="Test", last_name=username.title(), college="wolfson",
        mobile="+447700900000", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class PosterTestCase(TestCase):
    def setUp(self):
        self.host = make_user("host1")
        self.member = make_user("mem1")
        self.admin = make_user("adm1", is_portal_admin=True, is_super_admin=True)
        self.category = Category.objects.create(name="Pub Nights", slug="pub-nights", color="#8a5a2c", emoji="🍺")
        self.event = Event.objects.create(
            title="Quiz night at the Castle Inn", category=self.category,
            description="A **friendly** quiz. [Details](https://example.com) and more words.",
            location="The Castle Inn, 38 Castle Street", capacity=40,
            start=timezone.now() + datetime.timedelta(days=9), created_by=self.host, host=self.host,
        )
        self.studio = reverse("posters:studio", args=[self.event.slug])
        self.svg = reverse("posters:svg", args=[self.event.slug])


class LayoutTests(PosterTestCase):
    def test_plain_text_strips_markdown_and_caps_words(self):
        text = layout.plain_text("A **bold** [link](https://x) " + "word " * 80)
        self.assertNotIn("**", text)
        self.assertNotIn("https://", text)
        self.assertIn("link", text)
        self.assertTrue(text.endswith("…"))
        self.assertLessEqual(len(text.split()), 61)

    def test_pale_tag_colours_are_darkened_for_white_text(self):
        self.assertEqual(layout.readable_on_white("#b82818"), "#b82818")
        darker = layout.readable_on_white("#f2e39a")
        self.assertNotEqual(darker, "#f2e39a")
        self.assertGreaterEqual(1.05 / (layout._luminance(layout._hex_to_rgb(darker)) + 0.05), 4.5)

    def test_cover_box_keeps_the_focal_point_in_view(self):
        x, y, w, h = layout.cover_box((0, 0, 100, 50), (400, 400), (0.5, 0.5))
        self.assertEqual((w, h), (100, 100))
        self.assertEqual((x, y), (0, -25))
        x, y, w, h = layout.cover_box((0, 0, 100, 50), (400, 400), (0.5, 0.0))
        self.assertEqual(y, 0)  # top of the image stays at the top of the panel

    def test_every_field_reaches_the_scene(self):
        from django.test import RequestFactory
        settings = EventPoster(event=self.event, extra_line="£5 on the door", show_map=False)
        scene = layout.build_scene(self.event, settings, RequestFactory().get("/"))
        joined = " ".join(line for el in scene.items if el["t"] == "text" for line in el["lines"])
        for expected in ("Quiz night", "£5 on the door", "Castle Inn", "40, book early", "Test Host1", "friendly quiz"):
            self.assertIn(expected, joined)
        self.assertEqual(sum(1 for el in scene.items if el["t"] == "qr"), 1)

    def test_long_titles_step_down(self):
        from django.test import RequestFactory
        self.event.title = "An extraordinarily long and rambling title for an event that goes on and on"
        settings = EventPoster(event=self.event)
        scene = layout.build_scene(self.event, settings, RequestFactory().get("/"))
        title_el = next(el for el in scene.items if el["t"] == "text" and "rambling" in " ".join(el["lines"]))
        self.assertLess(title_el["size"], 9.2 * scene.u)
        self.assertLessEqual(len(title_el["lines"]), 3)


class StudioTests(PosterTestCase):
    def test_studio_and_svg_need_login_and_render_for_any_member(self):
        self.assertEqual(self.client.get(self.studio).status_code, 302)
        self.client.force_login(self.member)
        response = self.client.get(self.studio)
        self.assertContains(response, "Make a poster")
        self.assertContains(response, "Quiz night at the Castle Inn")
        self.assertContains(response, 'class="poster-svg"')
        self.assertNotContains(response, "Save settings for everyone")
        svg = self.client.get(self.svg + "?template=bold&size=square")
        self.assertEqual(svg["Content-Type"], "image/svg+xml; charset=utf-8")
        body = svg.content.decode()
        self.assertIn('viewBox="0 0 1080 1080"', body)
        self.assertIn("qrline", body)
        from django.test import RequestFactory
        content = layout.Content(self.event, EventPoster(event=self.event), RequestFactory().get("/"))
        self.assertTrue(content.scan_url.endswith(f"/p/{self.event.slug}/"))

    def test_overrides_in_the_query_never_save(self):
        self.client.force_login(self.member)
        response = self.client.get(self.studio + "?template=photo&headline=Pub+quiz&extra_line=Free+chips")
        self.assertContains(response, "Free chips")
        self.assertFalse(EventPoster.objects.exists())

    def test_organiser_saves_settings_and_member_cannot(self):
        self.client.force_login(self.member)
        self.client.post(self.studio, {"template": "bold", "size": "a4", "accent": "tag"})
        self.assertFalse(EventPoster.objects.exists())
        self.client.force_login(self.host)
        response = self.client.post(self.studio, {
            "template": "bold", "size": "a3", "accent": "gold", "headline": "Quiz!",
            "extra_line": "£5 on the door", "show_qr": "on", "show_map": "on",
            "focal_x": "0.2", "focal_y": "0.9",
        })
        self.assertRedirects(response, self.studio)
        saved = EventPoster.objects.get(event=self.event)
        self.assertEqual((saved.template, saved.size, saved.accent), ("bold", "a3", "gold"))
        self.assertEqual(saved.extra_line, "£5 on the door")
        self.assertEqual(saved.saved_by, self.host)
        self.assertFalse(saved.show_description)
        # Everyone now starts from the saved settings.
        self.client.force_login(self.member)
        self.assertContains(self.client.get(self.svg), "Quiz!")

    def test_print_page_sizes_and_bleed(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("posters:print", args=[self.event.slug]) + "?size=a4")
        self.assertContains(response, "@page { size: A4;")
        self.assertContains(response, "Print / Save as PDF")
        response = self.client.get(reverse("posters:print", args=[self.event.slug]) + "?size=a3&print_marks=on")
        self.assertContains(response, "@page { size: 303mm 426mm;")
        self.assertContains(response, 'viewBox="-3 -3 216 303"')

    def test_event_page_offers_the_button_and_counts_scans(self):
        self.client.force_login(self.host)
        self.assertContains(self.client.get(self.event.get_absolute_url()), "Make a poster")
        response = self.client.get(reverse("poster_scan", args=[self.event.slug]), HTTP_USER_AGENT="Mozilla/5.0 (iPhone)")
        self.assertRedirects(response, self.event.get_absolute_url())
        self.assertEqual(PosterScan.objects.get().device, "phone")
        self.assertContains(self.client.get(self.event.get_absolute_url()), "1 poster scan so far")
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(self.event.get_absolute_url()), "poster scan")


class MapTests(PosterTestCase):
    def test_no_key_means_no_map_and_a_directions_code(self):
        self.client.force_login(self.member)
        body = self.client.get(self.svg + "?show_map=on&show_qr=on").content.decode()
        self.assertNotIn("map.png", body)
        self.assertEqual(body.count("qrline"), 2)  # RSVP code plus directions code
        self.assertEqual(self.client.get(reverse("posters:map", args=[self.event.slug])).status_code, 404)

    def test_with_a_key_the_venue_is_geocoded_once_and_the_map_is_proxied(self):
        config = SiteConfig.get()
        config.geoapify_api_key = "test-key"
        config.save()
        payload = b'{"results": [{"lat": 52.211, "lon": 0.115}]}'
        with mock.patch.object(geocode, "_fetch", return_value=payload) as fetch:
            self.assertTrue(geocode.ensure_geocoded(self.event))
            self.assertTrue(geocode.ensure_geocoded(self.event))  # cached: no second call
        self.assertEqual(fetch.call_count, 1)
        self.event.refresh_from_db()
        self.assertAlmostEqual(self.event.latitude, 52.211)
        self.client.force_login(self.member)
        body = self.client.get(self.svg + "?show_map=on").content.decode()
        self.assertIn("map.png", body)
        with mock.patch.object(geocode, "_fetch", return_value=b"PNGBYTES"):
            response = self.client.get(reverse("posters:map", args=[self.event.slug]))
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content, b"PNGBYTES")

    def test_super_admin_sets_and_clears_the_key(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:superadmin_maps"), {"geoapify_api_key": "abc123xyz789"})
        self.assertEqual(SiteConfig.get().geoapify_api_key, "abc123xyz789")
        self.client.post(reverse("panel:superadmin_maps"), {"geoapify_api_key": "CLEAR"})
        self.assertEqual(SiteConfig.get().geoapify_api_key, "")
        self.assertEqual(AuditLog.objects.filter(action="update_map_key").count(), 2)
