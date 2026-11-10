"""Lunch swaps: a member invites a few others to lunch at their college."""
import datetime
import importlib

from django.apps import apps as django_apps
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events import lunch, services
from events.models import LUNCH_SWAP_SLUG, RSVP, Category, Event
from notifications.models import Notification, NotificationEmail
from panel.services import whats_on_events

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


def member(username, first, college="wolfson", n=1, **extra):
    """A member with a finished profile and an email address."""
    return User.objects.create_user(
        username=username, email=f"{username}@example.com",
        first_name=first, last_name="Member", college=college,
        mobile=f"0770090010{n}", **extra,
    )


class LunchSwapTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = member("lunch-host", "Hattie", n=1)
        cls.guest = member("lunch-guest", "Gus", college="darwin", n=2)
        cls.other = member("lunch-other", "Olive", college="kings", n=3)
        cls.pub = Category.objects.create(name="Pub Nights", slug="pub-nights")

    def setUp(self):
        # Never assume the migration's row is there: put it back if it is not.
        self.tag = lunch.lunch_swap_tag()

    def make_lunch(self, host=None, days=3, places=2):
        host = host or self.host
        day = timezone.localdate() + datetime.timedelta(days=days)
        start = timezone.make_aware(datetime.datetime.combine(day, datetime.time(12, 30)))
        return lunch.create_lunch(host, {
            "college": "wolfson", "start": start, "places": places,
            "meet": "Porters' Lodge", "paying": "own", "note": "",
        })

    def form_day(self, days=3):
        return timezone.localdate() + datetime.timedelta(days=days)

    def post_form(self, **overrides):
        data = {
            "college": "wolfson", "date": self.form_day().isoformat(), "time": "12:30",
            "places": "2", "meet": "Porters' Lodge", "paying": "own", "note": "",
        }
        data.update(overrides)
        return self.client.post(reverse("events:lunch_new"), data)

    def move_into_the_past(self, event, days=1):
        start = timezone.now() - datetime.timedelta(days=days)
        Event.objects.filter(pk=event.pk).update(
            start=start, end=start + datetime.timedelta(hours=1),
        )
        event.refresh_from_db()
        return event

    def lunches(self):
        return Event.objects.filter(category__slug=LUNCH_SWAP_SLUG)

    def notices(self, user):
        return list(Notification.objects.filter(recipient=user).order_by("pk"))

    def page(self, event, user=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse("events:detail", args=[event.slug]))


class LunchTagTests(LunchSwapTestCase):
    def test_the_migration_makes_the_tag_once(self):
        add_tag = importlib.import_module("events.migrations.0008_lunch_swap_tag").add_tag
        Category.objects.filter(slug=LUNCH_SWAP_SLUG).delete()

        add_tag(django_apps, None)
        tag = Category.objects.get(slug=LUNCH_SWAP_SLUG)
        self.assertEqual(tag.name, "Lunch Swap")
        self.assertEqual(tag.emoji, "🥪")
        self.assertEqual(tag.color, "#8a6d0b")
        self.assertIn("lunch at your college", tag.description)
        self.assertIn("How a lunch swap works", tag.page_content)

        add_tag(django_apps, None)
        self.assertEqual(Category.objects.filter(slug=LUNCH_SWAP_SLUG).count(), 1)

        Category.objects.filter(slug=LUNCH_SWAP_SLUG).delete()
        by_hand = Category.objects.create(name="lunch swap", slug="x")
        add_tag(django_apps, None)
        by_hand.refresh_from_db()
        self.assertEqual(by_hand.slug, LUNCH_SWAP_SLUG)
        self.assertEqual(Category.objects.filter(name__iexact="lunch swap").count(), 1)

    def test_the_form_puts_a_missing_tag_back(self):
        Category.objects.filter(slug=LUNCH_SWAP_SLUG).delete()
        tag = lunch.lunch_swap_tag()
        self.assertEqual(tag.slug, LUNCH_SWAP_SLUG)
        self.assertEqual(tag.name, "Lunch Swap")
        self.assertIn("How a lunch swap works", tag.page_content)

        tag.delete()
        by_hand = Category.objects.create(name="LUNCH SWAP", slug="y")
        self.assertEqual(lunch.lunch_swap_tag().pk, by_hand.pk)
        by_hand.refresh_from_db()
        self.assertEqual(by_hand.slug, LUNCH_SWAP_SLUG)
        self.assertEqual(Category.objects.filter(name__iexact="lunch swap").count(), 1)

    def test_college_names_read_naturally(self):
        self.assertEqual(lunch.college_place("trinity-hall"), "Trinity Hall")
        self.assertEqual(lunch.college_place("peterhouse"), "Peterhouse")
        self.assertEqual(lunch.college_place("kings"), "King's College")
        self.assertEqual(lunch.college_place("wolfson"), "Wolfson College")


