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
        "search", "dismiss_banner", "menu", "preview",
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


def page_tags(response, name):
    """Every <name> start tag on a page, as a list of attribute dicts."""
    from html.parser import HTMLParser

    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.found = []

        def handle_starttag(self, tag, attrs):
            if tag == name:
                self.found.append(dict(attrs))

    parser = Tags()
    parser.feed(response.content.decode())
    return parser.found


def query_of(link):
    from urllib.parse import parse_qs, urlsplit
    return parse_qs(urlsplit(link).query)


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

    def test_export_csv_never_lets_a_name_run_as_a_formula(self):
        type(self.member).objects.filter(pk=self.member.pk).update(first_name='=HYPERLINK("https://x.example","y")')
        self.client.force_login(self.creator)
        body = self.client.get(reverse("events:export", args=[self.public_event.slug]), {"format": "csv"}).content.decode()
        self.assertIn("'=HYPERLINK", body)
        self.assertNotIn('\n"=HYPERLINK', body)


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


class TagFilterTests(EventTestCase):
    """The calendar's tag filter folds away; chosen tags stay in view as chips."""

    def tag_list(self, response):
        """The attributes of the fold-away tag list."""
        return next(d for d in page_tags(response, "details") if d.get("class") == "tag-filter")

    def test_the_tag_list_starts_closed_and_quiet(self):
        response = self.client.get(reverse("events:calendar"))
        self.assertNotIn("open", self.tag_list(response))
        self.assertContains(response, "Filter by tag")
        self.assertNotContains(response, "tag-filter-count")
        self.assertNotContains(response, "tag-active")
        self.assertNotContains(response, "Clear tags")
        self.assertFalse(response.context["tags_open"])
        self.assertEqual(response.context["chips"], [])

    def test_chosen_tags_show_as_chips_with_a_count_while_the_list_is_closed(self):
        response = self.client.get(reverse("events:calendar"), {"cat": ["pub-nights", "supper-club"]})
        self.assertNotIn("open", self.tag_list(response))
        self.assertContains(
            response,
            '<span class="tag-filter-count">2<span class="visually-hidden"> chosen</span></span>',
            html=True,
        )
        self.assertContains(response, "Showing only")
        self.assertContains(response, "Clear tags")
        self.assertContains(response, '<span class="visually-hidden">Remove </span>', count=2)
        chips = response.context["chips"]
        self.assertEqual([chip["cat"].slug for chip in chips], ["pub-nights", "supper-club"])
        first = query_of(chips[0]["query"])
        self.assertEqual(first["cat"], ["supper-club"])  # removes itself, keeps the other
        self.assertNotIn("tags", first)                  # and leaves the list closed
        self.assertNotIn("cat", query_of(response.context["all_query"]))
        self.assertNotIn("tags", query_of(response.context["all_query"]))

    def test_picking_a_tag_keeps_the_list_open(self):
        response = self.client.get(reverse("events:calendar"), {"cat": "pub-nights"})
        pills = {pill["cat"].slug: pill for pill in response.context["pills"]}
        add = query_of(pills["supper-club"]["query"])
        self.assertEqual(sorted(add["cat"]), ["pub-nights", "supper-club"])
        self.assertEqual(add["tags"], ["open"])
        remove = query_of(pills["pub-nights"]["query"])
        self.assertNotIn("cat", remove)
        self.assertEqual(remove["tags"], ["open"])
        everything = query_of(response.context["all_open_query"])
        self.assertNotIn("cat", everything)
        self.assertEqual(everything["tags"], ["open"])
        opened = self.client.get(reverse("events:calendar") + pills["supper-club"]["query"])
        self.assertIn("open", self.tag_list(opened))
        self.assertTrue(opened.context["tags_open"])
        self.assertEqual(opened.context["active_slugs"], ["pub-nights", "supper-club"])
        self.assertContains(opened, "Public pub night")

    def test_nothing_outside_the_list_keeps_it_open(self):
        response = self.client.get(
            reverse("events:calendar"),
            {"cat": "pub-nights", "q": "pub", "view": "grid", "tags": "open"},
        )
        self.assertIn("open", self.tag_list(response))
        for key in ("prev_query", "next_query", "today_query", "grid_query", "list_query",
                    "clear_search_query", "all_query"):
            self.assertNotIn("tags", query_of(response.context[key]), key)
        for chip in response.context["chips"]:
            self.assertNotIn("tags", query_of(chip["query"]))
        for week in response.context["grid"]:
            for day in week["days"]:
                self.assertNotIn("tags", query_of(day["more_query"]))
        self.assertNotContains(response, 'name="tags"')  # searching closes the list
        other = self.client.get(reverse("events:calendar"), {"tags": "1"})
        self.assertNotIn("open", self.tag_list(other))  # only tags=open opens it

    def test_filter_links_come_back_to_the_filter_and_say_which_are_chosen(self):
        response = self.client.get(reverse("events:calendar"), {"cat": "pub-nights"})
        links = [a for a in page_tags(response, "a")
                 if "tag-pill" in a.get("class", "") or a.get("class") == "tag-clear"]
        # "All", one pill per tag, one chip for the chosen tag, and "Clear tags".
        self.assertEqual(len(links), len(response.context["pills"]) + 3)
        for link in links:
            self.assertTrue(link["href"].endswith("#tag-filter"), link["href"])
            self.assertEqual(link.get("rel"), "nofollow")
        # Chosen is said in words: once in the list (Pub Nights), not on "All".
        self.assertContains(response, '<span class="visually-hidden">, chosen</span>', count=1)
        unfiltered = self.client.get(reverse("events:calendar"))
        self.assertContains(unfiltered, 'All<span class="visually-hidden">, chosen</span>', count=1)


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
        from notifications.models import NotificationEmail
        from notifications.services import process_notification_emails
        self.assertEqual(NotificationEmail.objects.filter(recipient=self.other).count(), 1)
        self.assertEqual(len(mail.outbox), 0)
        process_notification_emails()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("place has opened up", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [self.other.email])

    @override_settings(SITE_URL="https://portal.example.test")
    def test_the_promotion_email_gives_the_event_page_once_as_a_button(self):
        from django.core import mail

        from notifications.services import process_notification_emails

        from . import services

        self.other.email = "other@cam.ac.uk"
        self.other.save(update_fields=["email"])
        waiting = RSVP.objects.create(event=self.full_event, user=self.other, status=RSVP.Status.WAITING)
        mail.outbox = []
        services.tell_promoted(self.full_event, [waiting])
        process_notification_emails()
        message, = mail.outbox
        address = "https://portal.example.test" + self.full_event.get_absolute_url()
        # the address has a line to itself at the end of the text part ...
        self.assertTrue(message.body.endswith(f"so the next member can join.\n\n{address}"), message.body)
        self.assertEqual(message.body.count(address), 1)
        # ... so the HTML part shows it as one button, not as "join: the event page"
        (html, _mimetype), = message.alternatives
        self.assertEqual(html.count(f'href="{address}"'), 1)
        self.assertIn(">Open the event page</a>", html)
        self.assertNotIn("join: ", html)
        self.assertNotIn(">the event page</a>", html)

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
        # the same time on the clock a week on, whatever the clocks do in between
        later = timezone.localtime(self.public_event.start).replace(tzinfo=None) + datetime.timedelta(days=7)
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
            timezone.localtime(copies.order_by("start").last().start).replace(tzinfo=None),
            timezone.localtime(self.public_event.start).replace(tzinfo=None) + datetime.timedelta(days=21),
        )
        self.assertTrue(all(c.location == "The Free Press" for c in copies))

    def clock_change_lunch(self, month, days_before):
        """A 12:30 to 13:30 event so many days before next year's clock change
        (the last Sunday of March or October)."""
        year = timezone.localdate().year + 1
        last = datetime.date(year, month, 31)
        sunday = last - datetime.timedelta(days=(last.weekday() + 1) % 7)
        day = sunday - datetime.timedelta(days=days_before)
        start = timezone.make_aware(datetime.datetime.combine(day, datetime.time(12, 30)))
        return Event.objects.create(
            title=f"Clock change lunch {month}", category=self.category, created_by=self.creator,
            start=start, end=start + datetime.timedelta(hours=1),
        )

    def test_repeat_keeps_the_time_on_the_clock_when_the_clocks_change(self):
        self.client.force_login(self.creator)
        for month in (3, 10):
            with self.subTest(month=month):
                source = self.clock_change_lunch(month, days_before=9)
                self.client.post(reverse("events:repeat", args=[source.slug]), {"weeks": 3})
                copies = Event.objects.filter(title=source.title).exclude(pk=source.pk)
                self.assertEqual(copies.count(), 3)
                for copy in copies:
                    start, end = timezone.localtime(copy.start), timezone.localtime(copy.end)
                    self.assertEqual((start.hour, start.minute), (12, 30), copy.start)
                    self.assertEqual((end.hour, end.minute), (13, 30), copy.end)
                    self.assertEqual((start.date() - timezone.localtime(source.start).date()).days % 7, 0)

    def test_duplicate_keeps_the_time_on_the_clock_when_the_clocks_change(self):
        self.client.force_login(self.creator)
        for month in (3, 10):
            with self.subTest(month=month):
                source = self.clock_change_lunch(month, days_before=2)
                response = self.client.get(reverse("events:duplicate", args=[source.slug]))
                later = timezone.localtime(source.start).date() + datetime.timedelta(days=7)
                self.assertContains(response, f"{later.isoformat()}T12:30")
                self.assertContains(response, f"{later.isoformat()}T13:30")

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

    def test_the_list_marks_today_and_fades_days_gone_by(self):
        now = timezone.now()
        Event.objects.create(title="Happening today", category=self.category, start=now, created_by=self.creator)
        response = self.client.get(reverse("events:calendar") + "?view=list")
        self.assertContains(response, '<section class="agenda-day today"')
        self.assertContains(response, '<span class="agenda-dow">Today</span>', html=True)
        past = timezone.localtime(self.past_event.start)
        response = self.client.get(reverse("events:calendar"), {"view": "list", "y": past.year, "m": past.month})
        self.assertContains(response, '<section class="agenda-day past"')
        self.assertContains(response, "Bygone gathering")

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
        self.assertNotIn("nav-burger", body)  # no more ☰: phones get a Menu link instead
        self.assertIn('<a class="nav-menu-link" href="/menu/">Menu</a>', body)

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


