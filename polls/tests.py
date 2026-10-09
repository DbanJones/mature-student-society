"""Polls: venue and date polls set their event; rotas produce a roster."""

import datetime

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import RSVP, Category, Event
from panel.models import AuditLog

from .models import Poll, PollOption, PollVote


def make_user(username, **extra):
    defaults = dict(
        first_name="Test", last_name=username.title(), college="wolfson",
        mobile="+447700900000", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class PollTestCase(TestCase):
    def setUp(self):
        self.host = make_user("host1")
        self.alice = make_user("ali1")
        self.bob = make_user("bob1")
        self.admin = make_user("adm1", is_portal_admin=True)
        self.category = Category.objects.create(name="Pub", slug="pub")
        start = timezone.now() + datetime.timedelta(days=10)
        self.event = Event.objects.create(
            title="Quiz night", category=self.category, start=start,
            end=start + datetime.timedelta(hours=2), created_by=self.host,
            host=self.host, location="TBC",
        )
        RSVP.objects.create(event=self.event, user=self.alice)
        self.closes = timezone.now() + datetime.timedelta(days=2)

    def make_poll(self, kind, labels, **extra):
        poll = Poll.objects.create(
            event=self.event, kind=kind, question="Where?", created_by=self.host,
            closes_at=self.closes, **extra,
        )
        options = []
        for i, label in enumerate(labels):
            if isinstance(label, tuple):
                label, fields = label
            else:
                fields = {}
            options.append(PollOption.objects.create(poll=poll, label=label, sort_order=i, **fields))
        return poll, options

    def vote(self, user, poll, *options):
        self.client.force_login(user)
        return self.client.post(
            reverse("polls:vote", args=[poll.pk]), {"option": [o.pk for o in options]}
        )


class VenuePollTests(PollTestCase):
    def test_host_creates_a_venue_poll_from_the_form(self):
        self.client.force_login(self.host)
        response = self.client.post(
            reverse("polls:create", args=[self.event.slug, "venue"]),
            {
                "question": "Which pub?", "description": "", "show_results": "always",
                "closes_at": timezone.localtime(self.closes).strftime("%Y-%m-%dT%H:%M"),
                "opt_label_0": "The Granta", "opt_label_1": "The Free Press",
            },
        )
        self.assertRedirects(response, self.event.get_absolute_url())
        poll = Poll.objects.get()
        self.assertEqual(poll.kind, "venue")
        self.assertEqual(poll.options.count(), 2)
        self.assertEqual(poll.created_by, self.host)

    def test_other_members_cannot_create_or_close_polls(self):
        poll, _ = self.make_poll("venue", ["A", "B"])
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.get(reverse("polls:choose", args=[self.event.slug])).status_code, 403
        )
        self.assertEqual(self.client.post(reverse("polls:close", args=[poll.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("polls:delete", args=[poll.pk])).status_code, 403)

    def test_vote_change_vote_and_single_choice(self):
        poll, (a, b) = self.make_poll("venue", ["A", "B"])
        self.vote(self.alice, poll, a)
        self.assertEqual(set(poll.choices_of(self.alice)), {a.pk})
        self.vote(self.alice, poll, b)
        self.assertEqual(set(poll.choices_of(self.alice)), {b.pk})
        self.vote(self.alice, poll, a, b)
        self.assertEqual(set(poll.choices_of(self.alice)), {b.pk})  # rejected, unchanged

    def test_closing_applies_the_winner_emails_attendees_and_audits(self):
        poll, (a, b) = self.make_poll("venue", ["The Granta", "The Free Press"])
        self.vote(self.alice, poll, a)
        self.vote(self.bob, poll, a)
        mail.outbox = []
        self.client.force_login(self.host)
        self.client.post(reverse("polls:close", args=[poll.pk]))
        self.event.refresh_from_db()
        poll.refresh_from_db()
        self.assertEqual(self.event.location, "The Granta")
        self.assertEqual(poll.outcome_option, a)
        self.assertIsNotNone(poll.applied_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ali1@cam.ac.uk", mail.outbox[0].bcc)
        self.assertIn("host1@cam.ac.uk", mail.outbox[0].bcc)
        self.assertTrue(AuditLog.objects.filter(action="apply_poll").exists())

    def test_tie_waits_for_the_host_to_decide(self):
        poll, (a, b) = self.make_poll("venue", ["A", "B"])
        self.vote(self.alice, poll, a)
        self.vote(self.bob, poll, b)
        self.client.force_login(self.host)
        self.client.post(reverse("polls:close", args=[poll.pk]))
        poll.refresh_from_db()
        self.assertTrue(poll.needs_decision)
        self.event.refresh_from_db()
        self.assertEqual(self.event.location, "TBC")
        self.client.post(reverse("polls:close", args=[poll.pk]), {"option": b.pk})
        poll.refresh_from_db()
        self.event.refresh_from_db()
        self.assertFalse(poll.needs_decision)
        self.assertEqual(self.event.location, "B")


class DateRotaAndVisibilityTests(PollTestCase):
    def test_date_poll_moves_the_start_and_keeps_the_duration(self):
        new_start = timezone.now() + datetime.timedelta(days=20)
        poll, (a, b) = self.make_poll(
            "date", [("Sat", {"start": new_start}), ("Sun", {"start": new_start + datetime.timedelta(days=1)})]
        )
        self.vote(self.alice, poll, a)
        poll.close(by=self.host)
        self.event.refresh_from_db()
        self.assertEqual(self.event.start, new_start)
        self.assertEqual(self.event.end - self.event.start, datetime.timedelta(hours=2))

    def test_opt_out_never_wins(self):
        poll, (a, none) = self.make_poll(
            "date", [("Sat", {"start": timezone.now() + datetime.timedelta(days=20)}),
                     ("None", {"is_opt_out": True})]
        )
        self.vote(self.alice, poll, none)
        self.vote(self.bob, poll, none)
        self.assertEqual(poll.close(), None)

    def test_rota_enforces_capacity_and_shows_the_host_a_roster(self):
        poll, (stall,) = self.make_poll(
            "volunteers", [("Stall 10-12", {"capacity": 1})], allow_multiple=True
        )
        self.vote(self.alice, poll, stall)
        self.vote(self.bob, poll, stall)
        self.assertEqual(stall.votes.count(), 1)
        roster = reverse("polls:roster", args=[poll.pk])
        self.assertEqual(self.client.get(roster).status_code, 403)  # bob is logged in
        self.client.force_login(self.host)
        response = self.client.get(roster)
        self.assertContains(response, "Test Ali1")
        self.assertContains(response, "+447700900000")
        csv_response = self.client.get(roster + "?format=csv")
        self.assertIn("Stall 10-12,Test Ali1", csv_response.content.decode())

    def test_results_hidden_until_close_when_asked(self):
        poll, (a, b) = self.make_poll("venue", ["A", "B"], show_results="after_close")
        self.vote(self.alice, poll, a)
        self.client.force_login(self.bob)
        response = self.client.get(self.event.get_absolute_url())
        self.assertNotContains(response, "poll-bar")
        self.client.force_login(self.host)
        self.assertContains(self.client.get(self.event.get_absolute_url()), "poll-bar")

    def test_voting_requires_login_and_an_open_poll(self):
        poll, (a, b) = self.make_poll("venue", ["A", "B"])
        response = self.client.post(reverse("polls:vote", args=[poll.pk]), {"option": a.pk})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        Poll.objects.filter(pk=poll.pk).update(closes_at=timezone.now() - datetime.timedelta(minutes=1))
        self.vote(self.alice, poll, a)
        self.assertFalse(PollVote.objects.exists())
        poll.refresh_from_db()
        self.assertEqual(poll.status, "closed")  # resolved lazily on load

    def test_close_polls_command_applies_due_polls(self):
        poll, (a, b) = self.make_poll("venue", ["Granta", "Castle"])
        self.vote(self.bob, poll, b)
        Poll.objects.filter(pk=poll.pk).update(closes_at=timezone.now() - datetime.timedelta(minutes=1))
        from io import StringIO
        call_command("close_polls", stdout=StringIO())
        self.event.refresh_from_db()
        self.assertEqual(self.event.location, "Castle")

    def test_dashboard_calendar_and_mailer_surface_open_polls(self):
        poll, _ = self.make_poll("venue", ["A", "B"])
        self.client.force_login(self.alice)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "Vote: <a")
        self.assertContains(response, "Where?")
        self.assertContains(self.client.get(reverse("events:calendar")), "📊 vote")
        from panel.services import build_whats_on_email
        from django.test import RequestFactory
        request = RequestFactory().get("/")
        _subject, body = build_whats_on_email(request)
        self.assertIn("HAVE YOUR SAY", body)
        self.assertIn(f"/polls/{poll.pk}/", body)

    def test_general_poll_is_admin_only_to_create_and_members_only_to_see(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("polls:choose_general")).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("polls:create_general", args=["general"]),
            {
                "question": "Garden party or punt?", "description": "",
                "show_results": "always",
                "closes_at": timezone.localtime(self.closes).strftime("%Y-%m-%dT%H:%M"),
                "opt_label_0": "Garden party", "opt_label_1": "Punt",
            },
        )
        poll = Poll.objects.get()
        self.assertRedirects(response, poll.get_absolute_url())
        self.client.logout()
        self.assertEqual(self.client.get(poll.get_absolute_url()).status_code, 302)


class IndexTests(PollTestCase):
    def test_index_lists_open_then_closed_polls_and_only_public_ones_to_visitors(self):
        self.make_poll(Poll.Kind.VENUE, ["Eagle", "Pickerel"])
        general = Poll.objects.create(kind=Poll.Kind.GENERAL, question="Garden party?", created_by=self.admin, closes_at=self.closes)
        PollOption.objects.create(poll=general, label="Yes")
        old = Poll.objects.create(kind=Poll.Kind.GENERAL, question="Old question", created_by=self.admin,
                                  closes_at=timezone.now() - datetime.timedelta(days=1))
        PollOption.objects.create(poll=old, label="Then")
        gone = Event.objects.create(
            title="Cancelled quiz", category=self.category, start=self.event.start, created_by=self.host,
            host=self.host, is_cancelled=True,
        )
        PollOption.objects.create(poll=Poll.objects.create(
            event=gone, kind=Poll.Kind.VENUE, question="Cancelled where?", created_by=self.host, closes_at=self.closes,
        ), label="Nowhere")
        page = self.client.get(reverse("polls:index"))
        self.assertContains(page, "Where?")              # on a public event
        self.assertNotContains(page, "Garden party?")    # general polls are for members
        self.assertNotContains(page, "Cancelled where?") # hidden events stay hidden
        self.client.force_login(self.alice)
        body = self.client.get(reverse("polls:index")).content.decode()
        self.assertIn("Garden party?", body)
        self.assertLess(body.index("Garden party?"), body.index("Old question"))  # open before closed
        old.refresh_from_db()
        self.assertEqual(old.status, Poll.Status.CLOSED)  # closed on the way past
        self.assertContains(self.client.get(reverse("events:calendar")), 'href="/polls/"')  # in the About menu
