"""Reservations survive retries, respect capacity and keep the waitlist fair."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification, NotificationEmail
from .models import Category, Event, RSVP
from .services import EventBookingError, cancel_event, save_event_edit, set_rsvp


def make_fixture(test):
    def member(name):
        return User.objects.create_user(username=name, first_name=name, last_name="Member", college="wolfson", mobile="07700900000", email=f"{name}@example.test")
    test.host, test.first, test.second, test.third = [member(name) for name in ("integrityhost", "integrityfirst", "integritysecond", "integritythird")]
    category = Category.objects.create(name="Integrity", slug="integrity")
    start = timezone.now() + timedelta(days=4)
    test.event = Event.objects.create(title="One place", category=category, created_by=test.host, host=test.host,
                                      start=start, end=start + timedelta(hours=2), capacity=1, location="College")


class BookingIntegrityTests(TestCase):
    def setUp(self):
        make_fixture(self)

    def edit_data(self, capacity):
        return dict(title=self.event.title, category=self.event.category_id, description="", location="College",
                    host=self.host.pk, start=timezone.localtime(self.event.start).strftime("%Y-%m-%dT%H:%M"),
                    end=timezone.localtime(self.event.end).strftime("%Y-%m-%dT%H:%M"), capacity=capacity)

    def test_repeated_join_and_cancel_preserve_intention(self):
        joined = set_rsvp(self.first, self.event.pk, "join")
        repeated = set_rsvp(self.first, self.event.pk, "join")
        self.assertEqual(repeated.rsvp.pk, joined.rsvp.pk)
        self.assertFalse(repeated.changed)
        self.assertEqual(self.event.going_count, 1)
        set_rsvp(self.first, self.event.pk, "cancel")
        repeated = set_rsvp(self.first, self.event.pk, "cancel")
        self.assertFalse(repeated.changed)
        self.assertEqual(self.event.going_count, 0)
        self.assertEqual(self.event.rsvps.count(), 1)

    def test_repeat_cancellation_only_promotes_and_notifies_once(self):
        set_rsvp(self.first, self.event.pk, "join")
        set_rsvp(self.second, self.event.pk, "join")
        set_rsvp(self.third, self.event.pk, "join")
        set_rsvp(self.first, self.event.pk, "cancel")
        set_rsvp(self.first, self.event.pk, "cancel")
        self.assertEqual(self.event.rsvps.get(user=self.second).status, RSVP.Status.GOING)
        self.assertEqual(self.event.rsvps.get(user=self.third).status, RSVP.Status.WAITING)
        self.assertEqual(NotificationEmail.objects.filter(recipient=self.second).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.second).count(), 1)

    def test_promotion_skips_suspended_or_inactive_members(self):
        set_rsvp(self.first, self.event.pk, "join")
        set_rsvp(self.second, self.event.pk, "join")
        set_rsvp(self.third, self.event.pk, "join")
        User.objects.filter(pk=self.second.pk).update(is_banned=True, is_active=False)
        set_rsvp(self.first, self.event.pk, "cancel")
        self.assertEqual(self.event.rsvps.get(user=self.third).status, RSVP.Status.GOING)
        self.assertFalse(NotificationEmail.objects.filter(recipient=self.second).exists())

    def test_new_join_cannot_jump_an_existing_waitlist(self):
        RSVP.objects.create(event=self.event, user=self.first, status=RSVP.Status.WAITING)
        newcomer = set_rsvp(self.second, self.event.pk, "join")
        self.assertEqual(self.event.rsvps.get(user=self.first).status, RSVP.Status.GOING)
        self.assertEqual(newcomer.rsvp.status, RSVP.Status.WAITING)
        self.assertEqual(newcomer.waiting_position, 1)

    def test_capacity_increase_promotes_waiters_and_reduction_is_rejected(self):
        set_rsvp(self.first, self.event.pk, "join")
        set_rsvp(self.second, self.event.pk, "join")
        form = save_event_edit(self.host, self.event.pk, self.edit_data(2))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(self.event.going_count, 2)
        form = save_event_edit(self.host, self.event.pk, self.edit_data(1))
        self.assertIn("capacity", form.errors)
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 2)

    def test_notifications_and_promotion_rollback_together(self):
        set_rsvp(self.first, self.event.pk, "join")
        set_rsvp(self.second, self.event.pk, "join")
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                set_rsvp(self.first, self.event.pk, "cancel")
                raise RuntimeError("rollback")
        self.assertEqual(self.event.rsvps.get(user=self.first).status, RSVP.Status.GOING)
        self.assertEqual(self.event.rsvps.get(user=self.second).status, RSVP.Status.WAITING)
        self.assertFalse(NotificationEmail.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_cancelled_event_cannot_allocate_or_promote_places(self):
        RSVP.objects.create(event=self.event, user=self.first, status=RSVP.Status.WAITING)
        cancel_event(self.host, self.event.pk)
        with self.assertRaises(EventBookingError):
            set_rsvp(self.second, self.event.pk, "join")
        self.assertEqual(self.event.promote_waitlist(), [])
        self.assertEqual(self.event.going_count, 0)

    def test_inactive_members_and_invalid_actions_cannot_change_a_booking(self):
        self.first.is_active = False
        with self.assertRaises(PermissionDenied):
            set_rsvp(self.first, self.event.pk, "join")
        with self.assertRaises(EventBookingError):
            set_rsvp(self.second, self.event.pk, "anything")
        self.assertFalse(self.event.rsvps.exists())

    def test_admin_metadata_edit_does_not_restore_stale_capacity_or_status(self):
        from django.contrib import admin
        from .admin import EventAdmin

        stale = Event.objects.get(pk=self.event.pk)
        save_event_edit(self.host, self.event.pk, self.edit_data(2))
        cancel_event(self.host, self.event.pk)
        stale.title = "Updated description"
        EventAdmin(Event, admin.site).save_model(RequestFactory().post("/dj-admin/"), stale, None, True)
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 2)
        self.assertTrue(self.event.is_cancelled)
        self.assertEqual(self.event.title, "Updated description")


@skipUnlessDBFeature("has_select_for_update")
class BookingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        make_fixture(self)

    def together(self, *operations):
        barrier = Barrier(len(operations), timeout=15)
        def run(operation):
            close_old_connections()
            try:
                barrier.wait()
                return operation()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures = [pool.submit(run, operation) for operation in operations]
            return [future.result(timeout=30) for future in futures]

    def test_last_place_is_allocated_once(self):
        statuses = self.together(
            lambda: set_rsvp(self.first, self.event.pk, "join").rsvp.status,
            lambda: set_rsvp(self.second, self.event.pk, "join").rsvp.status,
        )
        self.assertCountEqual(statuses, [RSVP.Status.GOING, RSVP.Status.WAITING])
        self.assertEqual(self.event.going_count, 1)

    def test_concurrent_duplicate_join_does_not_cancel_or_duplicate(self):
        self.together(lambda: set_rsvp(self.first, self.event.pk, "join"), lambda: set_rsvp(self.first, self.event.pk, "join"))
        self.assertEqual(self.event.rsvps.count(), 1)
        self.assertEqual(self.event.going_count, 1)

    def test_cancellation_and_new_join_preserve_waitlist_priority(self):
        set_rsvp(self.first, self.event.pk, "join")
        set_rsvp(self.second, self.event.pk, "join")
        self.together(lambda: set_rsvp(self.first, self.event.pk, "cancel"), lambda: set_rsvp(self.third, self.event.pk, "join"))
        self.assertEqual(self.event.going_count, 1)
        self.assertEqual(self.event.rsvps.get(user=self.second).status, RSVP.Status.GOING)
        self.assertEqual(self.event.rsvps.get(user=self.third).status, RSVP.Status.WAITING)
