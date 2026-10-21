"""The formal allocation ledger cannot be bypassed through ordinary events."""
import datetime

from django.contrib import admin
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.admin import EventAdmin
from events.forms import EventForm
from events.models import Category, Event, RSVP
from formals.models import FormalBooking, FormalExchange, FormalListing
from inbox.models import MessageBlock
from inbox.policy import can_message
from panel.services import whats_on_events
from polls.models import Poll, PollOption
from polls.services import apply_outcome


class FormalIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user(
            username="formal-host", first_name="Host", last_name="Member",
            college="wolfson", mobile="07700900001",
        )
        cls.guest = User.objects.create_user(
            username="formal-guest", first_name="Guest", last_name="Member",
            college="darwin", mobile="07700900002",
        )
        cls.committee = User.objects.create_user(
            username="formal-admin", first_name="Committee", last_name="Member",
            college="wolfson", mobile="07700900003", is_portal_admin=True,
            is_staff=True, is_superuser=True,
        )
        cls.category = Category.objects.create(name="Formals", slug="formals")
        cls.event = Event.objects.create(
            title="Private college invitation", category=cls.category,
            created_by=cls.host, host=cls.host, members_only=True,
            start=timezone.now() + datetime.timedelta(days=3), capacity=2,
        )
        cls.listing = FormalListing.objects.create(
            event=cls.event, college="wolfson", capacity=2,
            booking_closes_at=timezone.now() + datetime.timedelta(days=2),
        )

    def reserve(self, status="reserved"):
        return FormalBooking.objects.create(
            listing=self.listing, guest=self.guest, status=status,
            reserved_at=timezone.now() if status in ("reserved", "cancelled") else None,
        )

    def test_anonymous_events_search_and_mailer_never_include_formals(self):
        # The relation protects privacy even if a generic flag is corrupted.
        Event.objects.filter(pk=self.event.pk).update(members_only=False)
        for route in ("core:home", "events:calendar", "core:search"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route), {"q": "Private college"})
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.event.title)
        self.assertNotIn(self.event, whats_on_events())
        for name, args in (
            ("events:detail", [self.event.slug]), ("events:detail_pk", [self.event.pk]),
            ("events:ics", [self.event.slug]), ("poster_scan", [self.event.slug]),
        ):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404)

    def test_member_navigation_and_event_detail_use_formal_workspace(self):
        self.client.force_login(self.guest)
        self.assertContains(self.client.get(reverse("core:menu")), reverse("formals:index"))
        self.assertEqual(self.event.get_absolute_url(), self.listing.get_absolute_url())
        self.assertRedirects(
            self.client.get(reverse("events:detail", args=[self.event.slug])),
            self.listing.get_absolute_url(), fetch_redirect_response=False,
        )

    def test_formal_poster_assets_are_only_for_the_host_or_committee(self):
        self.client.force_login(self.guest)
        for name in ("studio", "svg", "print", "map"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"posters:{name}", args=[self.event.slug])).status_code, 404)

    def test_shared_event_cards_cache_formal_relation(self):
        self.client.force_login(self.guest)
        response = self.client.get(reverse("events:groups"))
        formal_group = next(group for group in response.context["groups"] if group.pk == self.category.pk)
        with self.assertNumQueries(0):
            self.assertEqual(formal_group.next_event.get_absolute_url(), self.listing.get_absolute_url())
        response = self.client.get(reverse("core:home"))
        with self.assertNumQueries(0):
            for event in response.context["next_events"]:
                self.assertTrue(event.is_formal)

    def test_generic_rsvp_cannot_allocate_a_formal_place(self):
        self.client.force_login(self.guest)
        response = self.client.post(reverse("events:rsvp", args=[self.event.slug]))
        self.assertRedirects(response, self.listing.get_absolute_url(), fetch_redirect_response=False)
        self.assertFalse(RSVP.objects.filter(event=self.event).exists())
        self.assertFalse(FormalBooking.objects.filter(listing=self.listing).exists())

    def test_generic_host_mutations_redirect_without_changing_formal(self):
        self.client.force_login(self.host)
        count = Event.objects.count()
        for route in ("events:edit", "events:cancel", "events:duplicate", "events:repeat", "events:export"):
            with self.subTest(route=route):
                response = self.client.post(reverse(route, args=[self.event.slug]), {
                    "weeks": 3, "title": "Bypass", "members_only": "",
                })
                self.assertRedirects(response, self.listing.get_absolute_url(), fetch_redirect_response=False)
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_cancelled)
        self.assertTrue(self.event.members_only)
        self.assertEqual(self.event.title, "Private college invitation")
        self.assertEqual(Event.objects.count(), count)

    def test_category_ownership_does_not_grant_formal_management(self):
        self.category.owners.add(self.guest)
        self.assertFalse(self.event.can_edit(self.guest))
        self.client.force_login(self.guest)
        response = self.client.post(reverse("panel:tagged_event_action", args=[self.event.pk]), {"action": "promote"})
        self.assertEqual(response.status_code, 403)

    def test_generic_committee_actions_and_admin_cannot_change_formal(self):
        self.client.force_login(self.committee)
        for action in ("delete", "cancel", "restore", "toggle_official", "toggle_super"):
            response = self.client.post(reverse("panel:event_action", args=[self.event.pk]), {"action": action})
            self.assertRedirects(response, self.listing.get_absolute_url(), fetch_redirect_response=False)
        request = RequestFactory().get("/")
        request.user = self.committee
        model_admin = EventAdmin(Event, admin.site)
        self.assertFalse(model_admin.has_change_permission(request, self.event))
        self.assertFalse(model_admin.has_delete_permission(request, self.event))
        self.assertEqual(model_admin.get_inline_instances(request, self.event), [])
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_cancelled)

    def test_poll_outcome_cannot_move_formal(self):
        poll = Poll.objects.create(
            event=self.event, created_by=self.host, kind="date", question="Move?",
            closes_at=timezone.now() + datetime.timedelta(hours=1),
        )
        option = PollOption.objects.create(
            poll=poll, label="Later", start=self.event.start + datetime.timedelta(days=1),
        )
        poll.outcome_option = option
        original = self.event.start
        apply_outcome(poll, self.host)
        self.event.refresh_from_db()
        self.assertEqual(self.event.start, original)
        poll.refresh_from_db()
        self.assertIsNone(poll.applied_at)
        self.client.force_login(self.host)
        self.assertEqual(self.client.get(reverse("polls:choose", args=[self.event.slug])).status_code, 403)

    def test_booking_drives_dashboard_counts_and_private_calendar(self):
        self.reserve()
        self.assertEqual(self.event.going_count, 1)
        self.client.force_login(self.guest)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["next_up"], self.event)
        token = self.guest.get_calendar_token()
        response = self.client.get(reverse("dashboard:calendar_feed"), {"token": token})
        self.assertContains(response, self.event.title)
        self.assertContains(response, self.listing.get_absolute_url())
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertFalse(RSVP.objects.exists())

    def test_cancelled_reservation_keeps_private_calendar_uid_as_cancelled(self):
        self.reserve("cancelled")
        response = self.client.get(reverse("dashboard:calendar_feed"), {"token": self.guest.get_calendar_token()})
        self.assertContains(response, f"UID:mss-event-{self.event.pk}@portal")
        self.assertContains(response, "STATUS:CANCELLED")
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_cancelled)

    def test_formal_history_prevents_account_deletion_cleanly(self):
        self.reserve()
        self.client.force_login(self.committee)
        for member in (self.host, self.guest):
            response = self.client.post(reverse("panel:member_delete", args=[member.pk]), follow=True)
            self.assertContains(response, "Formal Exchange history")
            self.assertTrue(User.objects.filter(pk=member.pk).exists())

    def test_outstanding_return_permits_messages_after_first_dinner(self):
        booking = self.reserve()
        FormalExchange.objects.create(
            original_booking=booking, creditor=self.host, debtor=self.guest,
            status="owed", return_college="darwin",
            return_by=timezone.localdate() + datetime.timedelta(days=30),
        )
        Event.objects.filter(pk=self.event.pk).update(start=timezone.now() - datetime.timedelta(days=1))
        self.assertTrue(can_message(self.host, self.guest))
        self.assertTrue(can_message(self.guest, self.host))
        MessageBlock.objects.create(user=self.host, blocked=self.guest)
        self.assertFalse(can_message(self.guest, self.host))

    def test_inactive_member_cannot_use_private_feed(self):
        token = self.guest.get_calendar_token()
        User.objects.filter(pk=self.guest.pk).update(is_active=False)
        self.assertEqual(self.client.get(reverse("dashboard:calendar_feed"), {"token": token}).status_code, 404)

    def test_reserved_formal_category_is_hidden_from_anonymous_surfaces(self):
        reserved = Category.objects.create(name="Formal Exchange", slug="formal-exchange")
        for name in ("core:home", "core:menu", "events:calendar", "events:groups"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Formal Exchange")
                self.assertNotContains(response, "formal-exchange")
        self.assertContains(self.client.get(reverse("events:groups")), self.category.name)
        for route in ("events:tag_page", "events:tag_page_moved"):
            self.assertEqual(self.client.get(reverse(route, args=[reserved.slug])).status_code, 404)
        self.client.force_login(self.guest)
        for name in ("core:menu", "events:calendar", "events:groups"):
            self.assertContains(self.client.get(reverse(name)), "Formal Exchange")
        self.assertEqual(self.client.get(reserved.get_absolute_url()).status_code, 200)

    def test_generic_event_forms_reject_reserved_formal_category(self):
        reserved = Category.objects.create(name="Formal Exchange", slug="formal-exchange")
        form = EventForm(user=self.host, is_create=True)
        self.assertNotIn(reserved, form.fields["category"].queryset)
        self.assertIn(self.category, form.fields["category"].queryset)
        self.client.force_login(self.host)
        response = self.client.post(reverse("events:create"), {
            "title": "Attempted ordinary formal", "category": reserved.pk,
            "start": timezone.localtime(self.event.start).strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("category", response.context["form"].errors)
        self.assertFalse(Event.objects.filter(title="Attempted ordinary formal").exists())

    def test_dashboard_checklist_and_attendance_follow_formal_booking_state(self):
        booking = self.reserve("requested")
        Event.objects.filter(pk=self.event.pk).update(start=timezone.now() - datetime.timedelta(days=1))
        self.client.force_login(self.guest)
        for state, first_event_done, attended in (
            ("requested", False, 0), ("held", False, 0),
            ("reserved", True, 0), ("attended", True, 1),
        ):
            with self.subTest(state=state):
                booking.status = state
                booking.save(update_fields=["status"])
                response = self.client.get(reverse("dashboard:home"))
                self.assertEqual(response.context["checklist"][-1]["done"], first_event_done)
                self.assertEqual(response.context["stats"]["attended"], attended)
