"""Posters: the layout, the studio, printing, the short link and the map."""

import base64
import datetime
from unittest import mock
from urllib.parse import unquote

from django.core.cache import cache
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
        cache.clear()
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
        self.assertNotContains(response, "Save as the default")
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
        response = self.client.get(reverse("posters:print", args=[self.event.slug]) + "?size=a4&print_marks=on")
        self.assertContains(response, 'viewBox="-3 -3 216 303"')
        response = self.client.get(reverse("posters:print", args=[self.event.slug]) + "?size=a3&print_marks=on")
        self.assertContains(response, "@page { size: 303mm 426mm;")
        # 3 mm of A3 is 2.12 units of its 210-unit width, so the trim stays 297 by 420 mm.
        self.assertContains(response, 'viewBox="-2.12121 -2.12121 214.242 301.242"')

    def test_event_page_offers_the_button_and_counts_scans(self):
        self.client.force_login(self.host)
        self.assertContains(self.client.get(self.event.get_absolute_url()), "Make a poster")
        response = self.client.get(reverse("poster_scan", args=[self.event.slug]), HTTP_USER_AGENT="Mozilla/5.0 (iPhone)")
        self.assertRedirects(response, self.event.get_absolute_url())
        self.assertEqual(PosterScan.objects.get().device, "phone")
        self.assertContains(self.client.get(self.event.get_absolute_url()), "1 poster scan so far")
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(self.event.get_absolute_url()), "poster scan")


LAYOUT_QUERIES = {
    "classic": "template=classic&size=a4", "bold": "template=bold&size=a4",
    "photo": "template=photo&size=a4", "square": "size=square", "story": "size=story",
}


def _with_key():
    config = SiteConfig.get()
    config.geoapify_api_key = "test-key"
    config.save()


def _payload(**result):
    import json

    return json.dumps({"results": [result] if result else []}).encode()