class LunchFormTests(LunchSwapTestCase):
    def test_the_form_needs_a_login(self):
        response = self.client.get(reverse("events:lunch_new"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_the_form_starts_from_my_college(self):
        self.client.force_login(self.host)
        response = self.client.get(reverse("events:lunch_new"))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form["college"].value(), "wolfson")
        self.assertEqual(form["meet"].value(), "Porters' Lodge")
        self.assertEqual(form["time"].value(), "12:30")
        self.assertEqual(form["places"].value(), 2)
        self.assertContains(response, f'min="{timezone.localdate().isoformat()}"')

        elsewhere = member("lunch-elsewhere", "Erin", college="other", n=4)
        self.client.force_login(elsewhere)
        response = self.client.get(reverse("events:lunch_new"))
        self.assertFalse(response.context["form"]["college"].value())

    def test_posting_makes_an_ordinary_members_only_event(self):
        self.client.force_login(self.host)
        response = self.post_form(places="3", note="About six pounds, card only.")
        event = self.lunches().get()
        # Not fetched here: the page is read below, with its one-off message.
        self.assertRedirects(
            response, event.get_absolute_url(), fetch_redirect_response=False
        )

        self.assertEqual(event.title, "Lunch at Wolfson with Hattie")
        self.assertEqual(event.category.slug, LUNCH_SWAP_SLUG)
        self.assertTrue(event.members_only)
        self.assertTrue(event.show_attendees)
        self.assertEqual(event.capacity, 3)
        self.assertEqual(event.location, "Porters' Lodge, Wolfson College")
        start = timezone.make_aware(
            datetime.datetime.combine(self.form_day(), datetime.time(12, 30))
        )
        self.assertEqual(event.start, start)
        self.assertEqual(event.end, start + datetime.timedelta(hours=1))
        self.assertEqual(event.created_by, self.host)
        self.assertEqual(event.host, self.host)
        self.assertIn("Everyone pays for their own lunch.", event.description)
        self.assertIn("About six pounds, card only.", event.description)
        self.assertFalse(event.is_formal)
        self.assertTrue(event.is_lunch_swap)

        page = self.client.get(event.get_absolute_url())
        self.assertContains(page, "Your lunch is on the calendar")
        self.assertContains(page, "Lunch at Wolfson with Hattie")

    def test_a_time_that_has_passed_is_refused(self):
        self.client.force_login(self.host)
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        response = self.post_form(date=yesterday.isoformat())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That time has already passed")
        self.assertFalse(self.lunches().exists())

    def test_a_refused_form_keeps_the_date(self):
        self.client.force_login(self.host)
        day = self.form_day(5).isoformat()
        response = self.post_form(college="", date=day)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{day}"')
        self.assertContains(response, "Choose the college where you will host.")
        self.assertFalse(self.lunches().exists())

    def test_no_more_than_six_places(self):
        self.client.force_login(self.host)
        response = self.post_form(places="7")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())
        self.assertFalse(self.lunches().exists())

    def test_a_very_long_name_still_fits(self):
        long_name = member("lunch-long", "A" * 150, n=5)
        self.client.force_login(long_name)
        response = self.post_form()
        self.assertEqual(response.status_code, 302)
        event = self.lunches().get()
        self.assertLessEqual(len(event.title), 140)

    def test_a_second_press_does_not_make_a_second_lunch(self):
        self.client.force_login(self.host)
        first = self.post_form()
        second = self.post_form()
        event = self.lunches().get()
        self.assertEqual(first["Location"], event.get_absolute_url())
        self.assertEqual(second["Location"], event.get_absolute_url())

    def test_ways_in(self):
        new_url = reverse("events:lunch_new")
        self.client.force_login(self.guest)
        add_page = self.client.get(reverse("events:create"))
        self.assertContains(add_page, f'href="{new_url}"')
        self.assertContains(add_page, "Host a lunch swap")
        tag_page = self.client.get(reverse("events:tag_page", args=[LUNCH_SWAP_SLUG]))
        self.assertContains(tag_page, f'href="{new_url}"')
        self.assertContains(tag_page, "Host a lunch")
        self.assertContains(self.client.get(reverse("core:menu")), f'href="{new_url}"')

        self.client.logout()
        self.assertNotContains(self.client.get(reverse("core:menu")), new_url)