class AttendeeListTests(EventTestCase):
    def test_the_organiser_can_hide_who_is_going(self):
        url = self.public_event.get_absolute_url()
        member_link = reverse("members:profile", args=[self.member.username])
        self.client.force_login(self.other)
        self.assertContains(self.client.get(url), member_link)
        self.assertContains(self.client.get(reverse("events:calendar")), member_link)
        self.public_event.show_attendees = False
        self.public_event.save()
        page = self.client.get(url)
        self.assertNotContains(page, member_link)
        self.assertContains(page, "chosen not to show")
        self.assertContains(page, "<strong>1</strong> going")  # the count stays
        Event.objects.update(show_attendees=False)  # the member is also going to another fixture event
        self.assertNotContains(self.client.get(reverse("events:calendar")), member_link)  # the calendar's list hides them too
        self.client.force_login(self.creator)  # organisers still see the list
        page = self.client.get(url)
        self.assertContains(page, member_link)
        self.assertContains(page, "you can still see them")
        self.client.force_login(self.admin)  # and so do admins
        self.assertContains(self.client.get(url), member_link)
        self.client.force_login(self.creator)  # weekly copies keep the choice
        self.client.post(reverse("events:repeat", args=[self.public_event.slug]), {"weeks": 2})
        copies = Event.objects.filter(title=self.public_event.title).exclude(pk=self.public_event.pk)
        self.assertEqual(copies.count(), 2)
        self.assertFalse(any(copy.show_attendees for copy in copies))
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse("events:create")), "Show who")
