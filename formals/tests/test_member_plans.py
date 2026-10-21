"""Keep unfinished plans visible and preserve context when arranging a return."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import Category, Event
from formals.models import FormalBooking, FormalExchange, FormalListing, ReturnProposal


class MemberPlanTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.host = User.objects.create_user(username="planhost", first_name="Alice", last_name="Host", college="wolfson", mobile="07700900111")
        self.guest = User.objects.create_user(username="planguest", first_name="Ben", last_name="Guest", college="darwin", mobile="07700900112")
        self.category = Category.objects.create(name="Formal Exchange", slug="formal-exchange")
        self.number = 0

    def listing(self, host=None, days=7, **extra):
        self.number += 1
        host = host or self.host
        start = self.now + timedelta(days=days)
        event = Event.objects.create(title=f"Dinner {self.number}", category=self.category, start=start,
                                     end=start + timedelta(hours=2), location="College hall", created_by=host,
                                     host=host, members_only=True, is_cancelled=extra.pop("cancelled", False))
        return FormalListing.objects.create(event=event, college=host.college,
                                            booking_closes_at=start - timedelta(days=1), **extra)

    def booking(self, listing, status, **extra):
        return FormalBooking.objects.create(listing=listing, guest=self.guest, status=status, **extra)

    def test_past_reservations_stay_visible_until_attendance_is_confirmed(self):
        pending = self.booking(self.listing(days=-4), "reserved")
        future = self.booking(self.listing(days=5), "reserved")
        attended = self.booking(self.listing(days=-9), "attended")
        cancelled = self.booking(self.listing(days=9), "cancelled")
        expired_request = self.booking(self.listing(days=-2), "requested")
        self.client.force_login(self.guest)
        response = self.client.get(reverse("formals:mine"))
        self.assertEqual([b.pk for b in response.context["bookings"]], [pending.pk, future.pk])
        self.assertEqual({b.pk for b in response.context["past_bookings"]}, {attended.pk, cancelled.pk, expired_request.pk})

    def test_hosts_see_pending_attendance_before_finished_history(self):
        awaiting = self.listing(days=-4)
        self.booking(awaiting, "reserved")
        finished = self.listing(days=-7)
        self.booking(finished, "attended")
        upcoming = self.listing(days=8)
        cancelled = self.listing(days=9, cancelled=True)
        self.client.force_login(self.host)
        response = self.client.get(reverse("formals:mine"))
        self.assertEqual([l.pk for l in response.context["listings"]], [awaiting.pk, upcoming.pk])
        self.assertEqual({l.pk for l in response.context["past_listings"]}, {finished.pk, cancelled.pk})

    def exchange(self, status="conditional", days=7):
        listing = self.listing(days=days, invitation_type="reciprocal", return_by=(self.now + timedelta(days=60)).date())
        booking = self.booking(listing, "reserved", return_college="darwin")
        return FormalExchange.objects.create(original_booking=booking, creditor=self.host, debtor=self.guest,
                                              return_college="darwin", return_by=listing.return_by, status=status)

    def test_terminal_exchanges_and_expired_holds_move_to_history(self):
        original = self.listing(host=self.guest, days=-2, invitation_type="reciprocal")
        original_booking = FormalBooking.objects.create(listing=original, guest=self.host, status="attended")
        active = FormalExchange.objects.create(
            original_booking=original_booking, creditor=self.guest, debtor=self.host,
            return_college="wolfson", return_by=(self.now + timedelta(days=60)).date(), status="owed",
        )
        completed = self.exchange("completed")
        closed = self.exchange("closed")
        held = self.booking(self.listing(days=12), "held", purpose="return")
        ReturnProposal.objects.create(exchange=active, booking=held, expires_at=self.now - timedelta(hours=1))
        self.client.force_login(self.guest)
        response = self.client.get(reverse("formals:mine"), {"past_exchange_page": "1"})
        self.assertEqual([e.pk for e in response.context["exchanges"]], [active.pk])
        self.assertEqual({e.pk for e in response.context["past_exchanges"]}, {completed.pk, closed.pk})
        self.assertIn(held.pk, {b.pk for b in response.context["past_bookings"]})
        self.assertTrue(response.context["show_history"])

    def return_data(self, **extra):
        start = timezone.localtime(self.now + timedelta(days=21))
        data = dict(title="Darwin return dinner", college="darwin", location="Darwin lodge",
                    start=start.strftime("%Y-%m-%dT%H:%M"), end="", capacity=2,
                    booking_closes_at=(start - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                    price_text="Guest pays college ticket", invitation_type="open", booking_mode="approval",
                    hosting_confirmed="on")
        data.update(extra)
        return data

    def test_creating_return_dinner_leads_back_to_the_correct_swap(self):
        swap = self.exchange()
        self.client.force_login(self.guest)
        url = reverse("formals:create")
        response = self.client.get(url, {"return_for": swap.pk})
        self.assertEqual(response.context["return_exchange"], swap)
        self.assertContains(response, 'name="return_for"')
        response = self.client.post(url, self.return_data(return_for=swap.pk))
        self.assertRedirects(response, swap.get_absolute_url())
        self.assertTrue(FormalListing.objects.filter(event__title="Darwin return dinner").exists())
        self.assertFalse(swap.proposals.exists())  # The member explicitly sends the invitation next.

    def test_return_dinner_context_rejects_wrong_dates_and_other_members(self):
        swap = self.exchange()
        self.client.force_login(self.host)
        self.assertEqual(self.client.get(reverse("formals:create"), {"return_for": swap.pk}).status_code, 403)
        self.client.force_login(self.guest)
        after_deadline = timezone.localtime(self.now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M")
        before = FormalListing.objects.count()
        response = self.client.post(reverse("formals:create"), self.return_data(return_for=swap.pk, start=after_deadline))
        self.assertFormError(response.context["form"], "start", "Choose a date on or before the agreed return deadline.")
        self.assertEqual(FormalListing.objects.count(), before)
