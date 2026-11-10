"""The Winter Ball event links through to the Winter Ball page.

The site menu has ``href="/winter-ball/"`` on every page, so these tests
never look for that string alone: they check the link around the event's
title, and that the event's own address is gone from the page.
"""

import datetime

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from core import ball
from core.models import SiteConfig
from core.tests import make_member
from events.models import RSVP, Category, Event
from panel import services as panel_services
from panel.models import AuditLog
from posters.models import PosterScan

BALL_TITLE = "MSS Bridgemas Formal (Winter Ball)"


class BallTestCase(TestCase):
    def setUp(self):
        self.config = SiteConfig.get()
        self.member = make_member("wb001")
        self.admin = make_member("wb002", is_portal_admin=True)
        self.category = Category.objects.create(name="Socials", slug="wb-socials", emoji="🎉")
        self.ball = self.make(BALL_TITLE, days=20, is_super=True, location="Churchill College")
        self.other = self.make("Quiz Night", days=5)
        self.ball_page = reverse("core:winter_ball")

    def make(self, title, days=10, **extra):
        return Event.objects.create(
            title=title, category=self.category, created_by=self.admin,
            start=timezone.now() + datetime.timedelta(days=days), **extra,
        )

    def set_tab(self, visibility):
        self.config.tab_visibility = {"ball": visibility}
        self.config.save()

    def move(self, event, **delta):
        event.start = timezone.now() + datetime.timedelta(**delta)
        event.save()

    def title_link(self, address=None, before=""):
        """The end of the link around the ball's title, as the lists write it."""
        return f'href="{address or self.ball_page}">{before}{BALL_TITLE}</a>'


class BallEventChoiceTests(BallTestCase):
    def test_finds_the_next_super_event_with_winter_ball_in_its_title(self):
        self.assertEqual(ball.find_event(), self.ball)

    def test_ignores_events_that_are_not_super(self):
        self.ball.is_super = False
        self.ball.save()
        self.make("Winter Ball dress swap", is_official=True)
        self.make("Winter Ball pre-drinks")
        self.assertIsNone(ball.find_event())

    def test_ignores_cancelled_events(self):
        self.ball.is_cancelled = True
        self.ball.save()
        self.assertIsNone(ball.find_event())

    def test_an_admins_choice_wins(self):
        self.config.ball_event = self.other
        self.config.save()
        self.assertEqual(ball.find_event(), self.other)

    def test_a_stale_choice_falls_back_to_the_name(self):
        self.config.ball_event = self.other
        self.config.save()
        self.move(self.other, days=-3)
        self.assertEqual(ball.find_event(), self.ball)

    def test_the_event_still_counts_until_twelve_hours_after_it_starts(self):
        self.move(self.ball, hours=-2)
        self.assertEqual(ball.find_event(), self.ball)
        self.move(self.ball, hours=-13)
        self.assertIsNone(ball.find_event())

    def test_deleting_the_chosen_event_clears_the_choice(self):
        self.config.ball_event = self.other
        self.config.save()
        self.other.delete()
        self.config.refresh_from_db()
        self.assertIsNone(self.config.ball_event_id)


