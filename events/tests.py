"""Events app tests — visibility, permissions, RSVP capacity, is_official.

Uses a hermetic URLconf (this module doubles as ROOT_URLCONF) with stub views
for the other apps' URL names that base.html reverses, so these tests pass
regardless of how far the other apps have been built.
"""

import datetime

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from supper.models import Restaurant

from .models import RSVP, Category, Event

User = get_user_model()


def _stub(request, *args, **kwargs):
    return HttpResponse("stub")


def _stub_patterns(app_name, names):
    return ([path(f"{name}/", _stub, name=name) for name in names], app_name)


urlpatterns = [
    path("events/", include("events.urls")),
    path("accounts/", include(_stub_patterns("accounts", [
        "login", "logout", "raven", "profile_setup", "profile",
        "waitlist", "whatsapp", "set_password",
    ]))),
    path("", include(_stub_patterns("core", ["home", "about", "wellbeing", "policies"]))),
    path("guide/", include(_stub_patterns("guide", ["index"]))),
    path("me/", include(_stub_patterns("dashboard", ["home"]))),
    path("admin/", include(_stub_patterns("panel", ["home"]))),
    path("supper-club/", include(([
        path("", _stub, name="index"),
        path("restaurant/<int:pk>/", _stub, name="restaurant"),
        path("rate/<int:pk>/", _stub, name="rate"),
    ], "supper"))),
]


def make_user(username, admin=False):
    """A member with a complete profile (so ProfileCompletionMiddleware passes)."""
    return User.objects.create_user(
        username=username,
        password="pw",
        first_name=username.capitalize(),
        last_name="Tester",
        college="wolfson",
        mobile="+44 7700 900001",
        is_portal_admin=admin,
    )


def dt_local(value):
    """Format an aware datetime for a datetime-local form input."""
    return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M")


@override_settings(ROOT_URLCONF="events.tests")
class EventTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="Pub Nights", slug="pub-nights", color="#8a5a2c", emoji="🍺"
        )
        cls.supper_category = Category.objects.create(
            name="Supper Club", slug="supper-club", color="#b82818", emoji="🍽️",
            has_restaurant_ratings=True,
        )
        cls.creator = make_user("creator")
        cls.member = make_user("member")
        cls.other = make_user("other")
        cls.admin = make_user("admin", admin=True)

        now = timezone.now()
        cls.public_event = Event.objects.create(
            title="Public pub night", category=cls.category,
            start=now + datetime.timedelta(days=7),
            end=now + datetime.timedelta(days=7, hours=2),
            created_by=cls.creator, location="The Free Press",
        )
        cls.members_event = Event.objects.create(
            title="Secret members social", category=cls.category,
            start=now + datetime.timedelta(days=8),
            created_by=cls.creator, members_only=True,
        )
        cls.past_event = Event.objects.create(
            title="Bygone gathering", category=cls.category,
            start=now - datetime.timedelta(days=7),
            end=now - datetime.timedelta(days=7) + datetime.timedelta(hours=2),
            created_by=cls.creator,
        )
        cls.full_event = Event.objects.create(
            title="Tiny tasting", category=cls.category,
            start=now + datetime.timedelta(days=9),
            created_by=cls.creator, capacity=1,
        )
        RSVP.objects.create(event=cls.full_event, user=cls.member)
        RSVP.objects.create(event=cls.public_event, user=cls.member)


