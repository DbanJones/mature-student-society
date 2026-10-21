"""Group capacity, host-set return terms and person-by-person attendance."""

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from formals import services
from formals.models import FormalBooking, FormalExchange, FormalListing, ReturnProposal

from .test_services import make_user
from . import test_concurrency


class GroupBookingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = make_user("grouphost")
        cls.guest = make_user("groupguest", college="darwin")
        cls.other = make_user("groupother", college="clare")
        cls.admin = make_user("groupadmin", is_portal_admin=True)

    def setUp(self):
        self.now = timezone.now()
        self.sequence = 0

    def listing(self, host=None, **changes):
        self.sequence += 1
        host = host or self.host
        start = changes.pop("start", self.now + timedelta(days=7))
        data = dict(
            title=f"Group formal {self.sequence}", description="", location="Dining hall",
            college=host.college, start=start, end=start + timedelta(hours=2), capacity=3,
            invitation_type="reciprocal", booking_mode="instant", return_places=0,
            allowed_return_colleges=[], booking_closes_at=start - timedelta(days=1),
            return_by=(start + timedelta(days=60)).date(), price_text="", practical_details="",
            hosting_confirmed=True,
        )
        data.update(changes)
        return services.create_listing(host, **data)

    def apply(self, listing, guest=None, **changes):
        guest = guest or self.guest
        data = dict(terms_version=listing.terms_version, accept_terms=True,
                    return_college=guest.college, can_host=True)
        data.update(changes)
        return services.apply_for_place(guest, listing.pk, **data)

    def swap(self, places=3, **changes):
        listing = self.listing(**changes)
        booking = self.apply(listing, places=places, companion_names=["Sam Guest", "Jo Guest"][:places - 1])
        return listing, booking, booking.exchange

    def attend(self, booking, seats):
        with patch("formals.services.timezone.now", return_value=booking.listing.event.end + timedelta(minutes=5)):
            services.confirm_attendance(booking.listing.host, booking.pk, seats)
            return services.confirm_attendance(booking.guest, booking.pk, seats)

    def propose(self, exchange, offset=14, **changes):
        listing = self.listing(host=self.guest, start=self.now + timedelta(days=offset), invitation_type="open", **changes)
        return listing, services.propose_return(self.guest, exchange.pk, listing.pk)

    def test_group_reserves_seats_once_and_creates_one_promise(self):
        listing, booking, exchange = self.swap(places=2)
        self.assertEqual((listing.occupied_count, listing.spots_left), (2, 1))
        self.assertEqual((booking.places, exchange.return_places), (2, 2))
        self.assertEqual(booking.terms_snapshot["agreed_return_places"], 2)
        repeated = self.apply(listing, places=2)
        self.assertEqual(repeated.pk, booking.pk)
        self.assertEqual(FormalExchange.objects.count(), 1)
        self.assertEqual(FormalBooking.objects.count(), 1)

    def test_repeat_request_cannot_silently_change_group_quantity(self):
        listing, booking, _ = self.swap(places=2)
        with self.assertRaises(services.FormalError):
            self.apply(listing, places=1)
        booking.refresh_from_db()
        self.assertEqual(booking.places, 2)

    def test_group_allocation_rolls_back_when_not_enough_seats(self):
        listing, _, _ = self.swap(places=2)
        with self.assertRaises(services.FormalError):
            self.apply(listing, self.other, places=2)
        self.assertFalse(listing.bookings.filter(guest=self.other).exists())
        self.assertEqual(listing.occupied_count, 2)

    def test_approval_is_atomic_for_whole_group(self):
        listing = self.listing(booking_mode="approval")
        first = self.apply(listing, places=2)
        second = self.apply(listing, self.other, places=2)
        self.assertEqual(listing.occupied_count, 0)
        services.decide_request(self.host, first.pk, True)
        with self.assertRaises(services.FormalError):
            services.decide_request(self.host, second.pk, True)
        second.refresh_from_db()
        self.assertEqual(second.status, "requested")
        self.assertEqual(listing.occupied_count, 2)

    def test_invalid_group_quantity_is_rejected_at_service_boundary(self):
        listing = self.listing(capacity=2)
        for places in (0, 3, 4, -1, True, False, None, 1.9, "1.9", [], {}):
            with self.subTest(places=places), self.assertRaises(services.FormalError):
                self.apply(listing, places=places)
        self.assertFalse(listing.bookings.exists())

    def test_database_guards_group_and_exchange_quantity(self):
        listing, booking, exchange = self.swap(places=2)
        for model, pk, value in ((FormalBooking, booking.pk, 0), (FormalBooking, booking.pk, 4), (FormalExchange, exchange.pk, 0), (FormalExchange, exchange.pk, 4)):
            field = "places" if model is FormalBooking else "return_places"
            with self.subTest(model=model.__name__, value=value), self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.filter(pk=pk).update(**{field: value})
        with self.assertRaises(IntegrityError), transaction.atomic():
            FormalListing.objects.filter(pk=listing.pk).update(return_places=4)

    def test_host_can_request_a_fixed_number_of_return_places(self):
        listing, booking, exchange = self.swap(places=1, return_places=3)
        self.assertEqual(exchange.return_places, 3)
        self.assertEqual(booking.terms_snapshot["agreed_return_places"], 3)
        return_listing, proposal = self.propose(exchange)
        self.assertEqual(proposal.booking.places, 3)
        self.assertEqual(return_listing.spots_left, 0)
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_host_return_choice_rejects_invalid_quantities(self):
        for quantity in (-1, 4, True, False, None, 1.1, "1.1"):
            with self.subTest(quantity=quantity), self.assertRaises(services.FormalError):
                self.listing(return_places=quantity)

    def test_open_invitation_carries_no_return_request(self):
        listing = self.listing(invitation_type="open", return_places=3)
        booking = self.apply(listing, places=3)
        self.assertEqual(listing.return_places, 0)
        self.assertEqual(booking.terms_snapshot["agreed_return_places"], 0)
        self.assertFalse(FormalExchange.objects.exists())

    def test_return_hold_requires_all_agreed_places(self):
        _, _, exchange = self.swap(places=2)
        with self.assertRaises(services.FormalError):
            self.propose(exchange, capacity=1)
        self.assertFalse(ReturnProposal.objects.exists())
        self.assertFalse(FormalBooking.objects.filter(purpose="return").exists())

    def test_return_hold_uses_quantity_until_expiry(self):
        _, _, exchange = self.swap(places=2)
        listing, proposal = self.propose(exchange)
        self.assertEqual(listing.occupied_count, 2)
        self.apply(listing, guest=self.other, places=1)
        self.assertEqual(listing.occupied_count, 3)
        ReturnProposal.objects.filter(pk=proposal.pk).update(expires_at=self.now - timedelta(minutes=1))
        self.assertEqual(listing.occupied_count, 1)
        services.expire_proposals()
        proposal.booking.refresh_from_db()
        self.assertEqual(proposal.booking.status, "cancelled")

    def test_accepting_return_does_not_count_held_group_twice(self):
        _, _, exchange = self.swap(places=3)
        listing, proposal = self.propose(exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        self.assertEqual(listing.occupied_count, 3)
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_cancel_group_releases_all_seats(self):
        listing, booking, exchange = self.swap(places=3)
        services.cancel_booking(self.guest, booking.pk)
        self.assertEqual(listing.spots_left, 3)
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "closed")

    def test_group_attendance_records_people_and_preserves_full_return_promise(self):
        _, booking, exchange = self.swap(places=3)
        booking = self.attend(booking, [1])
        exchange.refresh_from_db()
        self.assertEqual(booking.status, "attended")
        self.assertEqual(booking.confirmed_places, 1)
        self.assertFalse(booking.lead_attended)
        self.assertEqual(booking.attended_names, ["Sam Guest"])
        self.assertEqual((exchange.status, exchange.return_places, exchange.remaining_return_places), ("owed", 3, 3))

    def test_partial_return_can_be_finished_at_a_second_formal(self):
        _, booking, exchange = self.swap(places=2)
        self.attend(booking, [0, 1])
        _, first = self.propose(exchange)
        services.respond_to_proposal(self.host, first.pk, True)
        services.update_guest_names(self.host, first.booking_id, ["Taylor Guest"])
        self.attend(first.booking, [0])
        exchange.refresh_from_db()
        self.assertEqual((exchange.status, exchange.fulfilled_return_places, exchange.remaining_return_places), ("owed", 1, 1))
        _, second = self.propose(exchange, offset=28, capacity=1)
        self.assertEqual(second.booking.places, 1)
        services.respond_to_proposal(self.host, second.pk, True)
        self.attend(second.booking, [0])
        exchange.refresh_from_db()
        self.assertEqual((exchange.status, exchange.fulfilled_return_places, exchange.remaining_return_places), ("completed", 2, 0))
        self.assertEqual(exchange.proposals.count(), 2)

    def test_zero_attendance_closes_first_promise_and_releases_return_hold(self):
        _, booking, exchange = self.swap(places=2)
        listing, proposal = self.propose(exchange)
        booking = self.attend(booking, [])
        exchange.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual((booking.status, booking.confirmed_places), ("absent", 0))
        self.assertEqual(exchange.status, "closed")
        self.assertEqual(proposal.status, "cancelled")
        self.assertEqual(listing.occupied_count, 0)

    def test_zero_return_attendance_does_not_fulfil_promise_or_block_replacement(self):
        _, booking, exchange = self.swap(places=2)
        self.attend(booking, [0, 1])
        _, first = self.propose(exchange)
        services.respond_to_proposal(self.host, first.pk, True)
        self.attend(first.booking, [])
        exchange.refresh_from_db()
        self.assertEqual((exchange.status, exchange.remaining_return_places), ("owed", 2))
        _, second = self.propose(exchange, offset=28)
        self.assertEqual(second.booking.places, 2)

    def test_zero_original_after_return_attendance_requires_review(self):
        _, booking, exchange = self.swap(places=1)
        _, proposal = self.propose(exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        self.attend(proposal.booking, [0])
        self.attend(booking, [])
        exchange.refresh_from_db()
        self.assertEqual(exchange.status, "disputed")

    def test_mismatched_reports_pause_swap_and_can_be_corrected(self):
        _, booking, exchange = self.swap(places=2)
        with patch("formals.services.timezone.now", return_value=booking.listing.event.end + timedelta(minutes=5)):
            services.confirm_attendance(self.host, booking.pk, [0, 1])
            booking = services.confirm_attendance(self.guest, booking.pk, [0])
            exchange.refresh_from_db()
            self.assertEqual((booking.status, booking.confirmed_places, exchange.status), ("no_show", 0, "disputed"))
            with self.assertRaises(services.FormalError):
                services.resolve_exchange(self.admin, exchange.pk, "resume", "Checked reports")
            booking = services.confirm_attendance(self.guest, booking.pk, [0, 1])
        exchange.refresh_from_db()
        self.assertEqual((booking.status, booking.confirmed_places, exchange.status), ("attended", 2, "disputed"))
        exchange = services.resolve_exchange(self.admin, exchange.pk, "resume", "Both reports now agree")
        self.assertEqual(exchange.status, "owed")

    def test_committee_cannot_complete_before_every_return_place_is_confirmed(self):
        _, booking, exchange = self.swap(places=2)
        self.attend(booking, [0, 1])
        _, proposal = self.propose(exchange)
        services.respond_to_proposal(self.host, proposal.pk, True)
        self.attend(proposal.booking, [0])
        with self.assertRaises(services.FormalError):
            services.resolve_exchange(self.admin, exchange.pk, "complete", "One visit confirmed")

    def test_names_can_be_added_later_but_not_changed_after_a_report(self):
        listing = self.listing()
        booking = self.apply(listing, places=2)
        self.assertEqual(booking.companion_names, [])
        with patch("formals.services.timezone.now", return_value=listing.event.end + timedelta(minutes=5)):
            with self.assertRaises(services.FormalError):
                services.confirm_attendance(self.host, booking.pk, [0, 1])
            booking = services.update_guest_names(self.guest, booking.pk, [" Sam Guest "])
            self.assertEqual(booking.companion_names, ["Sam Guest"])
            services.confirm_attendance(self.host, booking.pk, [0, 1])
            with self.assertRaises(services.FormalError):
                services.update_guest_names(self.guest, booking.pk, ["Different person"])

    def test_names_and_attendance_are_private_and_validated(self):
        _, booking, _ = self.swap(places=2)
        for names in ("Not a list", ["One", "Two"], [None], ["A" * 121], ["A\nB"]):
            with self.subTest(names=names), self.assertRaises(services.FormalError):
                services.update_guest_names(self.guest, booking.pk, names)
        with self.assertRaises(services.FormalError):
            services.update_guest_names(self.other, booking.pk, ["No access"])
        with patch("formals.services.timezone.now", return_value=booking.listing.event.end + timedelta(minutes=5)):
            for seats in ("0", [True], [2], [-1], [0, 0], [0.0]):
                with self.subTest(seats=seats), self.assertRaises(services.FormalError):
                    services.confirm_attendance(self.host, booking.pk, seats)
            with self.assertRaises(services.FormalError):
                services.confirm_attendance(self.other, booking.pk, [0])

    def test_missing_names_can_be_completed_after_first_attendance_report(self):
        listing = self.listing()
        booking = self.apply(listing, places=2)
        self.assertTrue(booking.missing_guest_names)
        with patch("formals.services.timezone.now", return_value=listing.event.end + timedelta(minutes=5)):
            services.confirm_attendance(self.host, booking.pk, [0])
            booking = services.update_guest_names(self.guest, booking.pk, ["Sam Guest"])
            self.assertFalse(booking.missing_guest_names)
            booking = services.confirm_attendance(self.guest, booking.pk, [0, 1])
            self.assertEqual(booking.status, "no_show")
            self.assertEqual(booking.guest_attendance, [0, 1])
            with self.assertRaises(services.FormalError):
                services.update_guest_names(self.host, booking.pk, ["Someone else"])

    def test_filling_blank_guest_slot_preserves_other_named_identity(self):
        listing = self.listing()
        booking = self.apply(listing, places=3, companion_names=["", "Jo Guest"])
        with patch("formals.services.timezone.now", return_value=listing.event.end + timedelta(minutes=5)):
            services.confirm_attendance(self.host, booking.pk, [0, 2])
            with self.assertRaises(services.FormalError):
                services.update_guest_names(self.guest, booking.pk, ["Sam Guest"])
            booking = services.update_guest_names(self.guest, booking.pk, ["Sam Guest", "Jo Guest"])
            self.assertEqual(booking.companion_names, ["Sam Guest", "Jo Guest"])

    def test_legacy_attended_fallback_does_not_invent_group_attendance(self):
        _, booking, _ = self.swap(places=2)
        booking.status = "attended"
        self.assertEqual(booking.confirmed_places, 0)
        booking.places = 1
        self.assertEqual(booking.confirmed_places, 1)
        self.assertTrue(booking.lead_attended)

    def test_data_migration_preserves_existing_attendance_and_promises(self):
        listing = self.listing()
        attended = self.apply(listing)
        FormalBooking.objects.filter(pk=attended.pk).update(status="attended", host_confirmed=False, guest_confirmed=False)
        partial_listing = self.listing()
        partial = self.apply(partial_listing)
        FormalBooking.objects.filter(pk=partial.pk).update(host_confirmed=True)
        migration = import_module("formals.migrations.0002_group_places_and_attendance")
        migration.preserve_attendance(apps, SimpleNamespace(connection=connection))
        attended.refresh_from_db()
        partial.refresh_from_db()
        self.assertEqual((attended.host_attendance, attended.guest_attendance, attended.host_confirmed, attended.guest_confirmed), ([0], [0], True, True))
        self.assertEqual((partial.host_attendance, partial.guest_attendance), ([0], None))
        self.assertEqual(attended.exchange.return_places, 1)
        self.assertEqual(attended.exchange.terms_snapshot["agreed_return_places"], 1)


@skipUnlessDBFeature("has_select_for_update")
class GroupAllocationConcurrencyTests(TransactionTestCase):
    """Production-database races allocate complete groups, never partial seats."""

    run_together = test_concurrency.FormalAllocationConcurrencyTests.run_together

    def setUp(self):
        test_concurrency.FormalAllocationConcurrencyTests.setUp(self)
        self.listing.capacity = 3
        self.listing.save(update_fields=["capacity"])

    def apply_as(self, actor, listing=None):
        return services.apply_for_place(actor, (listing or self.listing).pk, terms_version=1,
                                        return_college=actor.college, can_host=True,
                                        accept_terms=True, places=2).pk

    def test_two_groups_cannot_share_three_places(self):
        outcomes = self.run_together(lambda: self.apply_as(self.first), lambda: self.apply_as(self.second))
        self.assertEqual([state for state, _ in outcomes].count("accepted"), 1)
        self.assertEqual([state for state, _ in outcomes].count("rejected"), 1)
        self.assertEqual(self.listing.occupied_count, 2)
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_group_return_hold_competes_with_group_reservation(self):
        original_id = self.apply_as(self.first)
        exchange = FormalExchange.objects.get(original_booking_id=original_id)
        start = self.listing.event.start + timedelta(days=14)
        listing = services.create_listing(
            self.first, title="Group return race", description="", location="College hall",
            college=self.first.college, start=start, end=start + timedelta(hours=2),
            capacity=3, invitation_type="open", booking_mode="instant",
            allowed_return_colleges=[], booking_closes_at=start - timedelta(days=1),
            return_by=None, price_text="", practical_details="", hosting_confirmed=True,
        )
        outcomes = self.run_together(
            lambda: services.propose_return(self.first, exchange.pk, listing.pk).pk,
            lambda: self.apply_as(self.second, listing),
        )
        self.assertEqual([state for state, _ in outcomes].count("accepted"), 1)
        self.assertEqual([state for state, _ in outcomes].count("rejected"), 1)
        self.assertEqual(listing.occupied_count, 2)
