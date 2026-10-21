"""Member privacy, form validation and end-to-end reservation journeys."""
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import RSVP
from formals import services
from formals.forms import ListingForm
from formals.models import FormalBooking, FormalExchange, FormalListing, ReturnProposal


def member(username, college="wolfson", **extra):
    return User.objects.create_user(
        username=username, first_name=username.title(), last_name="Member",
        college=college, mobile="07700900000", email=f"{username}@example.test",
        terms_accepted_version=999, **extra,
    )


class FormalViewTests(TestCase):
    def setUp(self):
        self.host = member("formalhost")
        self.guest = member("formalguest", "darwin")
        self.other = member("formalother", "darwin")
        self.admin = member("formaladmin", is_portal_admin=True)
        self.start = (timezone.now() + timedelta(days=7)).replace(second=0, microsecond=0)
        self.listing = services.create_listing(self.host, **self.listing_data())

    def listing_data(self, **extra):
        data = dict(
            title="Wolfson autumn formal", description="A shared dinner",
            location="Wolfson porters' lodge", start=self.start,
            end=self.start + timedelta(hours=2), college="wolfson", capacity=3,
            invitation_type="open", booking_mode="approval", allowed_return_colleges=[],
            booking_closes_at=self.start - timedelta(days=1), return_by=None,
            price_text="£18 paid to the host", practical_details="Smart dress",
            hosting_confirmed=True,
        )
        data.update(extra)
        return data

    def post_data(self, **extra):
        data = self.listing_data(**extra)
        for key in ("start", "end", "booking_closes_at"):
            if data[key]:
                data[key] = timezone.localtime(data[key]).strftime("%Y-%m-%dT%H:%M")
        data["return_by"] = data["return_by"].isoformat() if data["return_by"] else ""
        return data

    def reserve(self, guest=None, listing=None):
        listing = listing or self.listing
        guest = guest or self.guest
        booking = services.apply_for_place(
            guest, listing.pk, listing.terms_version,
            return_college=guest.college, can_host=True, accept_terms=True,
        )
        if booking.status == "requested":
            booking = services.decide_request(listing.host, booking.pk, True)
        return booking

    def reciprocal(self):
        return services.create_listing(self.host, **self.listing_data(
            title="Reciprocal dinner", invitation_type="reciprocal",
            booking_mode="instant", allowed_return_colleges=["darwin"],
            return_by=timezone.localdate(self.start + timedelta(days=45)),
        ))

    def test_anonymous_members_area_redirects_to_login_without_details(self):
        for name, args in [("index", []), ("create", []), ("mine", []), ("detail", [self.listing.pk]), ("edit", [self.listing.pk]), ("committee", [])]:
            with self.subTest(name=name):
                response = self.client.get(reverse(f"formals:{name}", args=args))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.url)
                self.assertNotIn(self.listing.event.title, response.content.decode())
        response = self.client.post(reverse("formals:apply", args=[self.listing.pk]), {"terms_version": 1, "accept_terms": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(FormalBooking.objects.exists())

    def test_member_browse_and_detail_are_private_and_do_not_show_guest_roster(self):
        self.reserve()
        self.client.force_login(self.other)
        response = self.client.get(reverse("formals:index"))
        self.assertContains(response, "Wolfson autumn formal")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        detail = self.client.get(self.listing.get_absolute_url())
        self.assertContains(detail, "Request places")
        self.assertNotContains(detail, self.guest.get_full_name())
        self.assertNotContains(detail, self.guest.email)

    def test_create_is_members_only_and_forces_private_event(self):
        self.client.force_login(self.host)
        response = self.client.post(reverse("formals:create"), self.post_data(title="New private invitation", members_only=""))
        created = FormalListing.objects.get(event__title="New private invitation")
        self.assertRedirects(response, created.get_absolute_url())
        self.assertTrue(created.event.members_only)
        self.assertFalse(created.event.show_attendees)
        self.assertEqual(created.host, self.host)

    def test_invalid_capacity_and_reciprocal_deadline_show_form_errors(self):
        self.client.force_login(self.host)
        before = FormalListing.objects.count()
        response = self.client.post(reverse("formals:create"), self.post_data(capacity=4, invitation_type="reciprocal"))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "capacity", "Ensure this value is less than or equal to 3.")
        self.assertFormError(response.context["form"], "return_by", "Choose a deadline for return invitations.")
        self.assertEqual(FormalListing.objects.count(), before)

    def test_forms_only_allow_the_hosts_profile_college(self):
        form = ListingForm(user=self.host)
        self.assertEqual(form.fields["college"].choices, [("wolfson", "Wolfson")])
        self.client.force_login(self.host)
        response = self.client.post(reverse("formals:create"), self.post_data(college="darwin"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("college", response.context["form"].errors)

    def test_approval_and_instant_reservations_use_the_formal_ledger_only(self):
        self.client.force_login(self.guest)
        response = self.client.post(reverse("formals:apply", args=[self.listing.pk]), {"terms_version": 1, "accept_terms": "on"})
        self.assertRedirects(response, self.listing.get_absolute_url())
        booking = FormalBooking.objects.get(listing=self.listing, guest=self.guest)
        self.assertEqual(booking.status, "requested")
        self.assertEqual(self.listing.spots_left, 3)
        self.client.force_login(self.host)
        response = self.client.post(reverse("formals:request_action", args=[booking.pk, "accept"]))
        self.assertRedirects(response, self.listing.get_absolute_url())
        booking.refresh_from_db()
        self.assertEqual(booking.status, "reserved")
        self.assertEqual(self.listing.spots_left, 2)
        instant = services.create_listing(self.host, **self.listing_data(title="Instant dinner", booking_mode="instant"))
        self.client.force_login(self.guest)
        self.client.post(reverse("formals:apply", args=[instant.pk]), {"terms_version": 1, "accept_terms": "on"})
        self.assertEqual(FormalBooking.objects.get(listing=instant, guest=self.guest).status, "reserved")
        self.assertFalse(RSVP.objects.filter(event__in=[self.listing.event, instant.event]).exists())

    def test_forged_old_terms_and_missing_acceptance_do_not_book(self):
        self.client.force_login(self.guest)
        url = reverse("formals:apply", args=[self.listing.pk])
        self.client.post(url, {"terms_version": 100, "accept_terms": "on"})
        self.client.post(url, {"terms_version": 1})
        self.assertFalse(FormalBooking.objects.exists())

    def test_unrelated_member_cannot_manage_bookings_or_see_private_swaps(self):
        reciprocal = self.reciprocal()
        booking = self.reserve(listing=reciprocal)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("formals:booking_action", args=[booking.pk, "cancel"])).status_code, 404)
        self.assertEqual(self.client.get(booking.exchange.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.post(reverse("formals:exchange_action", args=[booking.exchange.pk, "waive"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("formals:edit", args=[reciprocal.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("formals:committee")).status_code, 403)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "reserved")

    def test_host_can_manage_tickets_but_guest_cannot_self_confirm_a_ticket(self):
        booking = self.reserve()
        url = reverse("formals:booking_action", args=[booking.pk, "ticket-secured"])
        self.client.force_login(self.guest)
        self.client.post(url)
        booking.refresh_from_db()
        self.assertEqual(booking.ticket_status, "pending")
        self.client.force_login(self.host)
        self.client.post(url)
        booking.refresh_from_db()
        self.assertEqual(booking.ticket_status, "secured")
        response = self.client.get(self.listing.get_absolute_url())
        self.assertContains(response, self.guest.get_full_name())
        self.assertContains(response, "College ticket confirmed by host")

    def test_mutations_are_post_only_and_csrf_protected(self):
        self.client.force_login(self.guest)
        url = reverse("formals:apply", args=[self.listing.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(self.guest)
        self.assertEqual(secure_client.post(url, {"terms_version": 1, "accept_terms": "on"}).status_code, 403)
        self.assertFalse(FormalBooking.objects.exists())

    def test_shadow_hidden_host_remains_accessible_to_existing_guest(self):
        self.reserve()
        self.host.is_shadow_banned = True
        self.host.save(update_fields=["is_shadow_banned"])
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("formals:index")), self.listing.event.title)
        self.assertEqual(self.client.get(self.listing.get_absolute_url()).status_code, 404)
        self.client.force_login(self.guest)
        self.assertEqual(self.client.get(self.listing.get_absolute_url()).status_code, 200)

    def test_cancelled_listing_remains_private_and_visible_to_its_guest(self):
        self.reserve()
        services.cancel_listing(self.host, self.listing.pk, reason="Dining hall closed")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.listing.get_absolute_url()).status_code, 404)
        self.client.force_login(self.guest)
        self.assertContains(self.client.get(self.listing.get_absolute_url()), "This formal has been cancelled")

    def test_listing_terms_are_locked_once_someone_responds(self):
        services.apply_for_place(self.guest, self.listing.pk, 1, accept_terms=True)
        self.client.force_login(self.host)
        url = reverse("formals:edit", args=[self.listing.pk])
        response = self.client.post(url, self.post_data(title="Changed after request"))
        self.assertRedirects(response, self.listing.get_absolute_url())
        self.listing.event.refresh_from_db()
        self.assertEqual(self.listing.event.title, "Wolfson autumn formal")

    def test_reciprocal_journey_can_promise_later_and_accept_return_without_new_debt(self):
        reciprocal = self.reciprocal()
        self.client.force_login(self.guest)
        response = self.client.post(reverse("formals:apply", args=[reciprocal.pk]), {
            "terms_version": 1, "accept_terms": "on", "can_host": "on", "return_college": "darwin",
        })
        self.assertRedirects(response, reciprocal.get_absolute_url())
        booking = FormalBooking.objects.get(listing=reciprocal, guest=self.guest)
        exchange = booking.exchange
        self.assertEqual(exchange.status, "conditional")
        self.assertFalse(exchange.proposals.exists())
        response = self.client.get(exchange.get_absolute_url())
        self.assertContains(response, "Create a return formal")
        return_start = self.start + timedelta(days=14)
        returned = services.create_listing(self.guest, **self.listing_data(
            title="Darwin return dinner", college="darwin", start=return_start,
            end=return_start + timedelta(hours=2), booking_closes_at=return_start - timedelta(days=1),
        ))
        response = self.client.get(exchange.get_absolute_url())
        self.assertContains(response, "Darwin return dinner")
        self.client.post(reverse("formals:exchange_action", args=[exchange.pk, "propose"]), {"listing_id": returned.pk})
        proposal = ReturnProposal.objects.get(exchange=exchange)
        self.assertEqual(proposal.status, "pending")
        self.assertEqual(returned.spots_left, 2)
        self.client.force_login(self.host)
        self.assertContains(self.client.get(exchange.get_absolute_url()), "Accept return invitation")
        response = self.client.post(reverse("formals:proposal_action", args=[proposal.pk, "accept"]))
        self.assertRedirects(response, exchange.get_absolute_url())
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "accepted")
        self.assertEqual(proposal.booking.status, "reserved")
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_missing_return_listing_is_not_a_server_error(self):
        booking = self.reserve(listing=self.reciprocal())
        self.client.force_login(self.guest)
        url = reverse("formals:exchange_action", args=[booking.exchange.pk, "propose"])
        self.assertEqual(self.client.post(url, {"listing_id": 999999}).status_code, 404)
        self.assertEqual(self.client.post(url, {"listing_id": "9" * 100}).status_code, 302)
        self.assertFalse(ReturnProposal.objects.exists())

    def test_member_pages_and_committee_queue_render(self):
        reciprocal = self.reciprocal()
        booking = self.reserve(listing=reciprocal)
        services.report_issue(self.guest, booking.exchange.pk, reason="Please help us arrange a date")
        self.client.force_login(self.guest)
        self.assertContains(self.client.get(reverse("formals:mine")), "My plans")
        self.assertContains(self.client.get(booking.exchange.get_absolute_url()), "committee has been asked")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("formals:committee"))
        self.assertContains(response, "Needs attention")
        self.assertContains(response, "Committee review requested")
        self.assertContains(self.client.get(booking.exchange.get_absolute_url()), "Committee decision")

    def test_attendance_controls_wait_for_dinner_to_end_and_support_committee_review(self):
        booking = self.reserve()
        self.listing.event.start = timezone.now() - timedelta(hours=1)
        self.listing.event.end = timezone.now() + timedelta(hours=1)
        self.listing.event.save(update_fields=["start", "end"])
        self.client.force_login(self.guest)
        self.assertNotContains(self.client.get(self.listing.get_absolute_url()), "Confirm I attended")
        self.listing.event.end = timezone.now() - timedelta(minutes=1)
        self.listing.event.save(update_fields=["end"])
        self.assertContains(self.client.get(self.listing.get_absolute_url()), "Confirm I attended")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.listing.get_absolute_url()), "Confirm attendance (committee)")
        self.client.post(reverse("formals:booking_action", args=[booking.pk, "attendance"]))
        booking.refresh_from_db()
        self.assertEqual(booking.status, "attended")

    def test_ineligible_return_college_is_explained_and_cannot_submit(self):
        reciprocal = self.reciprocal()
        self.client.force_login(self.admin)  # Wolfson, not the requested Darwin.
        response = self.client.get(reciprocal.get_absolute_url())
        self.assertContains(response, "Your profile must name an eligible college")
        self.assertNotContains(response, "Reserve my place")
        self.client.post(reverse("formals:apply", args=[reciprocal.pk]), {"terms_version": 1, "accept_terms": "on", "can_host": "on", "return_college": "darwin"})
        self.assertFalse(FormalBooking.objects.filter(guest=self.admin).exists())
