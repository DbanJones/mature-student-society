"""Formal places, bilateral promises and return-seat reservations.

These tests exercise the service boundary rather than reproducing form
validation: a crafted request must obey the same rules as the member screens.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from events.models import RSVP
from inbox.models import MessageBlock
from formals import services
from formals.models import FormalBooking, FormalEmail, FormalExchange, ReturnProposal


def make_user(username, college="wolfson", **extra):
    return User.objects.create_user(
        username=username, first_name=username.capitalize(), last_name="Member",
        college=college, mobile="+447700900000", email=f"{username}@cam.ac.uk",
        **extra,
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class FormalServiceTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.host = make_user("formalhost")
        self.guest = make_user("formalguest", college="darwin")
        self.other = make_user("formalother", college="clare-hall")
        self.admin = make_user("formaladmin", is_portal_admin=True)
        self.sequence = 0

    def listing(self, host=None, **changes):
        host = host or self.host
        self.sequence += 1
        start = changes.pop("start", self.now + timedelta(days=7))
        defaults = {
            "title": f"Formal supper {self.sequence}",
            "description": "A college supper with fellow members.",
            "location": "College dining hall",
            "college": host.college,
            "start": start,
            "end": start + timedelta(hours=2),
            "capacity": 3,
            "invitation_type": "open",
            "booking_mode": "instant",
            "allowed_return_colleges": [],
            "booking_closes_at": start - timedelta(days=1),
            "return_by": None,
            "price_text": "Guest pays the college ticket price",
            "practical_details": "Meet at the porter's lodge.",
            "hosting_confirmed": True,
        }
        defaults.update(changes)
        if defaults["invitation_type"] == "reciprocal" and not defaults["return_by"]:
            defaults["return_by"] = (start + timedelta(days=60)).date()
        return services.create_listing(host, **defaults)

    def apply(self, listing, guest=None, **changes):
        guest = guest or self.guest
        defaults = {
            "terms_version": listing.terms_version,
            "accept_terms": True,
            "return_college": guest.college if listing.invitation_type == "reciprocal" else "",
            "can_host": listing.invitation_type == "reciprocal",
        }
        defaults.update(changes)
        return services.apply_for_place(guest, listing.pk, **defaults)

    def reciprocal(self, **listing_changes):
        listing = self.listing(invitation_type="reciprocal", **listing_changes)
        booking = self.apply(listing)
        return listing, booking, FormalExchange.objects.get(original_booking=booking)

    def confirm_first_meal(self, listing, booking):
        after_meal = listing.event.end + timedelta(minutes=5)
        with patch("formals.services.timezone.now", return_value=after_meal):
            services.confirm_attendance(self.host, booking.pk)
            services.confirm_attendance(self.guest, booking.pk)

    def return_proposal(self, listing, exchange, **changes):
        defaults = {"capacity": 1, "start": listing.event.start + timedelta(days=14)}
        defaults.update(changes)
        return_listing = self.listing(host=self.guest, **defaults)
        proposal = services.propose_return(self.guest, exchange.pk, return_listing.pk)
        return return_listing, proposal

    def test_listing_is_a_private_event_and_capacity_counts_only_guest_places(self):
        listing = self.listing(capacity=2)
        self.assertTrue(listing.event.members_only)
        self.assertEqual(listing.host, self.host)
        self.assertEqual(listing.spots_left, 2)
        booking = self.apply(listing)
        listing.refresh_from_db()
        self.assertEqual(booking.status, "reserved")
        self.assertEqual(listing.occupied_count, 1)
        self.assertEqual(listing.spots_left, 1)
        self.assertFalse(RSVP.objects.filter(event=listing.event).exists())

    def test_host_must_attest_and_offer_between_one_and_three_places(self):
        for changes in ({"hosting_confirmed": False}, {"capacity": 0}, {"capacity": 4}):
            with self.subTest(changes=changes), self.assertRaises(services.FormalError):
                self.listing(**changes)

    def test_listing_rejects_past_or_impossible_dates(self):
        for changes in (
            {"start": self.now - timedelta(days=1)},
            {"end": self.now + timedelta(days=6)},
            {"booking_closes_at": self.now + timedelta(days=8)},
            {"invitation_type": "reciprocal", "return_by": self.now.date()},
        ):
            with self.subTest(changes=changes), self.assertRaises(services.FormalError):
                self.listing(**changes)

    def test_anonymous_banned_and_inactive_members_cannot_apply(self):
        listing = self.listing()
        for actor in (
            AnonymousUser(),
            make_user("bannedformal", is_banned=True),
            make_user("inactiveformal", is_active=False),
        ):
            with self.subTest(actor=str(actor)), self.assertRaises(services.FormalError):
                services.apply_for_place(actor, listing.pk, terms_version=1, accept_terms=True)

    def test_host_cannot_book_their_own_guest_place(self):
        listing = self.listing()
        with self.assertRaises(services.FormalError):
            self.apply(listing, guest=self.host)

    def test_block_in_either_direction_prevents_a_new_allocation(self):
        for blocker, blocked in ((self.host, self.guest), (self.guest, self.host)):
            listing = self.listing()
            block = MessageBlock.objects.create(user=blocker, blocked=blocked)
            with self.subTest(blocker=blocker.pk), self.assertRaises(services.FormalError):
                self.apply(listing)
            block.delete()
        self.assertFalse(FormalBooking.objects.exists())

    def test_approval_requests_do_not_reserve_seats(self):
        listing = self.listing(capacity=1, booking_mode="approval")
        first = self.apply(listing)
        second = self.apply(listing, self.other)
        self.assertEqual(first.status, "requested")
        self.assertEqual(second.status, "requested")
        self.assertEqual(listing.spots_left, 1)
        services.decide_request(self.host, first.pk, True)
        self.assertEqual(listing.spots_left, 0)
        with self.assertRaises(services.FormalError):
            services.decide_request(self.host, second.pk, True)
        second.refresh_from_db()
        self.assertEqual(second.status, "requested")

    def test_instant_reservation_cannot_overbook_and_is_idempotent(self):
        listing = self.listing(capacity=1)
        first = self.apply(listing)
        repeated = self.apply(listing)
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(listing.bookings.count(), 1)
        with self.assertRaises(services.FormalError):
            self.apply(listing, self.other)
        self.assertEqual(listing.occupied_count, 1)

    def test_host_approval_is_idempotent_and_only_the_host_or_committee_can_decide(self):
        listing = self.listing(booking_mode="approval", invitation_type="reciprocal")
        booking = self.apply(listing)
        with self.assertRaises(services.FormalError):
            services.decide_request(self.other, booking.pk, True)
        services.decide_request(self.host, booking.pk, True)
        services.decide_request(self.host, booking.pk, True)
        self.assertEqual(FormalExchange.objects.filter(original_booking=booking).count(), 1)
        self.assertEqual(listing.occupied_count, 1)

    def test_approval_rechecks_block_and_suspension(self):
        listing = self.listing(booking_mode="approval")
        booking = self.apply(listing)
        MessageBlock.objects.create(user=self.host, blocked=self.guest)
        with self.assertRaises(services.FormalError):
            services.decide_request(self.host, booking.pk, True)
        MessageBlock.objects.all().delete()
        self.guest.is_active = False
        self.guest.save(update_fields=["is_active"])
        with self.assertRaises(services.FormalError):
            services.decide_request(self.host, booking.pk, True)
        self.assertEqual(listing.occupied_count, 0)

    def test_approval_rechecks_the_guests_promised_college(self):
        listing = self.listing(booking_mode="approval", invitation_type="reciprocal")
        booking = self.apply(listing)
        self.guest.college = "clare-hall"
        self.guest.save(update_fields=["college"])
        with self.assertRaises(services.FormalError):
            services.decide_request(self.host, booking.pk, True)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "requested")
        self.assertEqual(booking.return_college, "darwin")
        self.assertFalse(FormalExchange.objects.exists())

    def test_reservation_rechecks_that_the_host_still_has_the_listed_college(self):
        listing = self.listing()
        self.host.college = "darwin"
        self.host.save(update_fields=["college"])
        with self.assertRaises(services.FormalError):
            self.apply(listing)
        self.assertFalse(FormalBooking.objects.exists())

    def test_declining_a_request_creates_neither_seat_nor_promise(self):
        listing = self.listing(booking_mode="approval", invitation_type="reciprocal")
        booking = self.apply(listing)
        services.decide_request(self.host, booking.pk, False)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "declined")
        self.assertEqual(listing.occupied_count, 0)
        self.assertFalse(FormalExchange.objects.exists())

    def test_open_invitation_never_creates_return_obligation(self):
        listing = self.listing()
        booking = self.apply(listing)
        self.assertEqual(booking.status, "reserved")
        self.assertFalse(FormalExchange.objects.exists())

    def test_each_reciprocal_seat_creates_one_bilateral_promise_without_a_return_date(self):
        listing, booking, exchange = self.reciprocal()
        self.apply(listing, self.other)
        self.assertEqual(FormalExchange.objects.count(), 2)
        self.assertEqual(exchange.creditor, self.host)
        self.assertEqual(exchange.debtor, self.guest)
        self.assertEqual(exchange.return_college, "darwin")
        self.assertEqual(exchange.return_by, listing.return_by)
        self.assertEqual(exchange.status, "conditional")
        self.assertFalse(exchange.proposals.exists())

    def test_reciprocal_reservation_requires_explicit_terms_and_hosting_attestation(self):
        listing = self.listing(invitation_type="reciprocal")
        for changes in ({"accept_terms": False}, {"can_host": False}, {"return_college": ""}):
            with self.subTest(changes=changes), self.assertRaises(services.FormalError):
                self.apply(listing, **changes)
        self.assertFalse(FormalBooking.objects.exists())

    def test_selected_return_colleges_are_enforced(self):
        listing = self.listing(invitation_type="reciprocal", allowed_return_colleges=["darwin"])
        with self.assertRaises(services.FormalError):
            self.apply(listing, self.other)
        booking = self.apply(listing)
        self.assertEqual(booking.return_college, "darwin")

    def test_stale_terms_are_rejected_before_a_place_is_allocated(self):
        listing = self.listing()
        with self.assertRaises(services.FormalError):
            self.apply(listing, terms_version=listing.terms_version + 1)
        self.assertFalse(FormalBooking.objects.exists())

    def test_deadline_enforced_without_a_background_job(self):
        listing = self.listing()
        with patch("formals.services.timezone.now", return_value=listing.booking_closes_at + timedelta(seconds=1)):
            with self.assertRaises(services.FormalError):
                self.apply(listing)
        self.assertFalse(FormalBooking.objects.exists())

    def test_terms_cannot_change_after_someone_has_requested_or_reserved(self):
        listing = self.listing(booking_mode="approval")
        booking = self.apply(listing)
        before = booking.terms_snapshot
        with self.assertRaises(services.FormalError):
            services.update_listing(self.host, listing.pk, capacity=1)
        booking.refresh_from_db()
        self.assertEqual(booking.terms_snapshot, before)

    def test_guest_withdrawal_releases_place_and_closes_conditional_promise(self):
        listing, booking, exchange = self.reciprocal(capacity=1)
        services.cancel_booking(self.guest, booking.pk, reason="Plans changed")
        booking.refresh_from_db()
        exchange.refresh_from_db()
        self.assertEqual(booking.status, "cancelled")
        self.assertEqual(exchange.status, "closed")
        self.assertEqual(listing.spots_left, 1)
        with self.assertRaises(services.FormalError):
            self.apply(listing)

    def test_another_member_cannot_cancel_a_booking_or_listing(self):
        listing, booking, exchange = self.reciprocal()
        with self.assertRaises(services.FormalError):
            services.cancel_booking(self.other, booking.pk)
        with self.assertRaises(services.FormalError):
            services.cancel_listing(self.other, listing.pk)
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "conditional")

    def test_original_listing_cancellation_closes_conditional_promises(self):
        listing, booking, exchange = self.reciprocal()
        services.cancel_listing(self.host, listing.pk, reason="College cancelled supper")
        listing.event.refresh_from_db()
        booking.refresh_from_db()
        exchange.refresh_from_db()
        self.assertTrue(listing.event.is_cancelled)
        self.assertEqual(booking.status, "cancelled")
        self.assertEqual(exchange.status, "closed")

    def test_ticket_status_is_separate_from_reserved_place_and_requires_host(self):
        listing = self.listing()
        booking = self.apply(listing)
        self.assertEqual(booking.ticket_status, "pending")
        with self.assertRaises(services.FormalError):
            services.set_ticket_status(self.guest, booking.pk, True)
        services.set_ticket_status(self.host, booking.pk, True)
        booking.refresh_from_db()
        self.assertEqual(booking.ticket_status, "secured")
        self.assertEqual(booking.status, "reserved")
        self.assertEqual(listing.occupied_count, 1)

    def test_passing_the_date_alone_does_not_activate_or_complete_a_promise(self):
        listing, booking, exchange = self.reciprocal()
        with patch("formals.services.timezone.now", return_value=listing.event.end + timedelta(days=100)):
            services.expire_proposals()
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "conditional")
        booking.refresh_from_db()
        self.assertEqual(booking.status, "reserved")

    def test_attendance_requires_a_finished_meal_and_both_participants(self):
        listing, booking, exchange = self.reciprocal()
        with self.assertRaises(services.FormalError):
            services.confirm_attendance(self.host, booking.pk)
        with patch("formals.services.timezone.now", return_value=listing.event.end + timedelta(minutes=1)):
            with self.assertRaises(services.FormalError):
                services.confirm_attendance(self.other, booking.pk)
            services.confirm_attendance(self.host, booking.pk)
            exchange.refresh_from_db()
            self.assertEqual(exchange.status, "conditional")
            services.confirm_attendance(self.guest, booking.pk)
            services.confirm_attendance(self.guest, booking.pk)
        booking.refresh_from_db()
        exchange.refresh_from_db()
        self.assertEqual(booking.status, "attended")
        self.assertEqual(exchange.status, "owed")

    def test_return_proposal_holds_a_real_place_without_creating_another_exchange(self):
        listing, booking, exchange = self.reciprocal()
        return_listing, proposal = self.return_proposal(listing, exchange, invitation_type="reciprocal")
        proposal.booking.refresh_from_db()
        self.assertEqual(proposal.booking.purpose, "return")
        self.assertEqual(proposal.booking.guest, self.host)
        self.assertEqual(proposal.booking.status, "held")
        self.assertEqual(return_listing.spots_left, 0)
        self.assertEqual(FormalExchange.objects.count(), 1)
        with self.assertRaises(services.FormalError):
            self.apply(return_listing, self.other)

    def test_accepting_return_date_reserves_the_hold_and_completes_after_both_attend(self):
        listing, booking, exchange = self.reciprocal()
        self.confirm_first_meal(listing, booking)
        return_listing, proposal = self.return_proposal(listing, exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        services.respond_to_proposal(self.host, proposal.pk, True)
        proposal.booking.refresh_from_db()
        exchange.refresh_from_db()
        self.assertEqual(proposal.booking.status, "reserved")
        self.assertEqual(exchange.status, "owed")
        with patch("formals.services.timezone.now", return_value=return_listing.event.end + timedelta(minutes=1)):
            services.confirm_attendance(self.guest, proposal.booking.pk)
            exchange.refresh_from_db()
            self.assertEqual(exchange.status, "owed")
            services.confirm_attendance(self.host, proposal.booking.pk)
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "completed")
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_only_debtor_can_propose_and_only_creditor_can_accept_a_return(self):
        listing, booking, exchange = self.reciprocal()
        return_listing = self.listing(host=self.guest, start=listing.event.start + timedelta(days=14))
        with self.assertRaises(services.FormalError):
            services.propose_return(self.other, exchange.pk, return_listing.pk)
        proposal = services.propose_return(self.guest, exchange.pk, return_listing.pk)
        for actor in (self.guest, self.other):
            with self.subTest(actor=actor.pk), self.assertRaises(services.FormalError):
                services.respond_to_proposal(actor, proposal.pk, True)

    def test_return_acceptance_rechecks_the_return_hosts_college(self):
        listing, booking, exchange = self.reciprocal()
        return_listing, proposal = self.return_proposal(listing, exchange)
        self.guest.college = "clare-hall"
        self.guest.save(update_fields=["college"])
        with self.assertRaises(services.FormalError):
            services.respond_to_proposal(self.host, proposal.pk, True)
        proposal.refresh_from_db()
        proposal.booking.refresh_from_db()
        self.assertEqual(proposal.status, "pending")
        self.assertEqual(proposal.booking.status, "held")
        self.assertEqual(return_listing.occupied_count, 1)

    def test_return_must_match_the_promised_college_and_deadline_and_be_after_original(self):
        listing, booking, exchange = self.reciprocal()
        invalid = [
            self.listing(host=self.other, start=listing.event.start + timedelta(days=14)),
            self.listing(host=self.guest, start=listing.event.start - timedelta(days=1)),
            self.listing(host=self.guest, start=listing.event.start + timedelta(days=70)),
        ]
        for return_listing in invalid:
            with self.subTest(listing=return_listing.pk), self.assertRaises(services.FormalError):
                services.propose_return(self.guest, exchange.pk, return_listing.pk)
        self.assertFalse(ReturnProposal.objects.exists())

    def test_expired_return_hold_is_released_by_reservation_even_before_cron(self):
        listing, booking, exchange = self.reciprocal()
        return_listing, proposal = self.return_proposal(listing, exchange)
        with patch("formals.services.timezone.now", return_value=proposal.expires_at + timedelta(seconds=1)):
            other_booking = self.apply(return_listing, self.other)
            with self.assertRaises(services.FormalError):
                services.respond_to_proposal(self.host, proposal.pk, True)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "expired")
        self.assertEqual(other_booking.status, "reserved")
        self.assertEqual(return_listing.occupied_count, 1)

    def test_declining_or_cancelling_a_return_proposal_releases_its_hold(self):
        listing, booking, exchange = self.reciprocal()
        return_listing, proposal = self.return_proposal(listing, exchange)
        services.respond_to_proposal(self.host, proposal.pk, False)
        self.assertEqual(return_listing.spots_left, 1)
        another_listing, another_proposal = self.return_proposal(listing, exchange)
        services.cancel_proposal(self.guest, another_proposal.pk)
        self.assertEqual(another_listing.spots_left, 1)
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "conditional")

    def test_cancelled_return_keeps_an_earned_promise_outstanding(self):
        listing, booking, exchange = self.reciprocal()
        self.confirm_first_meal(listing, booking)
        return_listing, proposal = self.return_proposal(listing, exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        services.cancel_booking(self.guest, proposal.booking.pk, reason="Return formal cancelled")
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "owed")
        self.assertEqual(return_listing.spots_left, 1)

    def test_cannot_make_two_live_return_proposals_for_one_promise(self):
        listing, booking, exchange = self.reciprocal()
        self.return_proposal(listing, exchange)
        another_listing = self.listing(host=self.guest, start=listing.event.start + timedelta(days=20))
        with self.assertRaises(services.FormalError):
            services.propose_return(self.guest, exchange.pk, another_listing.pk)
        self.assertEqual(another_listing.spots_left, 3)

    def test_only_the_person_owed_a_return_can_waive_it(self):
        listing, booking, exchange = self.reciprocal()
        self.confirm_first_meal(listing, booking)
        with self.assertRaises(services.FormalError):
            services.waive_exchange(self.guest, exchange.pk, reason="I changed my mind")
        services.waive_exchange(self.host, exchange.pk, reason="No return needed")
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "waived")

    def test_waiving_a_promise_preserves_a_return_where_attendance_has_been_reported(self):
        listing, booking, exchange = self.reciprocal()
        self.confirm_first_meal(listing, booking)
        return_listing, proposal = self.return_proposal(listing, exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        after_return = return_listing.event.end + timedelta(minutes=1)
        with patch("formals.services.timezone.now", return_value=after_return):
            services.confirm_attendance(self.guest, proposal.booking.pk)
            services.waive_exchange(self.host, exchange.pk, reason="No further promise required")
        exchange.refresh_from_db()
        proposal.refresh_from_db()
        proposal.booking.refresh_from_db()
        self.assertEqual(exchange.status, "waived")
        self.assertEqual(proposal.status, "accepted")
        self.assertEqual(proposal.booking.status, "reserved")
        self.assertTrue(proposal.booking.host_confirmed)
        self.assertEqual(return_listing.occupied_count, 1)

    def test_original_cancellation_cannot_erase_an_already_reported_return_dinner(self):
        listing, booking, exchange = self.reciprocal()
        return_listing, proposal = self.return_proposal(listing, exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        after_return = return_listing.event.end + timedelta(minutes=1)
        with patch("formals.services.timezone.now", return_value=after_return):
            services.confirm_attendance(self.guest, proposal.booking.pk)
            with self.assertRaises(services.FormalError):
                services.cancel_booking(self.admin, booking.pk, reason="Original attendance is unconfirmed")
        exchange.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(exchange.status, "conditional")
        self.assertEqual(booking.status, "reserved")
        self.assertEqual(return_listing.occupied_count, 1)

    def test_return_deadline_extension_needs_the_other_participant_to_accept(self):
        listing, booking, exchange = self.reciprocal()
        original_deadline = exchange.return_by
        proposed_deadline = original_deadline + timedelta(days=30)
        services.request_extension(self.guest, exchange.pk, proposed_deadline)
        exchange.refresh_from_db()
        self.assertEqual(exchange.return_by, original_deadline)
        with self.assertRaises(services.FormalError):
            services.respond_to_extension(self.guest, exchange.pk, True)
        services.respond_to_extension(self.host, exchange.pk, True)
        exchange.refresh_from_db()
        self.assertEqual(exchange.return_by, proposed_deadline)

    def test_issue_reporting_and_resolution_are_private_to_participants_and_committee(self):
        listing, booking, exchange = self.reciprocal()
        with self.assertRaises(services.FormalError):
            services.report_issue(self.other, exchange.pk, "Unrelated member")
        services.report_issue(self.guest, exchange.pk, "Attendance needs checking")
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "disputed")
        with self.assertRaises(services.FormalError):
            services.resolve_exchange(self.host, exchange.pk, "resume", "Host cannot arbitrate")
        services.resolve_exchange(self.admin, exchange.pk, "resume", "Both parties agree to continue")
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "conditional")

    def test_resuming_a_dispute_after_both_dinners_were_confirmed_completes_the_exchange(self):
        listing, booking, exchange = self.reciprocal()
        return_listing, proposal = self.return_proposal(listing, exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        services.report_issue(self.guest, exchange.pk, "Both attendance records need checking")
        after_return = return_listing.event.end + timedelta(minutes=1)
        with patch("formals.services.timezone.now", return_value=after_return):
            services.confirm_attendance(self.host, booking.pk)
            services.confirm_attendance(self.guest, booking.pk)
            services.confirm_attendance(self.guest, proposal.booking.pk)
            services.confirm_attendance(self.host, proposal.booking.pk)
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "disputed")
        services.resolve_exchange(self.admin, exchange.pk, "resume", "Both dinners have been verified")
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "completed")

    def test_participant_and_event_deletion_cannot_silently_erase_an_agreement(self):
        listing, booking, exchange = self.reciprocal()
        with self.assertRaises(ProtectedError):
            self.guest.delete()
        with self.assertRaises(ProtectedError):
            listing.event.delete()

    def test_failed_email_delivery_is_retained_for_retry_and_sends_once_after_success(self):
        listing = self.listing()
        self.apply(listing)
        queued_ids = list(FormalEmail.objects.filter(sent_at__isnull=True).values_list("pk", flat=True))
        self.assertTrue(queued_ids)
        with patch("django.core.mail.backends.locmem.EmailBackend.send_messages", side_effect=OSError("Temporary mail outage")):
            self.assertEqual(services.deliver_emails(limit=50), 0)
        emails = list(FormalEmail.objects.filter(pk__in=queued_ids))
        self.assertTrue(all(item.sent_at is None and item.attempts >= 1 for item in emails))
        self.assertTrue(all(item.available_at > self.now for item in emails))
        self.assertTrue(all(item.last_error for item in emails))
        retry_at = max(item.available_at for item in emails) + timedelta(seconds=1)
        with patch("formals.services.timezone.now", return_value=retry_at):
            sent = services.deliver_emails(limit=50)
            repeated = services.deliver_emails(limit=50)
        self.assertEqual(sent, len(queued_ids))
        self.assertEqual(repeated, 0)
        self.assertEqual(len(mail.outbox), len(queued_ids))

    def test_reserving_a_place_queues_email_without_smtp_inside_the_transaction(self):
        listing = self.listing()
        with patch("formals.services.send_mail") as send:
            self.apply(listing)
        send.assert_not_called()
        self.assertTrue(FormalEmail.objects.filter(sent_at__isnull=True).exists())