class MapTests(PosterTestCase):
    def _place(self):
        _with_key()
        self.event.latitude, self.event.longitude = 52.2, 0.1
        self.event.geocoded_at = timezone.now()
        self.event.save()

    def test_no_key_means_no_map_one_qr_code_and_a_note_saying_why(self):
        self.client.force_login(self.member)
        body = self.client.get(self.svg + "?show_map=on&show_qr=on").content.decode()
        self.assertNotIn("map.png", body)
        self.assertEqual(body.count("qrline"), 1)  # the RSVP code only, never a second one
        self.assertEqual(self.client.get(reverse("posters:map", args=[self.event.slug])).status_code, 404)
        self.assertContains(self.client.get(self.studio), "maps aren&#x27;t switched on")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.studio), "Super admin tab")

    def test_with_a_key_the_venue_is_geocoded_once_and_the_map_is_proxied(self):
        _with_key()
        asks = len(geocode.candidates(self.event.location))
        with mock.patch.object(geocode, "_fetch", return_value=_payload(lat=52.211, lon=0.115)) as fetch:
            self.assertTrue(geocode.ensure_geocoded(self.event))
            self.assertTrue(geocode.ensure_geocoded(self.event))  # remembered: no second round
        self.assertEqual(fetch.call_count, asks)
        self.event.refresh_from_db()
        self.assertAlmostEqual(self.event.latitude, 52.211)
        self.client.force_login(self.member)
        response = self.client.get(self.svg + "?show_map=on")
        self.assertIn("/map.png?w=", response.content.decode())
        self.assertEqual(response["X-Map-Note"], "")
        with mock.patch.object(geocode, "_fetch", return_value=b"PNGBYTES"):
            response = self.client.get(reverse("posters:map", args=[self.event.slug]) + "?w=470&h=240")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content, b"PNGBYTES")

    def test_broader_parts_of_the_venue_are_tried_and_named_places_win(self):
        self.assertEqual(
            geocode.candidates("The Old Hall, Queens' College, Silver Street"),
            ["The Old Hall, Queens' College, Silver Street", "Queens' College, Silver Street", "Queens' College"],
        )
        self.assertEqual(geocode.candidates("The Eagle"), ["The Eagle"])
        _with_key()

        def answer(url, timeout=None):
            if "Old+Hall" in url:
                return _payload(lat=52.2018, lon=0.1152, result_type="street")
            if "Silver+Street" in url:
                return _payload()
            return _payload(lat=52.2025, lon=0.1147, result_type="amenity")

        with mock.patch.object(geocode, "_fetch", side_effect=answer):
            self.assertEqual(geocode.geocode("The Old Hall, Queens' College, Silver Street"), (52.2025, 0.1147))

    def test_a_match_for_the_whole_city_counts_as_not_found(self):
        _with_key()
        self.event.location = "Parkers Piece"
        self.event.save()
        with mock.patch.object(geocode, "_fetch", return_value=_payload(lat=52.2099, lon=0.1219, result_type="city")):
            self.assertFalse(geocode.ensure_geocoded(self.event))
        self.event.refresh_from_db()
        self.assertIsNone(self.event.latitude)
        self.assertIsNotNone(self.event.geocoded_at)
        self.client.force_login(self.member)
        self.assertContains(self.client.get(self.studio), "couldn&#x27;t be found")

    def test_super_admin_sets_and_clears_the_key(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:superadmin_maps"), {"geoapify_api_key": "abc123xyz789"})
        self.assertEqual(SiteConfig.get().geoapify_api_key, "abc123xyz789")
        self.client.post(reverse("panel:superadmin_maps"), {"geoapify_api_key": "CLEAR"})
        self.assertEqual(SiteConfig.get().geoapify_api_key, "")
        self.assertEqual(AuditLog.objects.filter(action="update_map_key").count(), 2)

    def test_a_failed_lookup_is_retried_but_no_match_is_remembered(self):
        _with_key()
        asks = len(geocode.candidates(self.event.location))
        with mock.patch.object(geocode, "_fetch", side_effect=TimeoutError) as fetch:
            self.assertFalse(geocode.ensure_geocoded(self.event))
            self.assertFalse(geocode.ensure_geocoded(self.event))  # waiting: not asked again yet
        self.assertEqual(fetch.call_count, asks)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.geocoded_at)  # a failure is not written off
        self.client.force_login(self.member)
        self.assertContains(self.client.get(self.studio), "didn&#x27;t answer in time")
        cache.clear()  # the wait is over
        with mock.patch.object(geocode, "_fetch", return_value=_payload()) as fetch:
            self.assertFalse(geocode.ensure_geocoded(self.event))
            self.assertFalse(geocode.ensure_geocoded(self.event))
        self.assertEqual(fetch.call_count, asks)  # no such place: remembered, not asked again
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.geocoded_at)
        self.event.geocoded_at = timezone.now() - datetime.timedelta(days=8)
        self.event.save()
        with mock.patch.object(geocode, "_fetch", return_value=_payload(lat=52.2, lon=0.1)):
            self.assertTrue(geocode.ensure_geocoded(self.event))  # a week on, worth another look

    def test_saving_an_event_waits_only_briefly_for_the_venue(self):
        from events.views import _geocode_quietly

        with mock.patch.object(geocode, "ensure_geocoded") as ensure:
            _geocode_quietly(self.event)
        ensure.assert_called_once_with(self.event, timeout=geocode.SAVE_TIMEOUT)

    def test_a_jpeg_map_is_labelled_as_one(self):
        self._place()
        self.client.force_login(self.member)
        picture = geocode.JPEG_MAGIC + b"rest of the picture"
        with mock.patch.object(geocode, "_fetch", return_value=picture):
            response = self.client.get(reverse("posters:map", args=[self.event.slug]))
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response.content, picture)

    def test_map_sizes_are_kept_in_bounds(self):
        self._place()
        self.client.force_login(self.member)
        with mock.patch.object(geocode, "fetch_static_map", return_value=b"PNG") as fetch:
            self.client.get(reverse("posters:map", args=[self.event.slug]) + "?w=99999&h=nonsense")
        fetch.assert_called_once_with(52.2, 0.1, layout.MAP_MAX_PX, 300)

    def test_a_location_of_only_commas_is_simply_not_found(self):
        _with_key()
        self.event.location = " , ,"
        self.event.save()
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.studio).status_code, 200)
        self.assertEqual(self.client.get(self.svg).status_code, 200)

    def test_a_street_waits_while_a_lookup_that_might_name_the_building_failed(self):
        _with_key()

        def answer(url, timeout=None):
            if "Old+Hall" in url:
                return _payload(lat=52.2018, lon=0.1152, result_type="street")
            raise TimeoutError

        with mock.patch.object(geocode, "_fetch", side_effect=answer):
            self.assertIsNone(geocode.geocode("The Old Hall, Queens' College, Silver Street"))
        with mock.patch.object(geocode, "_fetch", return_value=_payload(lat=52.2018, lon=0.1152, result_type="street")):
            self.assertEqual(geocode.geocode("The Old Hall, Queens' College, Silver Street"), (52.2018, 0.1152))

    def test_absurd_map_sizes_are_clamped_not_a_crash(self):
        self._place()
        self.client.force_login(self.member)
        with mock.patch.object(geocode, "fetch_static_map", return_value=b"PNG") as fetch:
            response = self.client.get(reverse("posters:map", args=[self.event.slug]) + "?w=1" + "0" * 400 + "&h=-5")
        self.assertEqual(response.status_code, 200)
        fetch.assert_called_once_with(52.2, 0.1, layout.MAP_MAX_PX, 100)

    def test_a_square_with_a_photo_says_why_it_has_no_map(self):
        self._place()
        self.client.force_login(self.member)
        response = self.client.get(self.svg + "?size=square&device_photo=1")
        self.assertNotIn("map.png", response.content.decode())
        self.assertIn("square", unquote(response["X-Map-Note"]))
        self.assertEqual(self.client.get(self.svg + "?size=story&device_photo=1")["X-Map-Note"], "")

    def test_clicks_pass_through_the_photo_templates_shade(self):
        self.client.force_login(self.member)
        body = self.client.get(self.svg + "?template=photo&size=a4").content.decode()
        self.assertIn('fill="url(#shade)" pointer-events="none"', body)

    def test_every_layout_has_one_qr_code_and_the_map(self):
        self._place()
        self.client.force_login(self.member)
        for name, query in LAYOUT_QUERIES.items():
            for photo in ("", "&device_photo=1"):
                with self.subTest(layout=name, photo=bool(photo)):
                    body = self.client.get(f"{self.svg}?{query}&show_map=on&show_qr=on{photo}").content.decode()
                    self.assertEqual(body.count("qrline"), 1)
                    if name == "square" and photo:
                        continue  # a square with a photo has no room for the map too
                    self.assertIn("/map.png?w=", body)

    def test_without_a_photo_the_map_takes_the_pictures_place(self):
        self._place()
        self.client.force_login(self.member)
        body = self.client.get(self.svg + "?template=classic&size=a4").content.decode()
        self.assertLess(body.index("map.png"), body.index(">Quiz night"))  # map above the title
        body = self.client.get(self.svg + "?template=classic&size=a4&device_photo=1").content.decode()
        self.assertGreater(body.index("map.png"), body.index(">Quiz night"))  # beside the QR code instead

    def test_the_downloaded_svg_carries_its_own_pictures(self):
        self._place()
        self.client.force_login(self.member)
        with mock.patch.object(geocode, "_fetch", return_value=b"\x89PNG fake map"):
            body = self.client.get(self.svg + "?download=1").content.decode()
        self.assertNotIn('href="/', body)
        self.assertIn('href="data:image/png;base64,', body)
        self.assertIn(base64.b64encode(b"\x89PNG fake map").decode(), body)

    def test_print_page_waits_for_pictures_and_takes_the_device_photo(self):
        self.client.force_login(self.member)
        url = reverse("posters:print", args=[self.event.slug])
        response = self.client.get(url + "?size=a4&print=1&device_photo=1")
        self.assertContains(response, "js/poster-print.js")
        self.assertContains(response, 'data-device-photo="1"')
        self.assertContains(response, 'data-auto-print="1"')
        self.assertNotContains(self.client.get(url + "?size=a4"), "data-device-photo")


