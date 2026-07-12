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