class BallLinkTests(BallTestCase):
    def assert_ball_links_to_its_page(self, response, before=""):
        self.assertContains(response, self.title_link(before=before))
        self.assertNotContains(response, self.ball.get_absolute_url())

    def test_home_links_the_ball_to_its_page(self):
        response = self.client.get(reverse("core:home"))
        self.assert_ball_links_to_its_page(response)
        self.assertContains(response, self.other.get_absolute_url())

    def test_calendar_links_the_ball_to_its_page(self):
        start = timezone.localtime(self.ball.start)
        response = self.client.get(reverse("events:calendar"), {"y": start.year, "m": start.month})
        # The agenda list, the month grid and "Coming up" all carry the event.
        self.assertContains(response, 'class="agenda-title" ' + self.title_link(before="🌟 "))
        self.assertContains(response, 'class="up-title" ' + self.title_link())
        self.assertNotContains(response, self.ball.get_absolute_url())
        self.assertContains(response, self.other.get_absolute_url())

    def test_search_links_the_ball_to_its_page(self):
        response = self.client.get(reverse("core:search"), {"q": "Bridgemas"})
        self.assert_ball_links_to_its_page(response, before="🎉 ")
        response = self.client.get(reverse("core:search"), {"q": "Quiz"})
        self.assertContains(response, self.other.get_absolute_url())

    def test_dashboard_links_the_ball_to_its_page(self):
        RSVP.objects.create(event=self.ball, user=self.member, status=RSVP.Status.GOING)
        RSVP.objects.create(event=self.other, user=self.member, status=RSVP.Status.GOING)
        self.client.force_login(self.member)
        response = self.client.get(reverse("dashboard:home"))
        self.assert_ball_links_to_its_page(response)
        self.assertContains(response, self.other.get_absolute_url())

    def test_tag_and_groups_pages_link_the_ball_to_its_page(self):
        self.move(self.other, days=30)  # so the ball is the tag's next event
        response = self.client.get(self.category.get_absolute_url())
        self.assert_ball_links_to_its_page(response)
        self.assertContains(response, self.other.get_absolute_url())
        response = self.client.get(reverse("events:groups"))
        self.assert_ball_links_to_its_page(response)

    def test_member_profile_links_the_ball_to_its_page(self):
        # A profile lists the events its member is hosting next.
        self.client.force_login(self.member)
        response = self.client.get(reverse("members:profile", args=[self.admin.username]))
        self.assert_ball_links_to_its_page(response)
        self.assertContains(response, self.other.get_absolute_url())

    def test_other_events_keep_their_own_page(self):
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, f'href="{self.other.get_absolute_url()}">Quiz Night</a>')

    def test_hidden_tab_leaves_links_on_the_event_page(self):
        self.set_tab("hidden")
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, self.title_link(self.ball.get_absolute_url()))
        self.assertNotContains(response, self.title_link())

    def test_members_tab_links_only_for_members(self):
        self.set_tab("members")
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, self.title_link(self.ball.get_absolute_url()))
        self.assertNotContains(response, self.title_link())
        self.client.force_login(self.member)
        response = self.client.get(reverse("core:home"))
        self.assert_ball_links_to_its_page(response)

    def test_event_page_has_a_banner_to_the_ball_page(self):
        response = self.client.get(self.ball.get_absolute_url())
        self.assertContains(response, "This is the Winter Ball")
        self.assertContains(response, f'<a href="{self.ball_page}">Winter Ball page</a>')
        response = self.client.get(self.other.get_absolute_url())
        self.assertNotContains(response, "This is the Winter Ball")

    def test_event_page_has_no_banner_when_the_tab_is_hidden(self):
        self.set_tab("hidden")
        response = self.client.get(self.ball.get_absolute_url())
        self.assertNotContains(response, "This is the Winter Ball")

    def test_whatsapp_share_points_at_the_ball_page(self):
        self.client.force_login(self.member)
        response = self.client.get(self.ball.get_absolute_url())
        self.assertIn("/winter-ball/", response.context["share_url"])
        self.assertNotIn(self.ball.slug, response.context["share_url"])
        # The sender can't know who will tap it: members-only means no.
        self.set_tab("members")
        response = self.client.get(self.ball.get_absolute_url())
        self.assertIn(self.ball.slug, response.context["share_url"])
        self.assertNotIn("/winter-ball/", response.context["share_url"])

    def test_poster_scan_goes_to_the_ball_page(self):
        response = self.client.get(reverse("poster_scan", args=[self.ball.slug]))
        self.assertRedirects(response, self.ball_page, fetch_redirect_response=False)
        self.assertEqual(PosterScan.objects.filter(event=self.ball).count(), 1)
        response = self.client.get(reverse("poster_scan", args=[self.other.slug]))
        self.assertRedirects(
            response, self.other.get_absolute_url(), fetch_redirect_response=False,
        )

    @override_settings(SITE_URL="")
    def test_mailer_link_is_the_ball_page(self):
        request = RequestFactory().get("/")
        self.assertEqual(
            panel_services.event_link(request, self.ball), "http://testserver/winter-ball/",
        )
        self.assertEqual(
            panel_services.event_link(request, self.other),
            "http://testserver" + self.other.get_absolute_url(),
        )
        self.set_tab("admins")
        request = RequestFactory().get("/")
        self.assertEqual(
            panel_services.event_link(request, self.ball),
            "http://testserver" + self.ball.get_absolute_url(),
        )

    @override_settings(SITE_URL="")
    def test_mailer_draft_sends_the_ball_to_its_page(self):
        self.move(self.ball, days=6)  # inside the mailer's two weeks
        request = RequestFactory().get("/")
        _subject, body = panel_services.build_whats_on_email(request)
        self.assertIn("[RSVP and details](http://testserver/winter-ball/)", body)
        self.assertNotIn(self.ball.get_absolute_url(), body)
        self.assertIn(
            f"[RSVP and details](http://testserver{self.other.get_absolute_url()})", body,
        )