class LunchPrivacyTests(LunchSwapTestCase):
    def test_visitors_cannot_see_a_lunch(self):
        event = self.make_lunch()
        for name, args in (
            ("events:detail", [event.slug]),
            ("events:detail_pk", [event.pk]),
            ("events:ics", [event.slug]),
        ):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404)

        local = timezone.localtime(event.start)
        tag_url = reverse("events:tag_page", args=[LUNCH_SWAP_SLUG])
        for url, query in (
            (reverse("core:home"), {}),
            (reverse("events:calendar"), {"y": local.year, "m": local.month}),
            (reverse("core:search"), {"q": "Lunch"}),
            (tag_url, {}),
        ):
            with self.subTest(url=url):
                response = self.client.get(url, query)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, event.title)
        self.assertContains(self.client.get(tag_url), "Member login to see and host lunches")

    def test_a_lunch_can_never_be_made_public(self):
        event = self.make_lunch()
        self.client.force_login(self.host)
        response = self.client.post(reverse("events:edit", args=[event.slug]), {
            "title": event.title, "category": str(self.tag.pk),
            "description": event.description, "location": event.location,
            "start": timezone.localtime(event.start).strftime(DATETIME_LOCAL),
            "end": timezone.localtime(event.end).strftime(DATETIME_LOCAL),
            "capacity": "2", "show_attendees": "on",
        })
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertTrue(event.members_only)

        made_by_hand = Event.objects.create(
            title="Lunch made another way", category=self.tag, members_only=False,
            start=timezone.now() + datetime.timedelta(days=4), created_by=self.host,
        )
        made_by_hand.refresh_from_db()
        self.assertTrue(made_by_hand.members_only)

        pub = Event.objects.create(
            title="Open pub night", category=self.pub, members_only=False,
            start=timezone.now() + datetime.timedelta(days=4), created_by=self.host,
        )
        pub.refresh_from_db()
        self.assertFalse(pub.members_only)
        pub.category = self.tag
        pub.save(update_fields=["category", "updated_at"])
        pub.refresh_from_db()
        self.assertTrue(pub.members_only)

        still_public = Event.objects.create(
            title="Another open night", category=self.pub, members_only=False,
            start=timezone.now() + datetime.timedelta(days=5), created_by=self.host,
        )
        still_public.save()
        still_public.refresh_from_db()
        self.assertFalse(still_public.members_only)

    def test_the_ordinary_form_edits_a_lunch(self):
        event = self.make_lunch()
        self.client.force_login(self.host)
        url = reverse("events:edit", args=[event.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.tag, response.context["form"].fields["category"].queryset)

        response = self.client.post(url, {
            "title": event.title, "category": str(self.tag.pk),
            "description": event.description, "location": event.location,
            "start": timezone.localtime(event.start).strftime(DATETIME_LOCAL),
            "end": timezone.localtime(event.end).strftime(DATETIME_LOCAL),
            "capacity": "4", "members_only": "on", "show_attendees": "on",
        })
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.capacity, 4)
        self.assertTrue(event.is_lunch_swap)

    def test_a_lunch_is_an_ordinary_event_everywhere(self):
        event = self.make_lunch()
        local = timezone.localtime(event.start)
        self.client.force_login(self.host)
        calendar = self.client.get(
            reverse("events:calendar"), {"y": local.year, "m": local.month}
        )
        self.assertContains(calendar, event.title)

        duplicate = self.client.get(reverse("events:duplicate", args=[event.slug]))
        self.assertEqual(duplicate.status_code, 200)

        self.client.post(reverse("events:repeat", args=[event.slug]), {"weeks": "2"})
        copies = self.lunches().exclude(pk=event.pk)
        self.assertEqual(copies.count(), 2)
        for copy in copies:
            self.assertTrue(copy.is_lunch_swap)
            self.assertTrue(copy.members_only)

        export = self.client.get(reverse("events:export", args=[event.slug]))
        self.assertEqual(export.status_code, 200)
        self.assertIn(event, list(whats_on_events()))


