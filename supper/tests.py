"""Supper Club tests: rating permission rules and leaderboard ordering.

The suite runs against a self-contained URLconf (this module) that mounts the
real ``supper.urls`` plus no-op stubs for every URL name referenced by
base.html and the supper templates. That keeps these tests green regardless
of the build state of the other apps.
"""

import datetime

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from events.models import RSVP, Category, Event

from .models import Rating, Restaurant

User = get_user_model()


def _stub_view(request, *args, **kwargs):
    return HttpResponse("stub")


def _namespace(app_name, names, pk_names=()):
    patterns = [path(f"{name}/", _stub_view, name=name) for name in names]
    patterns += [
        path(f"{name}/<int:pk>/", _stub_view, name=name) for name in pk_names
    ]
    return include((patterns, app_name))


urlpatterns = [
    path("supper-club/", include("supper.urls")),
    path("accounts/", _namespace(
        "accounts",
        ["login", "logout", "raven", "profile_setup", "profile", "waitlist",
         "whatsapp", "set_password"],
    )),
    path("events/", include(([
        path("", _stub_view, name="calendar"),
        path("<slug:slug>/", _stub_view, name="detail"),
    ], "events"))),
    path("guide/", _namespace("guide", ["index"])),
    path("faq/", _namespace("faq", ["index", "contacts", "colleges", "departments"])),
    path("me/", _namespace("dashboard", ["home"])),
    path("panel/", _namespace("panel", ["home"])),
    path("members/", include(([
        path("", _stub_view, name="directory"),
        path("<str:username>/", _stub_view, name="profile"),
    ], "members"))),
    path("messages/", include(([
        path("", _stub_view, name="inbox"),
        path("<str:username>/", _stub_view, name="thread"),
        path("<str:username>/block/", _stub_view, name="block_toggle"),
    ], "inbox"))),
    path("", _namespace("core", ["home", "about", "wellbeing", "policies",
                                 "winter_ball"])),
]

VALID_SCORES = {
    "food": 5, "service": 4, "atmosphere": 4, "value": 5,
    "comment": "Best thali in Cambridge.",
}


@override_settings(ROOT_URLCONF="supper.tests")
class SupperClubTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.supper_cat = Category.objects.create(
            name="Supper Club", slug="supper-club", has_restaurant_ratings=True
        )
        cls.pub_cat = Category.objects.create(name="Pub Nights", slug="pub-nights")

        cls.organiser = cls.make_member("amk67", "Amara", "Kensington")
        cls.attendee = cls.make_member("rt489", "Robert", "Tanaka")
        cls.other_member = cls.make_member("hb244", "Henry", "Blackwood")

        cls.restaurant = Restaurant.objects.create(
            name="The Tiffin Truck", cuisine="Indian street food", area="Regent Street"
        )
        now = timezone.now()
        cls.past_event = Event.objects.create(
            title="Supper Club at The Tiffin Truck",
            category=cls.supper_cat,
            start=now - datetime.timedelta(days=7),
            end=now - datetime.timedelta(days=7) + datetime.timedelta(hours=2),
            created_by=cls.organiser,
            restaurant=cls.restaurant,
        )
        cls.future_event = Event.objects.create(
            title="Supper Club returns to The Tiffin Truck",
            category=cls.supper_cat,
            start=now + datetime.timedelta(days=7),
            created_by=cls.organiser,
            restaurant=cls.restaurant,
        )
        for event in (cls.past_event, cls.future_event):
            RSVP.objects.create(event=event, user=cls.attendee)

    @classmethod
    def make_member(cls, username, first, last):
        """A member with a complete profile (so middleware lets them through)."""
        return User.objects.create_user(
            username=username,
            password="test-password",
            email=f"{username}@example.com",
            first_name=first,
            last_name=last,
            college="wolfson",
            mobile="+44 7700 900000",
        )

    def login(self, user):
        self.client.login(username=user.username, password="test-password")

    def rate_url(self, event):
        return reverse("supper:rate", args=[event.pk])


