"""User journeys across the calendar, dashboard and message composer."""
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Category, Event, RSVP
from formals import services
from formals.models import FormalBooking
from formals.tests.test_views import member
from inbox.models import DirectMessage, MAX_MESSAGE_LENGTH


class LinkParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs)


class PortalJourneyTests(TestCase):
    def setUp(self):
        self.host = member("journeyhost")
        self.guest = member("journeyguest", "darwin")
        self.creator = member("journeycreator")
        self.category = Category.objects.create(name="Walks", slug="journey-walks")
        self.start = (timezone.now() + timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)

    def formal(self, **changes):
        data = dict(
            title="Open college dinner", college="wolfson", location="Porters' lodge",
            start=self.start, end=self.start + timedelta(hours=2), capacity=3,
            booking_closes_at=self.start - timedelta(days=1), price_text="18 pounds per person",
            booking_mode="instant", invitation_type="open", hosting_confirmed=True,
        )
        data.update(changes)
        return services.create_listing(self.host, **data)

    def calendar(self, **changes):
        params = {"y": self.start.year, "m": self.start.month}
        params.update(changes)
        return self.client.get(reverse("events:calendar"), params)

    def test_calendar_formals_are_opt_in_and_only_available_places_are_shown(self):
        available = self.formal()
        full = self.formal(title="Fully allocated dinner")
        FormalBooking.objects.create(listing=full, guest=self.creator, places=3, status="reserved")
        closed = self.formal(title="Bookings closed dinner")
        closed.booking_closes_at = timezone.now() - timedelta(hours=1)
        closed.save(update_fields=["booking_closes_at"])
        self.client.force_login(self.guest)
        default = self.calendar()
        self.assertFalse(default.context["include_formals"])
        self.assertNotContains(default, available.event.title)
        response = self.calendar(formals="1")
        self.assertContains(response, available.event.title)
        self.assertContains(response, available.get_absolute_url())
        self.assertNotContains(response, full.event.title)
        self.assertNotContains(response, closed.event.title)
        self.assertTrue(response.context["include_formals"])
        self.client.logout()
        anonymous = self.calendar(formals="1")
        self.assertFalse(anonymous.context["include_formals"])
        self.assertNotContains(anonymous, available.event.title)
        self.assertNotContains(anonymous, "Include available formals")

    def test_month_search_and_day_overflow_keep_filters_and_show_every_event(self):
        self.client.force_login(self.guest)
        events = [Event.objects.create(
            title=f"Gathering {number}", category=self.category, start=self.start + timedelta(minutes=number),
            created_by=self.host,
        ) for number in range(5)]
        response = self.calendar(cat=self.category.slug, q="Gathering", view="grid", formals="1")
        for key in ("prev_query", "next_query", "today_query", "list_query"):
            params = parse_qs(urlsplit(response.context[key]).query)
            self.assertEqual(params["formals"], ["1"])
            self.assertEqual(params["cat"], [self.category.slug])
            self.assertEqual(params["q"], ["Gathering"])
            self.assertEqual(params["view"], ["list" if key == "list_query" else "grid"])
        links = LinkParser(response.content.decode()).links
        more = next(link["href"] for link in links if link.get("class") == "cal-more")
        target = urlsplit(more)
        self.assertEqual(target.fragment, f"day-{timezone.localdate(self.start).isoformat()}")
        self.assertEqual(parse_qs(target.query)["view"], ["list"])
        result = self.client.get(reverse("events:calendar") + "?" + target.query)
        self.assertEqual(len(result.context["agenda"][0][1]), 5)
        for event in events:
            self.assertContains(result, event.title)
        self.assertContains(result, f'id="{target.fragment}"')

    def test_dashboard_shows_group_plans_requests_and_the_actual_host(self):
        dinner = self.formal()
        dinner.event.created_by = self.creator
        dinner.event.save(update_fields=["created_by"])
        booking = services.apply_for_place(self.guest, dinner.pk, dinner.terms_version, places=2, accept_terms=True)
        requested = self.formal(title="Choose your guests dinner", booking_mode="approval")
        services.apply_for_place(self.guest, requested.pk, requested.terms_version, accept_terms=True)
        self.client.force_login(self.guest)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual([row.pk for row in response.context["formal_plans"]], [booking.pk, FormalBooking.objects.get(listing=requested).pk])
        self.assertContains(response, "My formal plans")
        self.assertContains(response, "2 places")
        self.assertContains(response, "Awaiting host approval")
        self.client.force_login(self.host)
        response = self.client.get(reverse("dashboard:home"))
        self.assertIn(dinner.event, response.context["running_events"])
        self.assertNotIn(dinner.event, response.context["ordinary_running_events"])
        self.assertEqual(response.context["formal_requests"], 1)
        self.assertTrue(response.context["next_up"].viewer_is_host)
        self.assertContains(response, "Review 1 formal guest request")
        self.client.force_login(self.creator)
        response = self.client.get(reverse("dashboard:home"))
        self.assertNotIn(dinner.event, response.context["running_events"])

    def test_dashboard_activity_does_not_call_future_or_cancelled_events_hosted(self):
        for title, start, cancelled in [
            ("Future host", self.start, False),
            ("Past cancelled host", self.start - timedelta(days=10), True),
            ("Past hosted event", self.start - timedelta(days=10), False),
        ]:
            Event.objects.create(title=title, category=self.category, start=start,
                                 created_by=self.creator, host=self.host, is_cancelled=cancelled)
        self.client.force_login(self.host)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["stats"]["hosted"], 1)
        self.assertContains(response, "My activity")
        self.assertNotContains(response, "Your year in numbers")

    def test_dashboard_shows_return_holds_only_while_the_invitation_is_live(self):
        original = self.formal(
            invitation_type="reciprocal", return_places=1,
            return_by=timezone.localdate(self.start + timedelta(days=30)),
        )
        booking = services.apply_for_place(self.guest, original.pk, original.terms_version,
                                          return_college="darwin", can_host=True, accept_terms=True)
        return_start = self.start + timedelta(days=8)
        returned = services.create_listing(self.guest, title="Return at Darwin", college="darwin",
            location="Porters' lodge", start=return_start, end=return_start + timedelta(hours=2),
            capacity=3, booking_closes_at=return_start - timedelta(days=1), price_text="18 pounds",
            booking_mode="instant", invitation_type="open", hosting_confirmed=True)
        proposal = services.propose_return(self.guest, booking.exchange.pk, returned.pk)
        self.client.force_login(self.host)
        response = self.client.get(reverse("dashboard:home"))
        self.assertIn(proposal.booking.pk, [row.pk for row in response.context["formal_plans"]])
        self.assertContains(response, "Respond to invitation")
        proposal.expires_at = timezone.now() - timedelta(seconds=1)
        proposal.save(update_fields=["expires_at"])
        response = self.client.get(reverse("dashboard:home"))
        self.assertNotIn(proposal.booking.pk, [row.pk for row in response.context["formal_plans"]])

    def test_failed_message_keeps_the_complete_draft_and_can_be_corrected(self):
        self.host.is_portal_admin = True
        self.host.save(update_fields=["is_portal_admin"])
        self.client.force_login(self.guest)
        url = reverse("inbox:thread", args=[self.host.username])
        draft = "<hello> " + "x" * MAX_MESSAGE_LENGTH
        response = self.client.post(url, {"body": draft})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["draft"], draft)
        self.assertContains(response, "&lt;hello&gt;", status_code=400)
        self.assertFalse(DirectMessage.objects.exists())
        response = self.client.post(url, {"body": "A shorter message"})
        self.assertRedirects(response, url)
        self.assertEqual(DirectMessage.objects.get().body, "A shorter message")

    def test_denied_message_preserves_an_unsent_draft_without_a_send_button(self):
        self.client.force_login(self.guest)
        url = reverse("inbox:thread", args=[self.host.username])
        response = self.client.post(url, {"body": "Please keep this unsent text"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Please keep this unsent text", status_code=403)
        self.assertContains(response, "Your message was not sent", status_code=403)
        self.assertContains(response, "readonly", status_code=403)
        self.assertNotContains(response, ">Send</button>", status_code=403)
        self.assertFalse(DirectMessage.objects.exists())
