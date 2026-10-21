"""Run these against the deployment database as well as the SQLite suite.

SQLite does not implement SELECT FOR UPDATE, so passing SQLite tests cannot
demonstrate the locking guarantees used to allocate formal places.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import User
from formals import services
from formals.models import FormalBooking, FormalExchange

from .test_services import make_user


@skipUnlessDBFeature("has_select_for_update")
class FormalAllocationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.host = make_user("racehost")
        self.first = make_user("racefirst", college="darwin")
        self.second = make_user("racesecond", college="clare-hall")
        start = timezone.now() + timedelta(days=7)
        self.listing = services.create_listing(
            self.host, title="One formal place", description="", location="College hall",
            college="wolfson", start=start, end=start + timedelta(hours=2),
            capacity=1, invitation_type="reciprocal", booking_mode="instant",
            allowed_return_colleges=[], booking_closes_at=start - timedelta(days=1),
            return_by=(start + timedelta(days=60)).date(), price_text="", practical_details="",
            hosting_confirmed=True,
        )

    def run_together(self, *operations):
        barrier = Barrier(len(operations), timeout=15)

        def run(operation):
            close_old_connections()
            try:
                barrier.wait()
                try:
                    return ("accepted", operation())
                except services.FormalError:
                    return ("rejected", None)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures = [pool.submit(run, operation) for operation in operations]
            return [future.result(timeout=30) for future in futures]

    def apply_as(self, user_id):
        user = User.objects.get(pk=user_id)
        return services.apply_for_place(
            user, self.listing.pk, terms_version=1, return_college=user.college,
            can_host=True, accept_terms=True,
        ).pk

    def test_two_instant_reservations_cannot_both_take_the_last_seat(self):
        outcomes = self.run_together(
            lambda: self.apply_as(self.first.pk),
            lambda: self.apply_as(self.second.pk),
        )
        self.assertEqual([state for state, _ in outcomes].count("accepted"), 1)
        self.assertEqual([state for state, _ in outcomes].count("rejected"), 1)
        self.assertEqual(FormalBooking.objects.filter(listing=self.listing, status="reserved").count(), 1)
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_duplicate_requests_from_one_member_share_one_booking_and_promise(self):
        outcomes = self.run_together(
            lambda: self.apply_as(self.first.pk),
            lambda: self.apply_as(self.first.pk),
        )
        self.assertEqual([state for state, _ in outcomes], ["accepted", "accepted"])
        self.assertEqual(outcomes[0][1], outcomes[1][1])
        self.assertEqual(FormalBooking.objects.count(), 1)
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_simultaneous_host_approvals_do_not_overbook(self):
        self.listing.booking_mode = "approval"
        self.listing.save(update_fields=["booking_mode"])
        first_id = self.apply_as(self.first.pk)
        second_id = self.apply_as(self.second.pk)

        def accept(booking_id):
            host = User.objects.get(pk=self.host.pk)
            services.decide_request(host, booking_id, True)
            return booking_id

        outcomes = self.run_together(lambda: accept(first_id), lambda: accept(second_id))
        self.assertEqual([state for state, _ in outcomes].count("accepted"), 1)
        self.assertEqual([state for state, _ in outcomes].count("rejected"), 1)
        self.assertEqual(FormalBooking.objects.filter(status="reserved").count(), 1)
        self.assertEqual(FormalBooking.objects.filter(status="requested").count(), 1)
        self.assertEqual(FormalExchange.objects.count(), 1)

    def test_return_hold_and_new_reservation_compete_for_the_same_seat(self):
        original_id = self.apply_as(self.first.pk)
        exchange = FormalExchange.objects.get(original_booking_id=original_id)
        start = self.listing.event.start + timedelta(days=14)
        return_listing = services.create_listing(
            self.first, title="One return place", description="", location="Darwin hall",
            college="darwin", start=start, end=start + timedelta(hours=2),
            capacity=1, invitation_type="open", booking_mode="instant",
            allowed_return_colleges=[], booking_closes_at=start - timedelta(days=1),
            return_by=None, price_text="", practical_details="", hosting_confirmed=True,
        )

        def propose():
            debtor = User.objects.get(pk=self.first.pk)
            return services.propose_return(debtor, exchange.pk, return_listing.pk).pk

        def reserve():
            guest = User.objects.get(pk=self.second.pk)
            return services.apply_for_place(
                guest, return_listing.pk, terms_version=1, accept_terms=True,
            ).pk

        outcomes = self.run_together(propose, reserve)
        self.assertEqual([state for state, _ in outcomes].count("accepted"), 1)
        self.assertEqual([state for state, _ in outcomes].count("rejected"), 1)
        self.assertEqual(return_listing.occupied_count, 1)
        self.assertEqual(FormalExchange.objects.count(), 1)