class RatePermissionTests(SupperClubTestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.rate_url(self.past_event))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_non_attendee_gets_403(self):
        self.login(self.other_member)
        response = self.client.get(self.rate_url(self.past_event))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "RSVP next time", status_code=403)

    def test_non_attendee_post_gets_403_and_saves_nothing(self):
        self.login(self.other_member)
        response = self.client.post(self.rate_url(self.past_event), VALID_SCORES)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Rating.objects.count(), 0)

    def test_cancelled_rsvp_gets_403(self):
        RSVP.objects.filter(event=self.past_event, user=self.attendee).update(
            status=RSVP.Status.CANCELLED
        )
        self.login(self.attendee)
        response = self.client.get(self.rate_url(self.past_event))
        self.assertEqual(response.status_code, 403)

    def test_rating_before_event_start_is_rejected(self):
        self.login(self.attendee)
        response = self.client.post(
            self.rate_url(self.future_event), VALID_SCORES, follow=True
        )
        self.assertEqual(Rating.objects.count(), 0)
        self.assertContains(response, "You can rate after the dinner")

    def test_404_when_event_has_no_restaurant(self):
        event = Event.objects.create(
            title="Supper without a booking", category=self.supper_cat,
            start=timezone.now() - datetime.timedelta(days=1),
            created_by=self.organiser,
        )
        RSVP.objects.create(event=event, user=self.attendee)
        self.login(self.attendee)
        self.assertEqual(self.client.get(self.rate_url(event)).status_code, 404)

    def test_404_when_category_has_no_ratings(self):
        event = Event.objects.create(
            title="Pub night that happened to be at a restaurant",
            category=self.pub_cat,
            start=timezone.now() - datetime.timedelta(days=1),
            created_by=self.organiser,
            restaurant=self.restaurant,
        )
        RSVP.objects.create(event=event, user=self.attendee)
        self.login(self.attendee)
        self.assertEqual(self.client.get(self.rate_url(event)).status_code, 404)

    def test_cancelled_event_cannot_be_rated(self):
        # A called-off dinner must not accept ratings (they'd pollute the
        # public average). Even an attendee gets a 404.
        self.past_event.is_cancelled = True
        self.past_event.save(update_fields=["is_cancelled"])
        self.login(self.attendee)
        self.assertEqual(self.client.get(self.rate_url(self.past_event)).status_code, 404)
        self.assertEqual(
            self.client.post(self.rate_url(self.past_event), VALID_SCORES).status_code, 404
        )
        self.assertEqual(Rating.objects.count(), 0)


class RatingSummaryVisibilityTests(SupperClubTestCase):
    """The public restaurant score must never include members-only or
    cancelled visits — those would leak the existence/outcome of hidden
    events."""

    def _rate(self, event, user, score=5):
        Rating.objects.create(
            event=event, user=user, food=score, service=score,
            atmosphere=score, value=score,
        )

    def test_members_only_visit_excluded_from_public_summary(self):
        hidden = Event.objects.create(
            title="Hush-hush tasting menu", category=self.supper_cat,
            start=timezone.now() - datetime.timedelta(days=3),
            created_by=self.organiser, restaurant=self.restaurant,
            members_only=True,
        )
        RSVP.objects.create(event=hidden, user=self.attendee)
        self._rate(hidden, self.attendee, score=5)

        # Anonymous (viewer=None) must see nothing from the hidden visit.
        self.assertIsNone(self.restaurant.rating_summary(None))
        # A logged-in member sees the full aggregate.
        member_summary = self.restaurant.rating_summary(self.attendee)
        self.assertIsNotNone(member_summary)
        self.assertEqual(member_summary["visits"], 1)

    def test_cancelled_visit_excluded_from_summary(self):
        cancelled = Event.objects.create(
            title="Called-off supper", category=self.supper_cat,
            start=timezone.now() - datetime.timedelta(days=3),
            created_by=self.organiser, restaurant=self.restaurant,
            is_cancelled=True,
        )
        RSVP.objects.create(event=cancelled, user=self.attendee)
        self._rate(cancelled, self.attendee, score=1)
        # Even for a member, a cancelled visit is not in the score.
        self.assertIsNone(self.restaurant.rating_summary(self.attendee))


class RateBehaviourTests(SupperClubTestCase):
    def test_attendee_can_rate_past_event(self):
        self.login(self.attendee)
        response = self.client.post(self.rate_url(self.past_event), VALID_SCORES)
        self.assertRedirects(
            response, reverse("supper:restaurant", args=[self.restaurant.pk])
        )
        rating = Rating.objects.get()
        self.assertEqual(rating.user, self.attendee)
        self.assertEqual(rating.event, self.past_event)
        self.assertEqual(rating.food, 5)
        self.assertEqual(rating.comment, "Best thali in Cambridge.")

    def test_second_submission_updates_not_duplicates(self):
        self.login(self.attendee)
        self.client.post(self.rate_url(self.past_event), VALID_SCORES)
        second = dict(VALID_SCORES, food=1, comment="Went downhill fast.")
        response = self.client.post(self.rate_url(self.past_event), second, follow=True)
        self.assertEqual(Rating.objects.count(), 1)
        rating = Rating.objects.get()
        self.assertEqual(rating.food, 1)
        self.assertEqual(rating.comment, "Went downhill fast.")
        self.assertContains(response, "Rating updated")

    def test_form_is_prefilled_when_editing(self):
        Rating.objects.create(
            event=self.past_event, user=self.attendee,
            food=3, service=3, atmosphere=3, value=3,
        )
        self.login(self.attendee)
        response = self.client.get(self.rate_url(self.past_event))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_update"])

    def test_missing_dimension_is_rejected(self):
        self.login(self.attendee)
        data = dict(VALID_SCORES)
        del data["service"]
        response = self.client.post(self.rate_url(self.past_event), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Rating.objects.count(), 0)
        self.assertContains(response, "Pick a star rating")


