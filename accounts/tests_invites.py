"""Invite a member: the dashboard box, the email, the link, and the credit
when the invited person joins by either route."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from accounts import invites
from accounts.auth import CrsidBackend
from accounts.models import Invitation, WaitlistRequest

User = get_user_model()


def make_member(username, **extra):
    defaults = dict(
        first_name="Dana", last_name="Member", college="hughes-hall",
        mobile="+44 7700 900001", email=f"{username}@cam.ac.uk", crsid=username,
    )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


@override_settings(SITE_URL="")
class InviteTests(TestCase):
    def setUp(self):
        self.member = make_member("dm999")
        self.client.force_login(self.member)

    def invite(self, email, **kwargs):
        return self.client.post(reverse("accounts:invite"), {"email": email}, **kwargs)

    def test_the_dashboard_has_the_box(self):
        page = self.client.get(reverse("dashboard:home"))
        self.assertContains(page, "Invite a member")
        self.assertContains(page, f'action="{reverse("accounts:invite")}"')

    def test_sending_emails_a_link_of_their_own(self):
        response = self.invite("New.Person@Example.org")
        self.assertRedirects(response, reverse("dashboard:home") + "#invite", fetch_redirect_response=False)
        invitation = Invitation.objects.get()
        self.assertEqual((invitation.inviter, invitation.email), (self.member, "new.person@example.org"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["new.person@example.org"])
        self.assertIn("Dana Member has invited you", message.subject)
        link = "http://testserver" + reverse("accounts:invite_accept", args=[invitation.token])
        self.assertIn(link, message.body)
        html = message.alternatives[0][0]
        self.assertIn(f'href="{link}"', html)
        self.assertNotIn(">http", html)  # the address is never the visible text
        page = self.client.get(reverse("dashboard:home"))
        self.assertContains(page, "<strong>1</strong> invited")

    def test_no_second_email_to_the_same_person_or_to_a_member(self):
        self.invite("new@example.org")
        self.invite("NEW@example.org")
        make_member("ab123")
        self.invite("ab123@cam.ac.uk")
        self.invite("not an address")
        self.assertEqual(Invitation.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_limits(self):
        with mock.patch.object(invites, "DAILY_LIMIT", 2):
            for n in range(3):
                self.invite(f"person{n}@example.org")
        self.assertEqual(Invitation.objects.count(), 2)
        for n in range(invites.PER_ADDRESS_LIMIT):
            Invitation.objects.create(inviter=make_member(f"zz{n}"), email="popular@example.org", token=f"t{n}")
        self.invite("popular@example.org")
        self.assertFalse(Invitation.objects.filter(inviter=self.member, email="popular@example.org").exists())

    def test_a_hidden_member_sends_nothing(self):
        self.member.is_shadow_banned = True
        self.member.save()
        response = self.invite("new@example.org", follow=True)
        self.assertContains(response, "Invitation sent")
        self.assertEqual((Invitation.objects.count(), len(mail.outbox)), (0, 0))

    def test_a_failed_send_leaves_no_invitation(self):
        with mock.patch("accounts.invites.send_mail", side_effect=OSError("smtp down")):
            response = self.invite("new@example.org", follow=True)
        self.assertContains(response, "could not be sent")
        self.assertEqual(Invitation.objects.count(), 0)

    def test_must_be_logged_in(self):
        self.client.logout()
        self.assertEqual(self.invite("new@example.org").status_code, 302)
        self.assertEqual(Invitation.objects.count(), 0)


class JoinTests(TestCase):
    def setUp(self):
        self.member = make_member("dm999")
        self.invitation = Invitation.objects.create(inviter=self.member, email="friend@example.org", token="tok123")
        self.link = reverse("accounts:invite_accept", args=["tok123"])

    def test_the_link_names_the_inviter_and_offers_both_ways_in(self):
        page = self.client.get(self.link)
        self.assertContains(page, "Dana Member has invited you")
        self.assertContains(page, reverse("accounts:raven"))
        self.assertContains(page, reverse("accounts:waitlist"))
        self.assertEqual(self.client.session[invites.SESSION_KEY], "tok123")

    def test_a_wrong_link_still_shows_how_to_join(self):
        page = self.client.get(reverse("accounts:invite_accept", args=["nope"]))
        self.assertContains(page, "Come and join us")
        self.assertNotIn(invites.SESSION_KEY, self.client.session)

    @override_settings(RAVEN_MODE="dev", DEBUG=True)
    def test_a_first_raven_login_through_the_link_credits_the_inviter(self):
        self.client.get(self.link)
        self.client.post(reverse("accounts:dev_login"), {"crsid": "nw456"})
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.joined_user.username, "nw456")
        self.assertIsNotNone(self.invitation.joined_at)
        self.assertEqual(invites.counts_for(self.member), {"sent": 1, "joined": 1})

    def test_the_raven_backend_credits_from_the_session_or_the_address(self):
        request = RequestFactory().get("/")
        request.session = {invites.SESSION_KEY: "tok123"}
        new = User.objects.create(username="nw456")
        CrsidBackend().configure_user(request, new, created=True)
        self.assertEqual(Invitation.objects.get(pk=self.invitation.pk).joined_user, new)
        # no link followed, but the invitation went to their University address
        by_address = Invitation.objects.create(inviter=self.member, email="xy789@cam.ac.uk", token="tok456")
        request.session = {}
        other = User.objects.create(username="xy789")
        CrsidBackend().configure_user(request, other, created=True)
        self.assertEqual(Invitation.objects.get(pk=by_address.pk).joined_user, other)
        # a returning member is never credited to anyone
        again = Invitation.objects.create(inviter=self.member, email="late@example.org", token="tok789")
        request.session = {invites.SESSION_KEY: "tok789"}
        CrsidBackend().configure_user(request, other, created=False)
        self.assertIsNone(Invitation.objects.get(pk=again.pk).joined_user)

    def test_an_approved_waitlist_request_through_the_link_credits_the_inviter(self):
        self.client.get(self.link)
        self.client.post(reverse("accounts:waitlist"), {
            "first_name": "Pat", "last_name": "Partner", "email": "pat@example.org",
            "connection": "Friend of Dana", "accept_terms": "on",
        })
        wreq = WaitlistRequest.objects.get(email="pat@example.org")
        self.assertEqual(wreq.invitation, self.invitation)
        admin = make_member("ad111", is_portal_admin=True)
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("panel:waitlist")), "Invited by Dana Member")
        with mock.patch("accounts.services.send_associate_invite"):
            self.client.post(reverse("panel:waitlist_review", args=[wreq.pk]), {"decision": "approve"})
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.joined_user.email, "pat@example.org")

    def test_nobody_is_credited_twice_or_for_themselves(self):
        new = make_member("nw456")
        self.assertEqual(invites.credit(new, self.invitation), self.invitation)
        second = Invitation.objects.create(inviter=make_member("ot222"), email="nw456@cam.ac.uk", token="tok2")
        self.assertIsNone(invites.credit(new, second))
        own = Invitation.objects.create(inviter=new, email="self@example.org", token="tok3")
        fresh = make_member("fr333")
        fresh_own = Invitation.objects.create(inviter=fresh, email="fr333@cam.ac.uk", token="tok4")
        self.assertIsNone(invites.credit(fresh, fresh_own))
        self.assertIsNone(Invitation.objects.get(pk=own.pk).joined_user)

    def test_the_admin_page_counts_who_brought_in_the_most(self):
        other = make_member("ot222", first_name="Olu", last_name="Other")
        Invitation.objects.create(inviter=other, email="a@example.org", token="a", joined_user=make_member("ja111"))
        Invitation.objects.create(inviter=other, email="b@example.org", token="b", joined_user=make_member("jb222"))
        Invitation.objects.create(inviter=self.member, email="c@example.org", token="c", joined_user=make_member("jc333"))
        board = invites.leaderboard()
        self.assertEqual([(m.username, m.invites_sent, m.invites_joined) for m in board],
                         [("ot222", 2, 2), ("dm999", 2, 1)])
        self.client.force_login(make_member("ad111", is_portal_admin=True))
        page = self.client.get(reverse("panel:invitations"))
        self.assertContains(page, "Olu Other")
        self.assertContains(page, "<strong>3</strong> new members")
        self.client.force_login(self.member)
        self.assertNotEqual(self.client.get(reverse("panel:invitations")).status_code, 200)


@override_settings(SITE_URL="")
class PersonalLinkTests(TestCase):
    """Every member has a link and QR code of their own: nobody's address
    is needed, and whoever joins through it is credited to them."""

    def setUp(self):
        self.member = make_member("dm999")
        self.link = reverse("accounts:invite_accept", args=[self.member.get_invite_code()])

    def test_the_dashboard_and_the_page_show_the_link_and_qr_code(self):
        self.client.force_login(self.member)
        full = "http://testserver" + self.link
        for name in ("dashboard:home", "accounts:invite"):
            page = self.client.get(reverse(name))
            self.assertContains(page, f'value="{full}"')
            self.assertContains(page, 'class="invite-qr"')
            self.assertContains(page, "<svg")
            self.assertContains(page, "https://wa.me/?text=")
        self.assertEqual(self.member.get_invite_code(), User.objects.get(pk=self.member.pk).invite_code)

    def test_the_link_names_the_member(self):
        self.assertContains(self.client.get(self.link), "Dana Member has invited you")

    @override_settings(RAVEN_MODE="dev", DEBUG=True)
    def test_joining_by_raven_through_the_link_credits_the_member(self):
        self.client.get(self.link)
        self.client.post(reverse("accounts:dev_login"), {"crsid": "nw456"})
        invitation = Invitation.objects.get()
        self.assertEqual((invitation.inviter, invitation.joined_user.username, invitation.by_link),
                         (self.member, "nw456", True))
        self.assertEqual(len(mail.outbox), 0)
        # the same link works for the next person too
        self.client.logout()
        self.client.get(self.link)
        self.client.post(reverse("accounts:dev_login"), {"crsid": "ab789"})
        self.assertEqual(invites.counts_for(self.member), {"sent": 2, "joined": 2})

    def test_joining_by_the_waitlist_through_the_link_credits_the_member(self):
        self.client.get(self.link)
        self.client.post(reverse("accounts:waitlist"), {
            "first_name": "Pat", "last_name": "Partner", "email": "pat@example.org",
            "connection": "Friend of Dana", "accept_terms": "on",
        })
        wreq = WaitlistRequest.objects.get(email="pat@example.org")
        self.assertEqual(wreq.invitation.inviter, self.member)
        self.assertEqual(invites.counts_for(self.member), {"sent": 1, "joined": 0})
        self.client.force_login(make_member("ad111", is_portal_admin=True))
        with mock.patch("accounts.services.send_associate_invite"):
            self.client.post(reverse("panel:waitlist_review", args=[wreq.pk]), {"decision": "approve"})
        self.assertEqual(invites.counts_for(self.member), {"sent": 1, "joined": 1})

    def test_people_who_come_by_link_do_not_use_up_the_email_limit(self):
        Invitation.objects.create(inviter=self.member, email="a@example.org", token="a", by_link=True)
        self.client.force_login(self.member)
        with mock.patch.object(invites, "DAILY_LIMIT", 1):
            self.client.post(reverse("accounts:invite"), {"email": "b@example.org", "back": "page"})
        self.assertTrue(Invitation.objects.filter(email="b@example.org", by_link=False).exists())

    def test_a_member_cannot_credit_themselves_and_a_banned_link_is_dead(self):
        self.client.force_login(self.member)
        self.assertRedirects(self.client.get(self.link), reverse("dashboard:home"), fetch_redirect_response=False)
        self.client.logout()
        self.member.is_banned = True
        self.member.save()
        self.assertContains(self.client.get(self.link), "Come and join us")