class LunchNoticeTests(LunchSwapTestCase):
    def test_taking_a_place_tells_the_host(self):
        event = self.make_lunch()
        services.set_rsvp(self.guest, event.pk, "join")

        notice, = self.notices(self.host)
        self.assertEqual(notice.kind, Notification.Kind.EVENT)
        self.assertIn("Gus Member is coming to your lunch", notice.text)
        self.assertIn("1 of 2 places taken", notice.text)
        self.assertEqual(notice.url, event.get_absolute_url())
        email = NotificationEmail.objects.get(recipient=self.host)
        self.assertTrue(email.subject.startswith("Gus Member"))
        self.assertIn(event.title, email.body)
        self.assertIn(event.get_absolute_url(), email.body)
        self.assertEqual(self.notices(self.guest), [])

    def test_dropping_out_tells_the_host_and_the_next_in_line(self):
        event = self.make_lunch(places=1)
        services.set_rsvp(self.guest, event.pk, "join")
        waiting = services.set_rsvp(self.other, event.pk, "join")
        self.assertEqual(waiting.rsvp.status, RSVP.Status.WAITING)

        services.set_rsvp(self.guest, event.pk, "cancel")
        texts = [notice.text for notice in self.notices(self.host)]
        self.assertEqual(len(texts), 3)
        self.assertIn("Gus Member is coming", texts[0])
        self.assertIn("Gus Member can no longer come", texts[1])
        self.assertIn("Olive Member is coming", texts[2])
        self.assertIn("1 of 1 places taken", texts[2])
        moved_up, = self.notices(self.other)
        self.assertEqual(moved_up.kind, Notification.Kind.WAITLIST)
        self.assertIn("A place opened up", moved_up.text)

    def test_the_waiting_list_does_not_bother_the_host(self):
        event = self.make_lunch(places=1)
        services.set_rsvp(self.guest, event.pk, "join")
        before = len(self.notices(self.host))
        services.set_rsvp(self.other, event.pk, "join")
        services.set_rsvp(self.other, event.pk, "cancel")
        self.assertEqual(len(self.notices(self.host)), before)

    def test_ordinary_events_stay_quiet(self):
        pub = Event.objects.create(
            title="Quiet pub night", category=self.pub, created_by=self.host,
            start=timezone.now() + datetime.timedelta(days=3), capacity=5,
        )
        services.set_rsvp(self.guest, pub.pk, "join")
        services.set_rsvp(self.guest, pub.pk, "cancel")
        self.assertEqual(self.notices(self.host), [])
        self.assertFalse(NotificationEmail.objects.exists())

    def test_a_flurry_of_changes_stops_emailing_the_host(self):
        event = self.make_lunch()
        for _ in range(4):
            services.set_rsvp(self.guest, event.pk, "join")
            services.set_rsvp(self.guest, event.pk, "cancel")
        self.assertEqual(len(self.notices(self.host)), 8)
        self.assertEqual(NotificationEmail.objects.filter(recipient=self.host).count(), 6)

    def test_a_hidden_member_does_not_notify_the_host(self):
        hidden = member("lunch-hidden", "Hidden", n=6, is_shadow_banned=True)
        event = self.make_lunch()
        result = services.set_rsvp(hidden, event.pk, "join")
        self.assertEqual(result.rsvp.status, RSVP.Status.GOING)
        self.assertEqual(self.notices(self.host), [])
        self.assertFalse(NotificationEmail.objects.exists())


