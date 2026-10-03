"""Tests for the member dashboard: login gating and the "next up" logic."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import RSVP, Category, Event
from supper.models import Restaurant

User = get_user_model()


def make_member(username, **extra):
    defaults = dict(
        first_name="Dana", last_name="Member", college="hughes-hall",
        mobile="+44 7700 900001", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class DashboardTests(TestCase):
    def setUp(self):
        self.member = make_member("dm999")
        self.category = Category.objects.create(
            name="Supper Club", slug="supper-club", emoji="🍽️",
            has_restaurant_ratings=True,
        )
        self.now = timezone.now()

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_shows_next_up_rsvp(self):
        later = Event.objects.create(
            title="Far Future Formal", category=self.category,
            start=self.now + datetime.timedelta(days=20), created_by=self.member,
        )
        sooner = Event.objects.create(
            title="Imminent Supper", category=self.category,
            start=self.now + datetime.timedelta(days=2), created_by=self.member,
        )
        RSVP.objects.create(event=later, user=self.member)
        RSVP.objects.create(event=sooner, user=self.member)

        self.client.force_login(self.member)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["next_up"], sooner)
        self.assertContains(response, "Imminent Supper")
        self.assertContains(response, "Next up for you")

    def test_cancelled_rsvp_not_next_up(self):
        event = Event.objects.create(
            title="Changed My Mind", category=self.category,
            start=self.now + datetime.timedelta(days=3), created_by=self.member,
        )
        RSVP.objects.create(
            event=event, user=self.member, status=RSVP.Status.CANCELLED
        )
        self.client.force_login(self.member)
        response = self.client.get(reverse("dashboard:home"))
        self.assertIsNone(response.context["next_up"])

    def test_unrated_supper_visit_prompt(self):
        restaurant = Restaurant.objects.create(name="The Tiffin Truck")
        visit = Event.objects.create(
            title="Past Supper", category=self.category,
            start=self.now - datetime.timedelta(days=5),
            created_by=self.member, restaurant=restaurant,
        )
        RSVP.objects.create(event=visit, user=self.member)
        self.client.force_login(self.member)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "Rate The Tiffin Truck")
        # Attendance stat counts the past visit.
        self.assertEqual(response.context["stats"]["attended"], 1)

    def test_no_admin_card_for_regular_members(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("dashboard:home"))
        self.assertNotIn("pending_approvals", response.context)
        self.assertNotContains(response, "Open the admin panel")


class KeepyUppyTests(TestCase):
    """The hidden football: score submission and leaderboard rules."""

    def setUp(self):
        self.member = make_member("ku001")
        self.rival = make_member("ku002", first_name="Riva", last_name="Larsen")
        self.url = reverse("dashboard:game_scores")

    def submit(self, score):
        return self.client.post(
            self.url, {"score": score}, content_type="application/json"
        )

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_submit_keeps_personal_best_only(self):
        self.client.force_login(self.member)
        self.assertEqual(self.submit(7).json()["best"], 7)
        self.assertEqual(self.submit(3).json()["best"], 7)   # lower: kept
        self.assertEqual(self.submit(12).json()["best"], 12)  # higher: replaces

    def test_leaderboard_orders_and_flags_me(self):
        self.client.force_login(self.rival)
        self.submit(20)
        self.client.force_login(self.member)
        data = self.submit(5).json()
        self.assertEqual([row["score"] for row in data["leaderboard"]], [20, 5])
        self.assertEqual([row["me"] for row in data["leaderboard"]], [False, True])

    def test_shadow_banned_scores_hidden(self):
        self.client.force_login(self.rival)
        self.submit(50)
        self.rival.is_shadow_banned = True
        self.rival.save(update_fields=["is_shadow_banned"])
        self.client.force_login(self.member)
        data = self.client.get(self.url).json()
        self.assertEqual(data["leaderboard"], [])

    def test_garbage_rejected_and_scores_capped(self):
        self.client.force_login(self.member)
        response = self.client.post(
            self.url, "not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.submit(999999).json()["best"], 10000)  # capped


class CalendarFeedTests(TestCase):
    def setUp(self):
        self.member = make_member("cf001")
        self.other = make_member("cf002")
        category = Category.objects.create(name="Walks", slug="walks")
        start = timezone.now() + datetime.timedelta(days=3)
        self.going = Event.objects.create(
            title="River walk", category=category, start=start, created_by=self.other,
        )
        self.hosting = Event.objects.create(
            title="My pub quiz", category=category, start=start, created_by=self.member,
        )
        self.not_mine = Event.objects.create(
            title="Someone else's thing", category=category, start=start, created_by=self.other,
        )
        RSVP.objects.create(event=self.going, user=self.member)

    def test_feed_needs_a_valid_token_and_lists_my_events(self):
        self.assertEqual(self.client.get("/me/calendar.ics").status_code, 404)
        self.assertEqual(self.client.get("/me/calendar.ics?token=nope").status_code, 404)
        token = self.member.get_calendar_token()
        response = self.client.get(f"/me/calendar.ics?token={token}")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("SUMMARY:River walk", body)
        self.assertIn("SUMMARY:My pub quiz", body)
        self.assertNotIn("Someone else", body)

    def test_dashboard_shows_the_link_and_reset_changes_it(self):
        token = self.member.get_calendar_token()
        self.client.force_login(self.member)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, f"calendar.ics?token={token}")
        self.client.post(reverse("dashboard:calendar_token_reset"))
        self.member.refresh_from_db()
        self.assertNotEqual(self.member.calendar_token, token)
        self.assertEqual(self.client.get(f"/me/calendar.ics?token={token}").status_code, 404)
