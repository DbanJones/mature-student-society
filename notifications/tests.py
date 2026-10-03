"""Notifications: the bell, following one, and the hooks that create them."""

import datetime

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import SitePage
from events.models import RSVP, Category, Event
from testimonials.models import Testimonial

from .models import Notification
from .services import notify


def make_user(username, **extra):
    defaults = dict(
        first_name="Test", last_name=username.title(), college="wolfson",
        mobile="+447700900000", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class NotificationTests(TestCase):
    def setUp(self):
        self.host = make_user("host1")
        self.alice = make_user("ali1")
        self.admin = make_user("adm1", is_portal_admin=True)
        self.category = Category.objects.create(name="Pub", slug="pub")
        self.event = Event.objects.create(
            title="Quiz", category=self.category, created_by=self.host, host=self.host,
            start=timezone.now() + datetime.timedelta(days=5),
        )
        RSVP.objects.create(event=self.event, user=self.alice)

    def test_bell_counts_unread_and_following_marks_read(self):
        notify([self.alice], Notification.Kind.GENERAL, "Hello", "/events/")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, '🔔<span class="nav-badge">1</span>')
        row = Notification.objects.get()
        response = self.client.get(reverse("notifications:go", args=[row.pk]))
        self.assertRedirects(response, "/events/", fetch_redirect_response=False)
        row.refresh_from_db()
        self.assertTrue(row.is_read)
        self.assertNotContains(self.client.get(reverse("dashboard:home")), '🔔<span class="nav-badge">')

    def test_mark_all_read_and_other_peoples_rows_are_hidden(self):
        notify([self.alice, self.host], Notification.Kind.GENERAL, "Hi", "")
        self.client.force_login(self.alice)
        self.client.post(reverse("notifications:mark_all_read"))
        self.assertEqual(Notification.unread_count_for(self.alice), 0)
        self.assertEqual(Notification.unread_count_for(self.host), 1)
        theirs = Notification.objects.get(recipient=self.host)
        self.assertEqual(self.client.get(reverse("notifications:go", args=[theirs.pk])).status_code, 404)

    def test_notify_skips_banned_and_excluded_and_emails_once(self):
        banned = make_user("ban1", is_banned=True)
        mail.outbox = []
        rows = notify(
            [self.alice, self.host, banned, self.alice], Notification.Kind.EVENT,
            "Something", "/x/", email_subject="Something", exclude=[self.host],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].bcc, ["ali1@cam.ac.uk"])

    def test_cancelling_an_event_tells_attendees_in_app_and_by_email(self):
        mail.outbox = []
        self.client.force_login(self.host)
        self.client.post(reverse("events:cancel", args=[self.event.slug]))
        row = Notification.objects.get(recipient=self.alice)
        self.assertIn("cancelled", row.text)
        self.assertEqual(row.kind, "event")
        self.assertFalse(Notification.objects.filter(recipient=self.host).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Cancelled", mail.outbox[0].subject)

    def test_moving_an_event_tells_attendees(self):
        self.client.force_login(self.host)
        new_start = timezone.localtime(self.event.start + datetime.timedelta(days=1)).replace(
            second=0, microsecond=0
        )
        self.client.post(reverse("events:edit", args=[self.event.slug]), {
            "title": "Quiz", "category": self.category.pk, "description": "",
            "location": "", "start": new_start.strftime("%Y-%m-%dT%H:%M"), "end": "",
            "host": self.host.pk, "capacity": "", "group_chat_link": "",
            "attendee_info": "", "restaurant": "", "new_restaurant": "",
        })
        self.event.refresh_from_db()
        self.assertEqual(timezone.localtime(self.event.start), new_start)
        self.assertTrue(Notification.objects.filter(recipient=self.alice, text__contains="moved").exists())

    def test_testimonial_decision_and_page_edit_hooks(self):
        testimonial = Testimonial.objects.create(author=self.alice, author_name="A", body="Great.")
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:testimonial_action", args=[testimonial.pk]), {"action": "approve"})
        self.assertTrue(Notification.objects.filter(recipient=self.alice, kind="testimonial").exists())

        page = SitePage.objects.create(title="Handbook", slug="handbook", content="x")
        page.editors.add(self.alice, self.host)
        self.client.force_login(self.host)
        self.client.post(reverse("core:site_page_edit", args=["handbook"]), {"title": "Handbook", "content": "y"})
        self.assertTrue(Notification.objects.filter(recipient=self.alice, kind="page").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.host, kind="page").exists())
