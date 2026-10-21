"""Member journeys exposed directly by the compact formal interface."""
from datetime import timedelta
from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from formals import services
from formals.models import FormalBooking
from formals.tests.test_views import member


class IdParser(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.ids = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        value = dict(attrs).get("id")
        if value:
            self.ids.append(value)


class CompactInterfaceTests(TestCase):
    def setUp(self):
        self.host = member("compacthost")
        self.guest = member("compactguest", "darwin")
        self.start = timezone.now() + timedelta(days=8)
        self.client.force_login(self.guest)

    def dinner(self, **changes):
        data = dict(
            title="Dinner together", college="wolfson", location="Porters' lodge",
            start=self.start, end=self.start + timedelta(hours=2),
            capacity=3, booking_mode="instant", invitation_type="reciprocal",
            booking_closes_at=self.start - timedelta(days=1), price_text="18 pounds per person",
            allowed_return_colleges=["darwin"], return_by=timezone.localdate(self.start + timedelta(days=30)),
            return_places=1, hosting_confirmed=True,
        )
        data.update(changes)
        return services.create_listing(self.host, **data)

    def test_reserve_a_group_directly_from_browse_with_host_selected_return_quantity(self):
        dinner = self.dinner()
        another = self.dinner(title="A second dinner", start=self.start + timedelta(days=1), end=self.start + timedelta(days=1, hours=2))
        response = self.client.get(reverse("formals:index"))
        self.assertContains(response, f'action="{reverse("formals:apply", args=[dinner.pk])}"')
        self.assertContains(response, f'action="{reverse("formals:apply", args=[another.pk])}"')
        self.assertContains(response, "1 return place")
        self.assertContains(response, "Share to WhatsApp")
        ids = IdParser(response.content.decode()).ids
        self.assertEqual(len(ids), len(set(ids)), "Repeated booking forms need unique field IDs.")
        response = self.client.post(reverse("formals:apply", args=[dinner.pk]), {
            "terms_version": dinner.terms_version, "places": 2,
            "return_college": "darwin", "accept_terms": "on", "next": "browse",
            "companion_names": "Jamie Example",
        })
        self.assertRedirects(response, reverse("formals:index"))
        booking = FormalBooking.objects.get(listing=dinner, guest=self.guest)
        self.assertEqual((booking.places, booking.exchange.return_places), (2, 1))
        response = self.client.get(reverse("formals:index"))
        self.assertContains(response, "2 places &middot; Reserved")
        self.assertContains(response, "1 place available")

    def test_group_request_shows_return_quantity_before_an_exchange_exists(self):
        dinner = self.dinner(booking_mode="approval", return_places=2)
        booking = services.apply_for_place(
            self.guest, dinner.pk, dinner.terms_version, places=3,
            return_college="darwin", can_host=True, accept_terms=True,
        )
        self.assertEqual(booking.status, "requested")
        response = self.client.get(dinner.get_absolute_url())
        self.assertContains(response, "2 places promised at Darwin.")
        self.client.force_login(self.host)
        response = self.client.get(dinner.get_absolute_url())
        self.assertContains(response, "Accept 3 places")
        self.assertContains(response, "0 of 3 places allocated")

    def test_group_attendance_screen_names_each_guest_and_single_booking_has_absence_option(self):
        dinner = self.dinner(invitation_type="open", return_places=0)
        group = services.apply_for_place(
            self.guest, dinner.pk, dinner.terms_version, places=2,
            companion_names=["Jamie Example"], accept_terms=True,
        )
        single_guest = member("compactsingle", "darwin")
        single = services.apply_for_place(single_guest, dinner.pk, dinner.terms_version, accept_terms=True)
        dinner.event.start = timezone.now() - timedelta(hours=3)
        dinner.event.end = timezone.now() - timedelta(hours=1)
        dinner.event.save(update_fields=["start", "end"])
        self.client.force_login(self.host)
        response = self.client.get(dinner.get_absolute_url())
        self.assertContains(response, "Jamie Example")
        self.assertContains(response, f'id="attendance-{group.pk}-attended_places_1"')
        self.assertContains(response, f'id="attendance-{single.pk}-attended_places_0"')
        self.assertContains(response, "Record different attendance")
        self.assertContains(response, "Leave all unchecked if nobody attended.")