class FitTests(PosterTestCase):
    """Long titles, long venues and every option on: nothing may overlap the
    QR code or the map, and everything stays on the sheet."""

    def _boxes(self, scene):
        texts, solids = [], []
        for el in scene.items:
            if el["t"] == "text":
                if el["anchor"] == "middle":
                    continue  # the QR code's own label
                width = max(layout.fonts.width(line, el["key"], el["size"], el["weight"]) + el["ls"] * len(line)
                            for line in el["lines"])
                left = el["x"] - width if el["anchor"] == "end" else el["x"]  # right-aligned runs end at x
                texts.append((el["lines"][0], left, el["top"], left + width, el["top"] + el["dy"] * len(el["lines"])))
            elif el["t"] == "qr":
                solids.append(("qr", el["x"], el["y"], el["x"] + el["size"], el["y"] + el["size"]))
            elif el["t"] == "image" and el.get("map"):
                solids.append(("map", el["x"], el["y"], el["x"] + el["w"], el["y"] + el["h"]))
        return texts, solids

    def _variants(self):
        """Long words everywhere; then a long date with an end time, a title
        joined with no-break spaces and the longest extra line allowed."""
        yield "long", "Free · bring a dish"
        self.event.title = "\u00a0".join("Quiz night at the Castle Inn with prizes".split())
        self.event.start = timezone.make_aware(datetime.datetime(2026, 9, 30, 19, 30))
        self.event.end = timezone.make_aware(datetime.datetime(2026, 9, 30, 22, 45))
        yield "dates", "Wear something warm, we will be outside for most of the even"

    def test_nothing_overlaps_the_qr_code_or_the_map(self):
        from django.test import RequestFactory

        self.event.title = "Michaelmas Networking Potluck and Research Flashtalks: Climate, Code and Colleges"
        self.event.location = "Lecture Theatre 1, Yusuf Hamied Department of Chemistry, Lensfield Road"
        self.event.description = "Bring a dish to share and five minutes on your research. " * 8
        request = RequestFactory().get("/")
        for variant, extra in self._variants():
          for size, template in (("a4", "classic"), ("a4", "bold"), ("a4", "photo"), ("square", "classic"), ("story", "classic")):
            for photo in (False, True):
                for map_href in ("", "/posters/x/map.png"):
                    settings = EventPoster(event=self.event, size=size, template=template, extra_line=extra)
                    scene = layout.build_scene(self.event, settings, request, map_href, photo)
                    texts, solids = self._boxes(scene)
                    with self.subTest(variant=variant, size=size, template=template, photo=photo, map=bool(map_href)):
                        for line, tx1, ty1, tx2, ty2 in texts:
                            self.assertLessEqual(tx2, scene.w - scene.u * 0.5, f"{line!r} runs off the sheet")
                        for name, x1, y1, x2, y2 in solids:
                            self.assertGreaterEqual(x1, -0.01)
                            self.assertLessEqual(x2, scene.w + 0.01)
                            self.assertLessEqual(y2, scene.h + 0.01)
                            for line, tx1, ty1, tx2, ty2 in texts:
                                overlaps = tx1 < x2 - 0.01 and x1 < tx2 - 0.01 and ty1 < y2 - 0.01 and y1 < ty2 - 0.01
                                self.assertFalse(overlaps, f"{line!r} overlaps the {name}")
                        if len(solids) == 2:
                            (_, ax1, ay1, ax2, ay2), (_, bx1, by1, bx2, by2) = solids
                            self.assertFalse(ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2, "QR code and map overlap")
                        if template == "bold" and size == "a4":
                            badge = next(el for el in scene.items  # the logo's rounded paper badge, not the sheet
                                         if el["t"] == "rect" and el["fill"] == layout.PAPER and el["rx"])
                            day = next(el for el in scene.items if el["t"] == "text" and el["lines"][0].isdigit())
                            ink_top = day["y"] - layout.fonts.ink_height(day["lines"][0], "display", 700) * day["size"]
                            self.assertGreaterEqual(ink_top, badge["y"] + badge["h"], "the date runs into the logo")