class BallPageTests(BallTestCase):
    def page(self):
        return self.client.get(self.ball_page)

    def test_logged_out_visitor_sees_the_date_and_a_login_link(self):
        response = self.page()
        self.assertContains(response, "Churchill College")
        self.assertContains(response, "?next=/winter-ball/")
        self.assertContains(response, "Log in to reserve your place")
        self.assertNotContains(response, 'class="ball-rsvp"')

    def test_member_reserves_in_one_press(self):
        self.client.force_login(self.member)
        rsvp_url = reverse("events:rsvp", args=[self.ball.slug])
        response = self.page()
        self.assertContains(response, f'action="{rsvp_url}"')
        self.assertContains(response, "Reserve your place")
        response = self.client.post(rsvp_url, {"action": "join"})
        self.assertRedirects(
            response, self.ball.get_absolute_url(), fetch_redirect_response=False,
        )
        rsvp = RSVP.objects.get(event=self.ball, user=self.member)
        self.assertEqual(rsvp.status, RSVP.Status.GOING)
        response = self.page()
        self.assertContains(response, "on the list. View your booking")
        self.assertNotContains(response, 'class="ball-rsvp"')

    def test_full_event_offers_the_waiting_list(self):
        self.ball.capacity = 1
        self.ball.save()
        RSVP.objects.create(
            event=self.ball, user=make_member("wb003"), status=RSVP.Status.GOING,
        )
        self.client.force_login(self.member)
        response = self.page()
        self.assertContains(response, "Join the waiting list")
        self.assertContains(response, "fully booked")
        self.client.post(reverse("events:rsvp", args=[self.ball.slug]), {"action": "join"})
        rsvp = RSVP.objects.get(event=self.ball, user=self.member)
        self.assertEqual(rsvp.status, RSVP.Status.WAITING)
        response = self.page()
        self.assertContains(response, "on the waiting list. View your place")

    def test_members_only_event_keeps_its_details_from_the_public(self):
        self.ball.members_only = True
        self.ball.save()
        response = self.page()
        self.assertNotContains(response, "Churchill College")
        self.assertNotContains(response, self.ball.get_absolute_url())
        self.assertContains(response, "Log in to see the date and reserve")
        self.client.force_login(self.member)
        self.assertContains(self.page(), "Churchill College")

    def test_no_event_is_a_save_the_date_page(self):
        self.ball.delete()
        self.assertContains(self.page(), "date to be announced")

    def test_bookings_close_once_it_has_started(self):
        self.move(self.ball, hours=-1)
        self.client.force_login(self.member)
        response = self.page()
        self.assertContains(response, "Bookings have closed.")
        self.assertNotContains(response, 'class="ball-rsvp"')

    def test_existing_page_text_still_renders(self):
        self.assertContains(self.page(), 'class="ball-timeline"')

    def test_organiser_is_offered_the_event_page_to_manage_it(self):
        self.assertContains(self.page(), "Event details and map")
        self.client.force_login(self.admin)
        self.assertContains(self.page(), "Manage this event")


class BallAdminTests(BallTestCase):
    def setUp(self):
        super().setUp()
        self.chooser = reverse("panel:ball_event")

    def test_only_admins_reach_the_chooser(self):
        response = self.client.get(self.chooser)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.chooser).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(self.chooser)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Showing now")
        self.assertContains(response, BALL_TITLE)

    def test_admin_chooses_and_clears_the_event(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.chooser, {"event": self.other.pk})
        self.assertRedirects(response, self.chooser, fetch_redirect_response=False)
        self.config.refresh_from_db()
        self.assertEqual(self.config.ball_event_id, self.other.pk)
        self.assertTrue(AuditLog.objects.filter(action="set_ball_event").exists())
        quiz_day = date_format(timezone.localtime(self.other.start), "l j F Y")
        self.assertContains(self.client.get(self.ball_page), quiz_day)
        # Listings follow the choice too.
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, f'href="{self.ball_page}">Quiz Night</a>')
        self.assertContains(response, self.title_link(self.ball.get_absolute_url()))

        self.client.post(self.chooser, {"event": ""})
        self.config.refresh_from_db()
        self.assertIsNone(self.config.ball_event_id)
        ball_day = date_format(timezone.localtime(self.ball.start), "l j F Y")
        self.assertContains(self.client.get(self.ball_page), ball_day)

    def test_chooser_refuses_an_event_that_is_over(self):
        old = self.make("Last week's social", days=-3)
        self.client.force_login(self.admin)
        response = self.client.post(self.chooser, {"event": old.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.config.refresh_from_db()
        self.assertIsNone(self.config.ball_event_id)

    def test_pages_panel_and_ball_page_offer_the_chooser_to_admins(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:pages")), self.chooser)
        self.assertContains(self.page_for_viewer(), self.chooser)
        self.client.force_login(self.member)
        self.assertNotContains(self.page_for_viewer(), self.chooser)

    def page_for_viewer(self):
        return self.client.get(self.ball_page)
