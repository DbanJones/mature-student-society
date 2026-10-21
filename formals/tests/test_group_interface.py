"""End-to-end shortcuts preserve capacity, privacy and explicit arrangements."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import SiteConfig
from formals import services
from formals.forms import GuestNamesForm
from formals.models import FormalBooking, FormalListing
from inbox.models import DirectMessage, MessageBlock
from inbox.policy import can_message


class GroupInterfaceTests(TestCase):
    def setUp(self):
        def member(name, college):
            return User.objects.create_user(username=name, first_name=name.title(), last_name="Member", college=college, mobile="07700900000", terms_accepted_version=999)
        self.host = member("grouphost", "wolfson")
        self.guest = member("groupguest", "darwin")
        self.other = member("groupother", "clare")
        self.outsider = member("groupoutsider", "darwin")
        self.start = timezone.localtime(timezone.now() + timedelta(days=10)).replace(hour=19, minute=0, second=0, microsecond=0)
        self.data = dict(title="Dinner for members", college="wolfson", start=self.start, end=self.start + timedelta(hours=2), location="Lodge", capacity=3, booking_closes_at=self.start - timedelta(days=1), price_text="£18 per person", invitation_type="open", booking_mode="instant", hosting_confirmed=True, return_places=0)
        self.listing = services.create_listing(self.host, **self.data)
        config = SiteConfig.get()
        config.messaging_mode = SiteConfig.MessagingMode.RESTRICTED
        config.save()

    def reserve(self, user, **changes):
        return services.apply_for_place(user, self.listing.pk, self.listing.terms_version, accept_terms=True, **changes)

    def post_data(self, **changes):
        data = dict(self.data, **changes)
        for name in ("start", "end", "booking_closes_at"):
            data[name] = data[name].strftime("%Y-%m-%dT%H:%M") if data[name] else ""
        return data

    def test_inline_group_reservation_records_host_selected_return_places(self):
        self.listing.invitation_type = "reciprocal"
        self.listing.return_by = (self.start + timedelta(days=60)).date()
        self.listing.return_places = 1
        self.listing.save()
        self.client.force_login(self.guest)
        response = self.client.get(reverse("formals:index"))
        listing = response.context["page"][0]
        self.assertIsNotNone(listing.application)
        self.assertContains(response, f'booking-{listing.pk}-places')
        response = self.client.post(reverse("formals:apply", args=[listing.pk]), {"terms_version": 1, "places": 2, "companion_names": "Lee", "return_college": "darwin", "accept_terms": "on", "next": "browse"})
        self.assertRedirects(response, reverse("formals:index"))
        booking = FormalBooking.objects.get(guest=self.guest, listing=listing)
        self.assertEqual(booking.places, 2)
        self.assertEqual(booking.companion_names, ["Lee"])
        self.assertEqual(booking.exchange.return_places, 1)
        self.assertEqual(self.listing.spots_left, 1)

    def test_invalid_names_stay_visible_and_do_not_allocate(self):
        self.client.force_login(self.guest)
        response = self.client.post(reverse("formals:apply", args=[self.listing.pk]), {"terms_version": 1, "places": 2, "companion_names": "Lee\nJo", "accept_terms": "on"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("companion_names", response.context["application"].errors)
        self.assertContains(response, "Lee", status_code=400)
        self.assertFalse(self.listing.bookings.exists())

    def test_multiple_dates_publish_together_with_shared_arrangements(self):
        self.client.force_login(self.host)
        data = self.post_data(**{"dates-TOTAL_FORMS": 2, "dates-INITIAL_FORMS": 0, "dates-0-date": (self.start + timedelta(days=7)).date().isoformat(), "dates-1-date": (self.start + timedelta(days=14)).date().isoformat()})
        response = self.client.post(reverse("formals:create"), data)
        self.assertRedirects(response, reverse("formals:mine"))
        self.assertEqual(FormalListing.objects.count(), 4)
        latest = FormalListing.objects.order_by("-event__start").first()
        self.assertEqual(timezone.localtime(latest.event.start).time(), self.start.time())
        self.assertEqual(latest.event.start - latest.booking_closes_at, timedelta(days=1))
        self.assertEqual(latest.return_places, 0)

    def test_duplicate_or_invalid_bulk_dates_publish_nothing(self):
        self.client.force_login(self.host)
        for day in (self.start.date(), (self.start - timedelta(days=20)).date()):
            response = self.client.post(reverse("formals:create"), self.post_data(**{"dates-TOTAL_FORMS": 1, "dates-INITIAL_FORMS": 0, "dates-0-date": day.isoformat()}))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["date_formset"].errors)
            self.assertEqual(FormalListing.objects.count(), 1)

    def test_bulk_dates_outside_datetime_range_publish_nothing(self):
        self.client.force_login(self.host)
        for day in ("0001-01-01", "9999-12-31"):
            with self.subTest(day=day):
                data = self.post_data(end=self.start + timedelta(hours=6), **{"dates-TOTAL_FORMS": 1, "dates-INITIAL_FORMS": 0, "dates-0-date": day})
                response = self.client.post(reverse("formals:create"), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["date_formset"].errors)
                self.assertEqual(FormalListing.objects.count(), 1)

    def test_names_form_preserves_unnamed_seat_positions(self):
        booking = self.reserve(self.guest, places=3, companion_names=["", "Jo"])
        form = GuestNamesForm({"companion_names": "\nJo"}, booking=booking)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["companion_names"], ["", "Jo"])

    def test_whatsapp_summary_links_to_private_page_without_roster(self):
        self.reserve(self.guest, places=2, companion_names=["Private Companion"])
        self.client.force_login(self.other)
        response = self.client.get(self.listing.get_absolute_url())
        share = response.context["whatsapp_url"]
        self.assertEqual(urlsplit(share).netloc, "wa.me")
        text = parse_qs(urlsplit(share).query)["text"][0]
        self.assertIn("£18 per person", text)
        self.assertIn(self.listing.get_absolute_url(), text)
        self.assertIn("sign in", text)
        self.assertNotIn("Private Companion", text)
        self.assertNotIn(self.guest.get_full_name(), text)
        self.client.logout()
        self.assertEqual(self.client.get(self.listing.get_absolute_url()).status_code, 302)

    def test_confirmed_members_can_message_until_start_only(self):
        self.reserve(self.guest)
        self.reserve(self.other)
        self.assertTrue(can_message(self.guest, self.other))
        self.assertTrue(can_message(self.other, self.guest))
        self.assertFalse(can_message(self.outsider, self.guest))
        self.client.force_login(self.guest)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertIn(self.other, response.context["messaging_members"])
        with patch("django.utils.timezone.now", return_value=self.start):
            self.assertFalse(can_message(self.guest, self.other))
            response = self.client.get(self.listing.get_absolute_url())
            self.assertFalse(response.context["messaging_members"])
            self.client.post(reverse("inbox:thread", args=[self.other.username]), {"body": "Too late"})
            self.assertFalse(DirectMessage.objects.exists())

    def test_requests_cancellations_and_blocks_do_not_grant_peer_messaging(self):
        first = self.reserve(self.guest)
        other = self.reserve(self.other)
        MessageBlock.objects.create(user=self.guest, blocked=self.other)
        self.assertFalse(can_message(self.guest, self.other))
        MessageBlock.objects.all().delete()
        for status in ("requested", "held", "cancelled", "withdrawn"):
            other.status = status
            other.save(update_fields=["status"])
            self.assertFalse(can_message(self.guest, self.other))
        self.client.force_login(self.outsider)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertFalse(response.context["messaging_members"])
        self.assertNotContains(response, self.guest.get_full_name())

    def test_minimum_places_filter_uses_places_not_booking_count(self):
        self.reserve(self.guest, places=2)
        self.client.force_login(self.other)
        response = self.client.get(reverse("formals:index"), {"min_places": 2})
        self.assertFalse(response.context["page"])

    def test_invalid_guest_name_edit_preserves_draft(self):
        booking = self.reserve(self.guest, places=2)
        self.client.force_login(self.guest)
        response = self.client.post(reverse("formals:booking_action", args=[booking.pk, "guest-names"]), {"companion_names": "Lee\nJo"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["my_booking"].attendee_form["companion_names"].value(), "Lee\nJo")
        self.assertContains(response, "Your changes have not been saved", status_code=400)
        booking.refresh_from_db()
        self.assertEqual(booking.companion_names, [])

    def test_attendance_failure_preserves_selected_people(self):
        booking = self.reserve(self.guest, places=2)
        # Attendance cannot be reported before the dinner has happened.
        self.client.force_login(self.guest)
        response = self.client.post(reverse("formals:booking_action", args=[booking.pk, "attendance"]), {"attendance_submitted": "True", "attended_places": ["0"]})
        self.assertEqual(response.status_code, 400)
        form = response.context["my_booking"].attendance_form
        self.assertEqual(form["attended_places"].value(), ["0"])
        self.assertTrue(form.non_field_errors())
        booking.refresh_from_db()
        self.assertIsNone(booking.guest_attendance)

    def test_blocked_host_has_no_message_shortcut(self):
        self.reserve(self.guest)
        MessageBlock.objects.create(user=self.host, blocked=self.guest)
        self.client.force_login(self.guest)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertNotContains(response, reverse("inbox:thread", args=[self.host.username]))

    def test_disputed_attendance_shows_both_reports(self):
        booking = self.reserve(self.guest, places=2, companion_names=["Lee"])
        booking.status = "no_show"
        booking.host_attendance = [0]
        booking.guest_attendance = [1]
        booking.save()
        self.client.force_login(self.guest)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertEqual(response.context["my_booking"].host_reported_names, [self.guest.get_full_name()])
        self.assertEqual(response.context["my_booking"].guest_reported_names, ["Lee"])
        self.assertContains(response, "Host reported")
        self.assertContains(response, "Guest reported")
