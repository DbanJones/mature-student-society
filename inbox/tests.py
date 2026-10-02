"""Direct-message policy tests.

Messaging is restricted by default: ordinary members can write to committee
admins (and reply to them) but not to each other; admins can message anyone;
an admin can switch a member to "enabled" (message anyone) or "muted" (send
nothing); and the site-wide mode can be flipped back to "open".
"""

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.models import SiteConfig

from .models import DirectMessage, MessageBlock
from .policy import can_message, messaging_denied_reason


def make_user(username, **extra):
    defaults = dict(
        first_name="Test", last_name=username.title(), college="wolfson",
        mobile="+447700900000", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


class MessagingPolicyTests(TestCase):
    def setUp(self):
        self.admin = make_user("adm1", is_portal_admin=True)
        self.alice = make_user("ali1")
        self.bob = make_user("bob1")

    def test_restricted_is_the_default(self):
        self.assertEqual(
            SiteConfig.get().messaging_mode, SiteConfig.MessagingMode.RESTRICTED
        )
        self.assertEqual(self.alice.messaging, User.Messaging.DEFAULT)

    def test_members_cannot_message_each_other_when_restricted(self):
        self.assertFalse(can_message(self.alice, self.bob))
        self.assertIn("switched off", messaging_denied_reason(self.alice, self.bob))

    def test_members_can_write_to_admins_and_admins_to_anyone(self):
        self.assertTrue(can_message(self.alice, self.admin))
        self.assertTrue(can_message(self.admin, self.alice))

    def test_an_enabled_member_can_message_anyone(self):
        self.alice.messaging = User.Messaging.ENABLED
        self.alice.save()
        self.assertTrue(can_message(self.alice, self.bob))
        # Bob is still on the default, so he can't start a thread with Alice.
        self.assertFalse(can_message(self.bob, self.alice))

    def test_a_muted_member_cannot_message_anyone_not_even_admins(self):
        self.alice.messaging = User.Messaging.MUTED
        self.alice.save()
        self.assertFalse(can_message(self.alice, self.admin))
        self.assertIn(
            "restricted by the committee",
            messaging_denied_reason(self.alice, self.admin),
        )

    def test_admins_are_never_muted(self):
        self.admin.messaging = User.Messaging.MUTED
        self.admin.save()
        self.assertTrue(can_message(self.admin, self.alice))

    def test_open_mode_lets_members_message_each_other(self):
        config = SiteConfig.get()
        config.messaging_mode = SiteConfig.MessagingMode.OPEN
        config.save()
        self.assertTrue(can_message(self.alice, self.bob))

    def test_a_block_wins_in_both_directions(self):
        MessageBlock.objects.create(user=self.bob, blocked=self.admin)
        self.assertFalse(can_message(self.admin, self.bob))
        self.assertFalse(can_message(self.bob, self.admin))

    def test_nobody_can_message_themselves(self):
        self.assertFalse(can_message(self.alice, self.alice))


class MessagingViewTests(TestCase):
    def setUp(self):
        self.admin = make_user("adm1", is_portal_admin=True)
        self.alice = make_user("ali1")
        self.bob = make_user("bob1")

    def test_thread_refuses_a_member_to_member_send_and_explains_why(self):
        self.client.force_login(self.alice)
        url = reverse("inbox:thread", args=[self.bob.username])
        response = self.client.get(url)
        self.assertContains(response, "switched off")
        self.assertNotContains(response, 'name="body"')
        self.client.post(url, {"body": "hi"})
        self.assertFalse(DirectMessage.objects.exists())

    def test_member_can_write_to_an_admin_and_the_admin_can_reply(self):
        self.client.force_login(self.alice)
        self.client.post(
            reverse("inbox:thread", args=[self.admin.username]),
            {"body": "hello committee"},
        )
        self.assertEqual(DirectMessage.objects.count(), 1)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("inbox:thread", args=[self.alice.username]),
            {"body": "hello back"},
        )
        self.assertEqual(DirectMessage.objects.count(), 2)

    def test_profile_hides_the_message_button_when_sending_is_off(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("members:profile", args=[self.bob.username]))
        self.assertNotContains(response, reverse("inbox:thread", args=[self.bob.username]))
        response = self.client.get(reverse("members:profile", args=[self.admin.username]))
        self.assertContains(response, reverse("inbox:thread", args=[self.admin.username]))

    def test_inbox_lists_the_committee_for_restricted_members(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("inbox:inbox"))
        self.assertContains(response, "Message the committee")
        self.assertContains(response, reverse("inbox:thread", args=[self.admin.username]))

    def test_inbox_shows_no_committee_card_in_open_mode(self):
        config = SiteConfig.get()
        config.messaging_mode = SiteConfig.MessagingMode.OPEN
        config.save()
        self.client.force_login(self.alice)
        response = self.client.get(reverse("inbox:inbox"))
        self.assertNotContains(response, "Message the committee")


class HostMessagingTests(TestCase):
    """Attendees and the host of an upcoming event can always talk."""

    def setUp(self):
        import datetime

        from events.models import RSVP, Category, Event

        self.host = make_user("host1")
        self.alice = make_user("ali1")
        self.bob = make_user("bob1")
        category = Category.objects.create(name="Pub", slug="pub")
        self.event = Event.objects.create(
            title="Quiz", category=category, created_by=self.host,
            start=timezone_now() + datetime.timedelta(days=3),
        )
        RSVP.objects.create(event=self.event, user=self.alice)

    def test_attendee_and_host_can_message_each_other_but_not_bystanders(self):
        self.assertTrue(can_message(self.alice, self.host))
        self.assertTrue(can_message(self.host, self.alice))
        self.assertFalse(can_message(self.bob, self.host))
        self.assertFalse(can_message(self.alice, self.bob))

    def test_event_page_offers_message_the_host(self):
        self.client.force_login(self.alice)
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, "Message the host")
        self.client.force_login(self.bob)
        self.assertNotContains(self.client.get(self.event.get_absolute_url()), "Message the host")

    def test_cancelled_or_past_events_do_not_count(self):
        self.event.is_cancelled = True
        self.event.save()
        self.assertFalse(can_message(self.alice, self.host))


def timezone_now():
    from django.utils import timezone
    return timezone.now()