class VisibilityTests(EventTestCase):
    def test_anonymous_calendar_hides_members_only(self):
        response = self.client.get(reverse("events:calendar"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public pub night")
        self.assertNotContains(response, "Secret members social")

    def test_member_calendar_shows_members_only(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("events:calendar"))
        self.assertContains(response, "Secret members social")

    def test_anonymous_calendar_shows_counts_not_initials(self):
        response = self.client.get(reverse("events:calendar"))
        self.assertContains(response, "going")
        self.assertNotContains(response, "avatar-token")

    def test_member_calendar_shows_initials(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("events:calendar"))
        self.assertContains(response, "avatar-token")

    def test_anonymous_members_only_detail_404(self):
        response = self.client.get(reverse("events:detail", args=[self.members_event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_member_members_only_detail_ok(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("events:detail", args=[self.members_event.pk]))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_detail_no_attendee_names(self):
        response = self.client.get(reverse("events:detail", args=[self.public_event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "avatar-token")


class PermissionTests(EventTestCase):
    def test_edit_forbidden_for_non_creator(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("events:edit", args=[self.public_event.pk]))
        self.assertEqual(response.status_code, 403)

    def test_edit_allowed_for_creator_and_admin(self):
        self.client.force_login(self.creator)
        self.assertEqual(
            self.client.get(reverse("events:edit", args=[self.public_event.pk])).status_code, 200
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("events:edit", args=[self.public_event.pk])).status_code, 200
        )

    def test_cancel_forbidden_for_non_creator(self):
        self.client.force_login(self.other)
        response = self.client.post(reverse("events:cancel", args=[self.public_event.pk]))
        self.assertEqual(response.status_code, 403)
        self.public_event.refresh_from_db()
        self.assertFalse(self.public_event.is_cancelled)

    def test_export_forbidden_for_non_creator(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("events:export", args=[self.public_event.pk]))
        self.assertEqual(response.status_code, 403)

    def test_export_requires_login(self):
        response = self.client.get(reverse("events:export", args=[self.public_event.pk]))
        self.assertEqual(response.status_code, 302)

    def test_export_ok_for_creator_and_admin(self):
        self.client.force_login(self.creator)
        response = self.client.get(reverse("events:export", args=[self.public_event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.member.mobile)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("events:export", args=[self.public_event.pk])).status_code,
            200,
        )

    def test_export_csv(self):
        self.client.force_login(self.creator)
        response = self.client.get(
            reverse("events:export", args=[self.public_event.pk]), {"format": "csv"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode()
        self.assertIn("Member Tester", body)
        self.assertIn(self.member.mobile, body)


class RSVPTests(EventTestCase):
    def rsvp_url(self, event):
        return reverse("events:rsvp", args=[event.pk])

    def test_rsvp_toggles_without_deleting(self):
        self.client.force_login(self.other)
        self.client.post(self.rsvp_url(self.public_event))
        row = RSVP.objects.get(event=self.public_event, user=self.other)
        self.assertEqual(row.status, RSVP.Status.GOING)

        self.client.post(self.rsvp_url(self.public_event))
        row.refresh_from_db()  # row flipped, not deleted
        self.assertEqual(row.status, RSVP.Status.CANCELLED)

        self.client.post(self.rsvp_url(self.public_event))
        row.refresh_from_db()
        self.assertEqual(row.status, RSVP.Status.GOING)

    def test_full_event_rejects_new_rsvp(self):
        self.client.force_login(self.other)
        self.client.post(self.rsvp_url(self.full_event))
        self.assertFalse(
            RSVP.objects.filter(event=self.full_event, user=self.other).exists()
        )

    def test_full_event_allows_toggling_off_then_frees_a_spot(self):
        self.client.force_login(self.member)  # already going
        self.client.post(self.rsvp_url(self.full_event))
        row = RSVP.objects.get(event=self.full_event, user=self.member)
        self.assertEqual(row.status, RSVP.Status.CANCELLED)

        self.client.force_login(self.other)
        self.client.post(self.rsvp_url(self.full_event))
        self.assertTrue(
            RSVP.objects.filter(
                event=self.full_event, user=self.other, status=RSVP.Status.GOING
            ).exists()
        )

    def test_past_event_rejects_rsvp(self):
        self.client.force_login(self.other)
        self.client.post(self.rsvp_url(self.past_event))
        self.assertFalse(
            RSVP.objects.filter(event=self.past_event, user=self.other).exists()
        )

    def test_cancelled_event_rejects_rsvp(self):
        cancelled = Event.objects.create(
            title="Called off", category=self.category,
            start=timezone.now() + datetime.timedelta(days=3),
            created_by=self.creator, is_cancelled=True,
        )
        self.client.force_login(self.other)
        self.client.post(self.rsvp_url(cancelled))
        self.assertFalse(RSVP.objects.filter(event=cancelled, user=self.other).exists())

    def test_rsvp_requires_login(self):
        response = self.client.post(self.rsvp_url(self.public_event))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class OfficialFlagTests(EventTestCase):
    def form_data(self, **extra):
        start = timezone.now() + datetime.timedelta(days=10)
        data = {
            "title": "Posted event",
            "category": str(self.category.pk),
            "description": "",
            "location": "Somewhere",
            "start": dt_local(start),
            "end": dt_local(start + datetime.timedelta(hours=2)),
            "capacity": "",
        }
        data.update(extra)
        return data

    def test_non_admin_cannot_set_official(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse("events:create"), self.form_data(is_official="on")
        )
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title="Posted event")
        self.assertFalse(event.is_official)

    def test_admin_can_set_official(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("events:create"),
            self.form_data(title="Official do", is_official="on"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.get(title="Official do").is_official)

    def test_non_admin_edit_preserves_official_flag(self):
        official = Event.objects.create(
            title="Big official night", category=self.category,
            start=timezone.now() + datetime.timedelta(days=5),
            created_by=self.creator, is_official=True,
        )
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("events:edit", args=[official.pk]),
            self.form_data(title="Big official night (edited)", is_official=""),
        )
        self.assertEqual(response.status_code, 302)
        official.refresh_from_db()
        self.assertTrue(official.is_official)  # flag untouched by non-admin edit

    def test_past_start_requires_override_on_create(self):
        self.client.force_login(self.member)
        start = timezone.now() - datetime.timedelta(days=2)
        data = self.form_data(
            title="Backdated", start=dt_local(start),
            end=dt_local(start + datetime.timedelta(hours=2)),
        )
        response = self.client.post(reverse("events:create"), data)
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.assertFalse(Event.objects.filter(title="Backdated").exists())

        data["allow_past"] = "on"
        response = self.client.post(reverse("events:create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(title="Backdated").exists())

    def test_end_must_follow_start(self):
        self.client.force_login(self.member)
        start = timezone.now() + datetime.timedelta(days=4)
        data = self.form_data(
            title="Inverted", start=dt_local(start),
            end=dt_local(start - datetime.timedelta(hours=1)),
        )
        response = self.client.post(reverse("events:create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.filter(title="Inverted").exists())


class RestaurantCreationTests(EventTestCase):
    def test_new_restaurant_get_or_created(self):
        self.client.force_login(self.member)
        start = timezone.now() + datetime.timedelta(days=6)
        response = self.client.post(reverse("events:create"), {
            "title": "Supper at Noodles Plus",
            "category": str(self.supper_category.pk),
            "description": "",
            "location": "Mill Road",
            "start": dt_local(start),
            "end": dt_local(start + datetime.timedelta(hours=2)),
            "capacity": "",
            "restaurant": "",
            "new_restaurant": "Noodles Plus",
        })
        self.assertEqual(response.status_code, 302)
        restaurant = Restaurant.objects.get(name="Noodles Plus")
        self.assertEqual(restaurant.added_by, self.member)
        event = Event.objects.get(title="Supper at Noodles Plus")
        self.assertEqual(event.restaurant, restaurant)

    def test_restaurant_ignored_for_non_supper_category(self):
        self.client.force_login(self.member)
        start = timezone.now() + datetime.timedelta(days=6)
        response = self.client.post(reverse("events:create"), {
            "title": "Pub night with stray restaurant",
            "category": str(self.category.pk),
            "description": "",
            "location": "",
            "start": dt_local(start),
            "end": "",
            "capacity": "",
            "new_restaurant": "Should Not Exist",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Restaurant.objects.filter(name="Should Not Exist").exists())
        event = Event.objects.get(title="Pub night with stray restaurant")
        self.assertIsNone(event.restaurant)
