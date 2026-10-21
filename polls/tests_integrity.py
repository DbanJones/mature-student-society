"""A vote or a final poll result changes as one unit under concurrent requests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import User
from events.models import Category, Event
from notifications.models import NotificationEmail
from panel.models import AuditLog
from .models import Poll, PollOption, PollVote
from .services import PollError, cast_vote, close_poll


def make_fixture(test):
    def member(name):
        return User.objects.create_user(username=name, first_name=name, last_name="Member", college="wolfson", mobile="07700900000", email=f"{name}@example.test")
    test.host, test.first, test.second = [member(name) for name in ("pollintegrityhost", "pollintegrityfirst", "pollintegritysecond")]
    category = Category.objects.create(name="Poll integrity", slug="poll-integrity")
    start = timezone.now() + timedelta(days=7)
    test.event = Event.objects.create(title="Poll event", category=category, created_by=test.host, host=test.host,
                                      start=start, end=start + timedelta(hours=2), location="Original venue")
    test.poll = Poll.objects.create(event=test.event, kind="venue", question="Where?", created_by=test.host, closes_at=start - timedelta(days=2))
    test.a = PollOption.objects.create(poll=test.poll, label="First venue")
    test.b = PollOption.objects.create(poll=test.poll, label="Second venue")


class PollIntegrityTests(TestCase):
    def setUp(self):
        make_fixture(self)

    def test_repeated_vote_preserves_the_same_row(self):
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        before = PollVote.objects.get().pk
        cast_vote(self.first, self.poll.pk, [str(self.a.pk)])
        self.assertEqual(PollVote.objects.get().pk, before)

    def test_failed_vote_replacement_preserves_previous_choice(self):
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        with patch("polls.models.PollVote.objects.bulk_create", side_effect=IntegrityError("insert failed")):
            with self.assertRaises(IntegrityError):
                cast_vote(self.first, self.poll.pk, [self.b.pk])
        self.assertEqual(self.poll.choices_of(self.first), {self.a.pk})

    def test_invalid_or_foreign_options_never_erase_an_existing_vote(self):
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        other = Poll.objects.create(kind="general", question="Other", created_by=self.host, closes_at=self.poll.closes_at)
        foreign = PollOption.objects.create(poll=other, label="Foreign")
        for values in ([self.b.pk, foreign.pk], ["not-a-number"], [True], [1.1], [], [self.a.pk, self.b.pk]):
            with self.subTest(values=values), self.assertRaises(PollError):
                cast_vote(self.first, self.poll.pk, values)
            self.assertEqual(self.poll.choices_of(self.first), {self.a.pk})

    def test_closed_result_cannot_disagree_with_the_applied_event(self):
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        self.poll.close(by=self.host)
        with self.assertRaises(PollError):
            self.poll.close(by=self.host, option=self.b)
        self.poll.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.poll.outcome_option_id, self.a.pk)
        self.assertEqual(self.event.location, self.a.label)

    def test_repeated_close_from_stale_object_applies_once(self):
        stale = Poll.objects.get(pk=self.poll.pk)
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        self.poll.close(by=self.host)
        stale.close(by=self.host)
        self.assertEqual(AuditLog.objects.filter(action="apply_poll").count(), 1)
        self.assertEqual(NotificationEmail.objects.count(), 1)
        self.assertEqual(stale.outcome_option_id, self.a.pk)

    def test_outcome_failure_rolls_back_poll_event_and_notifications(self):
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        with patch("panel.models.AuditLog.record", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.poll.close(by=self.host)
        self.poll.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.poll.status, Poll.Status.OPEN)
        self.assertIsNone(self.poll.applied_at)
        self.assertEqual(self.event.location, "Original venue")
        self.assertFalse(NotificationEmail.objects.exists())

    def test_venue_change_clears_previous_map_coordinates(self):
        Event.objects.filter(pk=self.event.pk).update(latitude=52.2, longitude=0.12, geocoded_at=timezone.now())
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        self.poll.close(by=self.host)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.latitude)
        self.assertIsNone(self.event.longitude)
        self.assertIsNone(self.event.geocoded_at)

    def test_cancelled_event_does_not_take_votes_or_apply_a_result(self):
        cast_vote(self.first, self.poll.pk, [self.a.pk])
        Event.objects.filter(pk=self.event.pk).update(is_cancelled=True)
        with self.assertRaises(PollError):
            cast_vote(self.second, self.poll.pk, [self.b.pk])
        self.poll.close(by=self.host)
        self.event.refresh_from_db()
        self.assertEqual(self.event.location, "Original venue")
        self.assertIsNone(self.poll.applied_at)

    def test_only_eligible_members_vote_and_organisers_close(self):
        self.first.is_active = False
        with self.assertRaises(PermissionDenied):
            cast_vote(self.first, self.poll.pk, [self.a.pk])
        with self.assertRaises(PermissionDenied):
            close_poll(self.poll.pk, by=self.second)


@skipUnlessDBFeature("has_select_for_update")
class PollConcurrencyTests(TransactionTestCase):
    def setUp(self):
        make_fixture(self)

    def together(self, *operations):
        barrier = Barrier(len(operations), timeout=15)
        def run(operation):
            close_old_connections()
            try:
                barrier.wait()
                try:
                    operation()
                    return "accepted"
                except PollError:
                    return "rejected"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures = [pool.submit(run, operation) for operation in operations]
            return [future.result(timeout=30) for future in futures]

    def test_two_members_cannot_take_the_same_volunteer_place(self):
        Poll.objects.filter(pk=self.poll.pk).update(kind=Poll.Kind.VOLUNTEERS)
        PollOption.objects.filter(pk=self.a.pk).update(capacity=1)
        outcomes = self.together(lambda: cast_vote(self.first, self.poll.pk, [self.a.pk]), lambda: cast_vote(self.second, self.poll.pk, [self.a.pk]))
        self.assertCountEqual(outcomes, ["accepted", "rejected"])
        self.assertEqual(self.a.votes.count(), 1)

    def test_vote_and_close_cannot_record_a_vote_after_the_final_result(self):
        self.together(lambda: cast_vote(self.first, self.poll.pk, [self.a.pk]), lambda: close_poll(self.poll.pk, by=self.host))
        self.poll.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.poll.status, Poll.Status.CLOSED)
        if self.poll.votes.exists():
            self.assertEqual(self.poll.outcome_option_id, self.a.pk)
            self.assertEqual(self.event.location, self.a.label)
        else:
            self.assertIsNone(self.poll.outcome_option_id)
            self.assertEqual(self.event.location, "Original venue")
