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
    path("", include(_stub_patterns("core", [
        "home", "about", "wellbeing", "policies", "terms", "winter_ball",
        "search", "dismiss_banner",
    ]))),
    path("guide/", include(_stub_patterns("guide", ["index"]))),
    path("faq/", include(_stub_patterns("faq", ["index", "contacts", "colleges", "departments"]))),
    path("me/", include(_stub_patterns("dashboard", ["home"]))),
    path("admin/", include(_stub_patterns("panel", ["home"]))),
    path("testimonials/", include(_stub_patterns("testimonials", ["index"]))),
    path("polls/", include("polls.urls")),
    path("surveys/", include("surveys.urls")),
    path("notifications/", include("notifications.urls")),
    path("posters/", include("posters.urls")),
    path("p/<slug:slug>/", _stub, name="poster_scan"),
    path("members/", include(([
        path("", _stub, name="directory"),
        path("<str:username>/", _stub, name="profile"),
    ], "members"))),
    path("messages/", include(([
        path("", _stub, name="inbox"),
        path("<str:username>/", _stub, name="thread"),
        path("<str:username>/block/", _stub, name="block_toggle"),
    ], "inbox"))),
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
        response = self.client.get(reverse("events:detail", args=[self.members_event.slug]))
        self.assertEqual(response.status_code, 404)

    def test_member_members_only_detail_ok(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("events:detail", args=[self.members_event.slug]))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_detail_no_attendee_names(self):
        response = self.client.get(reverse("events:detail", args=[self.public_event.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "avatar-token")


class PermissionTests(EventTestCase):
    def test_edit_forbidden_for_non_creator(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("events:edit", args=[self.public_event.slug]))
        self.assertEqual(response.status_code, 403)

    def test_edit_allowed_for_creator_and_admin(self):
        self.client.force_login(self.creator)
        self.assertEqual(
            self.client.get(reverse("events:edit", args=[self.public_event.slug])).status_code, 200
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("events:edit", args=[self.public_event.slug])).status_code, 200
        )

    def test_cancel_forbidden_for_non_creator(self):
        self.client.force_login(self.other)
        response = self.client.post(reverse("events:cancel", args=[self.public_event.slug]))
        self.assertEqual(response.status_code, 403)
        self.public_event.refresh_from_db()
        self.assertFalse(self.public_event.is_cancelled)

    def test_export_forbidden_for_non_creator(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("events:export", args=[self.public_event.slug]))
        self.assertEqual(response.status_code, 403)

    def test_export_requires_login(self):
        response = self.client.get(reverse("events:export", args=[self.public_event.slug]))
        self.assertEqual(response.status_code, 302)

    def test_export_ok_for_creator_and_admin(self):
        self.client.force_login(self.creator)
        response = self.client.get(reverse("events:export", args=[self.public_event.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.member.mobile)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("events:export", args=[self.public_event.slug])).status_code,
            200,
        )

    def test_export_csv(self):
        self.client.force_login(self.creator)
        response = self.client.get(
            reverse("events:export", args=[self.public_event.slug]), {"format": "csv"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode()
        self.assertIn("Member Tester", body)
        self.assertIn(self.member.mobile, body)


class RSVPTests(EventTestCase):
    def rsvp_url(self, event):
        return reverse("events:rsvp", args=[event.slug])

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

    def test_full_event_puts_a_new_rsvp_on_the_waitlist(self):
        self.client.force_login(self.other)
        self.client.post(self.rsvp_url(self.full_event))
        row = RSVP.objects.get(event=self.full_event, user=self.other)
        self.assertEqual(row.status, RSVP.Status.WAITING)
        self.assertEqual(self.full_event.going_count, 1)

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
            reverse("events:edit", args=[official.slug]),
            self.form_data(title="Big official night (edited)", is_official=""),
        )
        self.assertEqual(response.status_code, 302)
        official.refresh_from_db()
        self.assertTrue(official.is_official)  # flag untouched by non-admin edit

    def test_tag_owner_can_promote_within_own_tag_only(self):
        self.supper_category.owners.add(self.member)
        self.client.force_login(self.member)
        # Their own tag: promotion sticks.
        response = self.client.post(
            reverse("events:create"),
            self.form_data(
                title="Supper night", category=str(self.supper_category.pk),
                is_official="on",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.get(title="Supper night").is_official)
        # A tag they don't own: the form refuses.
        response = self.client.post(
            reverse("events:create"),
            self.form_data(title="Pub takeover", is_official="on"),
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        self.assertFalse(Event.objects.filter(title="Pub takeover").exists())

    def test_only_super_admin_can_set_super(self):
        # Portal admin (not super): the field doesn't exist, value ignored.
        self.client.force_login(self.admin)
        self.client.post(
            reverse("events:create"),
            self.form_data(title="Wannabe super", is_super="on"),
        )
        self.assertFalse(Event.objects.get(title="Wannabe super").is_super)
        # Super admin: it sticks.
        self.admin.is_super_admin = True
        self.admin.save(update_fields=["is_super_admin"])
        self.client.post(
            reverse("events:create"),
            self.form_data(title="True super", is_super="on"),
        )
        self.assertTrue(Event.objects.get(title="True super").is_super)

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


class AdditiveFilterTests(EventTestCase):
    """Calendar tag pills combine: ?cat=a&cat=b shows events from both."""

    def setUp(self):
        self.supper_event = Event.objects.create(
            title="Supper filter target", category=self.supper_category,
            start=timezone.now() + datetime.timedelta(days=5),
            created_by=self.creator,
        )

    def test_single_filter_still_works(self):
        response = self.client.get(reverse("events:calendar"), {"cat": "pub-nights"})
        self.assertContains(response, "Public pub night")
        self.assertNotContains(response, "Supper filter target")

    def test_filters_are_additive(self):
        response = self.client.get(
            reverse("events:calendar"), {"cat": ["pub-nights", "supper-club"]}
        )
        self.assertContains(response, "Public pub night")
        self.assertContains(response, "Supper filter target")

    def test_legacy_comma_links_still_work(self):
        response = self.client.get(
            reverse("events:calendar"), {"cat": "pub-nights,supper-club"}
        )
        self.assertContains(response, "Public pub night")
        self.assertContains(response, "Supper filter target")

    def test_unknown_slug_ignored(self):
        response = self.client.get(reverse("events:calendar"), {"cat": "not-a-tag"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public pub night")  # no filter applied


class WaitlistTests(EventTestCase):
    def test_full_event_offers_the_waitlist_and_promotes_on_cancellation(self):
        from django.core import mail

        url = reverse("events:rsvp", args=[self.full_event.slug])
        self.other.email = "other@cam.ac.uk"
        self.other.save(update_fields=["email"])
        self.client.force_login(self.other)
        response = self.client.post(url, follow=True)
        self.assertContains(response, "on the waitlist (number 1)")
        waiting = RSVP.objects.get(event=self.full_event, user=self.other)
        self.assertEqual(waiting.status, RSVP.Status.WAITING)
        self.assertEqual(self.full_event.going_count, 1)

        # The holder of the only place cancels: the waiter is promoted and told.
        mail.outbox = []
        self.client.force_login(self.member)
        self.client.post(url)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, RSVP.Status.GOING)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("place has opened up", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [self.other.email])

    def test_leaving_the_waitlist(self):
        RSVP.objects.create(event=self.full_event, user=self.other, status=RSVP.Status.WAITING)
        self.client.force_login(self.other)
        response = self.client.get(self.full_event.get_absolute_url())
        self.assertContains(response, "number 1 on the waitlist")
        self.client.post(reverse("events:rsvp", args=[self.full_event.slug]))
        self.assertEqual(
            RSVP.objects.get(event=self.full_event, user=self.other).status,
            RSVP.Status.CANCELLED,
        )


class CalendarFileTests(EventTestCase):
    def test_ics_download_has_the_event(self):
        response = self.client.get(reverse("events:ics", args=[self.public_event.slug]))
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode()
        self.assertIn("BEGIN:VEVENT", body)
        self.assertIn("SUMMARY:Public pub night", body)
        self.assertIn("LOCATION:The Free Press", body)
        self.assertIn(self.public_event.get_absolute_url(), body)

    def test_members_only_ics_needs_login(self):
        url = reverse("events:ics", args=[self.members_event.slug])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_ics_escapes_commas(self):
        from events.ics import escape
        self.assertEqual(escape("The Granta, Newnham"), "The Granta" + chr(92) + ", Newnham")


class DuplicateAndRepeatTests(EventTestCase):
    def test_duplicate_prefills_a_create_form_a_week_later(self):
        self.client.force_login(self.creator)
        response = self.client.get(reverse("events:duplicate", args=[self.public_event.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Public pub night"')
        self.assertContains(response, "copy of")
        later = timezone.localtime(self.public_event.start + datetime.timedelta(days=7))
        self.assertContains(response, later.strftime("%Y-%m-%dT%H:%M"))

    def test_repeat_creates_weekly_copies_for_the_creator_only(self):
        url = reverse("events:repeat", args=[self.public_event.slug])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"weeks": 2}).status_code, 403)
        self.client.force_login(self.creator)
        self.client.post(url, {"weeks": 3})
        copies = Event.objects.filter(title="Public pub night").exclude(pk=self.public_event.pk)
        self.assertEqual(copies.count(), 3)
        self.assertEqual(
            copies.order_by("start").last().start,
            self.public_event.start + datetime.timedelta(days=21),
        )
        self.assertTrue(all(c.location == "The Free Press" for c in copies))

    def test_repeat_rejects_silly_numbers(self):
        self.client.force_login(self.creator)
        self.client.post(reverse("events:repeat", args=[self.public_event.slug]), {"weeks": 40})
        self.assertEqual(Event.objects.filter(title="Public pub night").count(), 1)


class CalendarViewsTests(EventTestCase):
    def test_agenda_and_grid_both_render_with_a_layout_toggle(self):
        response = self.client.get(reverse("events:calendar") + "?view=list")
        self.assertContains(response, 'class="cal-agenda forced"')
        self.assertContains(response, "Public pub night")
        response = self.client.get(reverse("events:calendar") + "?view=grid")
        self.assertContains(response, 'class="cal-grid forced"')

    def test_term_week_labels_appear_when_term_dates_are_set(self):
        from core.models import SiteConfig
        config = SiteConfig.get()
        today = timezone.localdate()
        config.michaelmas_start = today - datetime.timedelta(days=today.weekday())
        config.save()
        response = self.client.get(reverse("events:calendar"))
        self.assertContains(response, "Michaelmas wk")

    def test_capacity_bar_on_coming_up_rows(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("events:calendar"))
        self.assertContains(response, "cap-fill")
        self.assertContains(response, ">full<")


class GroupsTests(EventTestCase):
    def test_groups_page_lists_every_tag_with_its_next_event(self):
        response = self.client.get(reverse("events:groups"))
        self.assertContains(response, "Pub Nights")
        self.assertContains(response, "Supper Club")
        self.assertContains(response, "Next: <a")
        self.assertContains(response, "Public pub night")
        self.assertNotContains(response, "Secret members social")  # members only, logged out
        self.assertContains(response, "Nothing scheduled just yet")  # Supper Club

    def test_group_pages_live_under_groups_and_old_links_redirect(self):
        url = reverse("events:tag_page", args=["pub-nights"])
        self.assertEqual(url, "/events/groups/pub-nights/")
        self.assertEqual(self.client.get(url).status_code, 200)
        old = self.client.get("/events/tags/pub-nights/")
        self.assertEqual(old.status_code, 301)
        self.assertEqual(old["Location"], url)

    def test_menu_has_event_calendar_and_groups_under_about(self):
        body = self.client.get(reverse("events:calendar")).content.decode()
        self.assertIn(">Event Calendar</a>", body)
        self.assertNotIn(">What's on</summary>", body)
        about = body[body.index(">About</summary>"):]
        about = about[:about.index("</details>")]
        self.assertIn(reverse("events:groups"), about)
        self.assertIn("/events/groups/supper-club/", about)
        self.assertIn('id="nav-toggle" class="nav-toggle" aria-hidden="true" tabindex="-1" hidden', body)

    def test_supper_club_page_links_to_the_restaurants(self):
        body = self.client.get(reverse("events:tag_page", args=["supper-club"])).content.decode()
        self.assertIn("Restaurants and ratings", body)


class StaticVersionTests(TestCase):
    def test_static_urls_carry_a_content_version(self):
        from django.templatetags.static import static

        url = static("css/base.css")
        self.assertRegex(url, r"css/base\.css\?v=[0-9a-f]{10}$")
        self.assertEqual(static("css/no-such-file.css"), "/static/css/no-such-file.css")


class EndTimeDefaultTests(EventTestCase):
    def test_the_end_follows_the_start_by_two_hours_unless_given(self):
        self.client.force_login(self.member)
        start = timezone.now() + datetime.timedelta(days=10)
        data = {
            "title": "Open-ended drinks", "category": str(self.category.pk), "description": "",
            "location": "The Eagle", "start": dt_local(start), "end": "", "capacity": "",
        }
        self.client.post(reverse("events:create"), data)
        event = Event.objects.get(title="Open-ended drinks")
        self.assertEqual(event.end - event.start, datetime.timedelta(hours=2))
        data.update(title="Long drinks", end=dt_local(start + datetime.timedelta(hours=5)))
        self.client.post(reverse("events:create"), data)
        event = Event.objects.get(title="Long drinks")
        self.assertEqual(event.end - event.start, datetime.timedelta(hours=5))
        data["end"] = ""  # on an existing event a blank end means open-ended
        self.client.post(reverse("events:edit", args=[event.slug]), data)
        event.refresh_from_db()
        self.assertIsNone(event.end)