class LeaderboardTests(SupperClubTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.better = Restaurant.objects.create(name="Vedanta", cuisine="Indian")
        cls.unvisited = Restaurant.objects.create(name="Limoncello", cuisine="Italian")
        better_visit = Event.objects.create(
            title="Supper Club at Vedanta", category=cls.supper_cat,
            start=timezone.now() - datetime.timedelta(days=30),
            created_by=cls.organiser, restaurant=cls.better,
        )
        # Tiffin Truck averages 3.0 overall; Vedanta averages 5.0.
        Rating.objects.create(
            event=cls.past_event, user=cls.attendee,
            food=3, service=3, atmosphere=3, value=3,
        )
        Rating.objects.create(
            event=better_visit, user=cls.organiser,
            food=5, service=5, atmosphere=5, value=5,
        )

    def test_leaderboard_orders_by_overall_average(self):
        response = self.client.get(reverse("supper:index"))
        self.assertEqual(response.status_code, 200)
        ranked = [row["restaurant"] for row in response.context["rated"]]
        self.assertEqual(ranked, [self.better, self.restaurant])
        self.assertEqual(response.context["rated"][0]["rank"], 1)
        self.assertEqual(response.context["rated"][0]["medal"], "🥇")
        self.assertIn(self.unvisited, list(response.context["unrated"]))

    def test_index_is_public(self):
        response = self.client.get(reverse("supper:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The MSS Supper Club")

    def test_members_only_supper_hidden_from_public_banner(self):
        Event.objects.create(
            title="Secret members-only supper", category=self.supper_cat,
            start=timezone.now() + datetime.timedelta(days=2),
            created_by=self.organiser, restaurant=self.restaurant,
            members_only=True,
        )
        response = self.client.get(reverse("supper:index"))
        self.assertNotContains(response, "Secret members-only supper")
        self.login(self.attendee)
        response = self.client.get(reverse("supper:index"))
        self.assertContains(response, "Secret members-only supper")


class RestaurantPageTests(SupperClubTestCase):
    def test_page_is_public_and_shows_ratings(self):
        Rating.objects.create(
            event=self.past_event, user=self.attendee,
            food=4, service=4, atmosphere=4, value=4, comment="Lovely evening.",
        )
        url = reverse("supper:restaurant", args=[self.restaurant.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lovely evening.")
        self.assertContains(response, self.attendee.first_name)

    def test_comment_is_escaped_not_rendered_as_html(self):
        Rating.objects.create(
            event=self.past_event, user=self.attendee,
            food=4, service=4, atmosphere=4, value=4,
            comment="<script>alert('pwned')</script>",
        )
        url = reverse("supper:restaurant", args=[self.restaurant.pk])
        response = self.client.get(url)
        self.assertNotContains(response, "<script>alert")
        self.assertContains(response, "&lt;script&gt;")

    def test_attended_unrated_viewer_sees_rate_prompt(self):
        self.login(self.attendee)
        url = reverse("supper:restaurant", args=[self.restaurant.pk])
        response = self.client.get(url)
        self.assertContains(response, "Rate this visit")
        Rating.objects.create(
            event=self.past_event, user=self.attendee,
            food=4, service=4, atmosphere=4, value=4,
        )
        response = self.client.get(url)
        self.assertNotContains(response, "Rate this visit")

    def test_members_only_visit_hidden_from_anonymous(self):
        hidden = Event.objects.create(
            title="Hush-hush tasting menu", category=self.supper_cat,
            start=timezone.now() - datetime.timedelta(days=3),
            created_by=self.organiser, restaurant=self.restaurant,
            members_only=True,
        )
        RSVP.objects.create(event=hidden, user=self.attendee)
        Rating.objects.create(
            event=hidden, user=self.attendee,
            food=5, service=5, atmosphere=5, value=5, comment="Secret feast.",
        )
        url = reverse("supper:restaurant", args=[self.restaurant.pk])
        response = self.client.get(url)
        self.assertNotContains(response, "Hush-hush tasting menu")
        self.assertNotContains(response, "Secret feast.")
        self.login(self.attendee)
        response = self.client.get(url)
        self.assertContains(response, "Hush-hush tasting menu")
        self.assertContains(response, "Secret feast.")
