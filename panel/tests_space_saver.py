"""The space saver: pictures of events over a week ago, and pictures no
event uses, go when an admin says; nothing else ever does."""

import datetime
import os
import tempfile
import time

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import Category, Event
from panel.models import AuditLog


def picture(name):
    return SimpleUploadedFile(name, b"x" * 2048, content_type="image/jpeg")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SpaceSaverTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        moved = override_settings(MEDIA_ROOT=media.name)
        moved.enable()
        self.addCleanup(moved.disable)
        self.media = media.name
        self.admin = User.objects.create_user(username="ss001", password="pw", email="ss001@cam.ac.uk",
                                              first_name="Sam", last_name="Saver", college="darwin",
                                              mobile="+44 7700 900100", is_portal_admin=True)
        self.client.force_login(self.admin)
        category = Category.objects.create(name="Socials", slug="socials")
        now = timezone.now()

        def event(title, start, end=None, image=True):
            return Event.objects.create(title=title, category=category, start=start, end=end, created_by=self.admin,
                                        image=picture(f"{title}.jpg") if image else "")

        self.old = event("old", now - datetime.timedelta(days=10), now - datetime.timedelta(days=10, hours=-2))
        self.no_end = event("noend", now - datetime.timedelta(days=9))
        self.recent = event("recent", now - datetime.timedelta(days=3), now - datetime.timedelta(days=3, hours=-2))
        self.coming = event("coming", now + datetime.timedelta(days=5))
        self.old_path, self.recent_path = self.old.image.path, self.recent.image.path
        events = os.path.join(self.media, "events")
        self.leftover = os.path.join(events, "replaced-long-ago.jpg")
        self.fresh = os.path.join(events, "being-uploaded.jpg")
        for path in (self.leftover, self.fresh):
            with open(path, "wb") as handle:
                handle.write(b"y" * 1024)
        long_ago = time.time() - 3 * 86400
        os.utime(self.leftover, (long_ago, long_ago))
        os.makedirs(os.path.join(self.media, "profiles"))
        self.photo = os.path.join(self.media, "profiles", "member.jpg")
        with open(self.photo, "wb") as handle:
            handle.write(b"z")
        os.utime(self.photo, (long_ago, long_ago))

    def test_the_page_shows_what_could_go_and_deletes_nothing(self):
        page = self.client.get(reverse("panel:space_saver"))
        self.assertEqual([event.title for event, _size in page.context["events"]], ["noend", "old"])
        self.assertEqual([name for name, _size in page.context["unused"]], [os.path.join("events", "replaced-long-ago.jpg")])
        self.assertEqual(page.context["free_bytes"], 2 * 2048 + 1024)
        self.assertContains(page, "Free up 5.0")
        self.assertTrue(os.path.exists(self.old_path))
        self.assertTrue(os.path.exists(self.leftover))

    def test_freeing_up_keeps_the_events_and_everything_recent(self):
        response = self.client.post(reverse("panel:space_saver"), follow=True)
        self.assertContains(response, "2 pictures of past events and 1 unused file")
        self.old.refresh_from_db()
        self.assertEqual(self.old.image.name, "")
        self.assertTrue(Event.objects.filter(pk=self.old.pk).exists())  # the event stays
        self.assertFalse(os.path.exists(self.old_path))
        self.assertFalse(os.path.exists(self.leftover))
        for kept in (self.recent_path, self.coming.image.path, self.fresh, self.photo):
            self.assertTrue(os.path.exists(kept), kept)
        self.assertTrue(AuditLog.objects.filter(action="space_saver").exists())
        again = self.client.post(reverse("panel:space_saver"), follow=True)
        self.assertContains(again, "Nothing to clear.")

    def test_only_admins(self):
        member = User.objects.create_user(username="ss002", password="pw", email="ss002@cam.ac.uk", first_name="Mo",
                                          last_name="Member", college="darwin", mobile="+44 7700 900101")
        self.client.force_login(member)
        self.assertEqual(self.client.post(reverse("panel:space_saver")).status_code, 403)
        self.assertTrue(os.path.exists(self.old_path))