class LunchPageTests(LunchSwapTestCase):
    def test_the_host_cannot_take_a_guest_place(self):
        event = self.make_lunch()
        self.client.force_login(self.host)
        response = self.client.post(
            reverse("events:rsvp", args=[event.slug]), {"action": "join"}, follow=True,
        )
        self.assertFalse(RSVP.objects.filter(event=event).exists())
        shown = [str(message) for message in response.context["messages"]]
        self.assertEqual(shown, ["You're hosting this lunch, so you don't need a place."])

        own_page = self.page(event)
        self.assertContains(own_page, "You're hosting, so you don't need a place")
        self.assertNotContains(own_page, "I'm going</button>")
        self.assertContains(self.page(event, self.guest), "I'm going</button>")

    def test_a_host_who_holds_a_place_can_give_it_back(self):
        event = Event.objects.create(
            title="Started as something else", category=self.pub, created_by=self.host,
            start=timezone.now() + datetime.timedelta(days=3), capacity=3,
        )
        services.set_rsvp(self.host, event.pk, "join")
        event.category = self.tag
        event.save()

        own_page = self.page(event, self.host)
        self.assertContains(own_page, "Can't make it any more")
        self.assertNotContains(own_page, "You're hosting, so you don't need a place")
        self.client.post(reverse("events:rsvp", args=[event.slug]), {"action": "cancel"})
        self.assertEqual(
            RSVP.objects.get(event=event, user=self.host).status, RSVP.Status.CANCELLED
        )

    def test_host_one_back_shows_only_to_guests_after_the_lunch(self):
        event = self.make_lunch()
        services.set_rsvp(self.guest, event.pk, "join")
        back_link = f"{reverse('events:lunch_new')}?back={event.slug}"

        guest_page = self.page(event, self.guest)
        self.assertContains(guest_page, "This is a lunch swap.")
        self.assertNotContains(guest_page, "Host one back")
        host_page = self.page(event, self.host)
        self.assertContains(host_page, "You're hosting this lunch.")
        self.assertNotContains(host_page, "Host one back")
        self.assertNotContains(self.page(event, self.other), "Host one back")

        self.move_into_the_past(event)
        guest_page = self.page(event, self.guest)
        self.assertContains(guest_page, "Host one back")
        self.assertContains(guest_page, back_link)
        self.assertNotContains(self.page(event, self.other), "Host one back")
        self.assertNotContains(self.page(event, self.host), "Host one back")

    def test_hosting_one_back_tells_the_first_host_once(self):
        first = self.make_lunch()
        services.set_rsvp(self.guest, first.pk, "join")
        self.move_into_the_past(first)
        Notification.objects.all().delete()

        self.client.force_login(self.guest)
        form = self.client.get(reverse("events:lunch_new"), {"back": first.slug})
        self.assertEqual(form.context["back"], first)
        self.post_form(college="darwin", back=first.slug)
        returned = self.lunches().get(created_by=self.guest)
        invited = [n for n in self.notices(self.host) if "has invited you back" in n.text]
        self.assertEqual(len(invited), 1)
        self.assertIn("Gus has invited you back", invited[0].text)
        self.assertEqual(invited[0].url, returned.get_absolute_url())

        self.post_form(college="darwin", back=first.slug, date=self.form_day(6).isoformat())
        self.assertEqual(self.lunches().filter(created_by=self.guest).count(), 2)
        invited = [n for n in self.notices(self.host) if "has invited you back" in n.text]
        self.assertEqual(len(invited), 1)

        # Somebody who was not at the lunch cannot use its link to send a notice.
        self.client.force_login(self.other)
        self.post_form(college="kings", back=first.slug)
        self.assertTrue(self.lunches().filter(created_by=self.other).exists())
        invited = [n for n in self.notices(self.host) if "has invited you back" in n.text]
        self.assertEqual(len(invited), 1)
