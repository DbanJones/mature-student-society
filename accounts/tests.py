"""Focused tests for the accounts app: login gating, dev impersonation
guards, the associate waitlist, the one-time WhatsApp reveal and the
profile-completion middleware.

NOTE: pages render base.html, which reverses URL names owned by the other
apps (events, guide, supper, dashboard, core, panel) — these tests are meant
to run on the integrated project.
"""

import re

from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core.models import SiteConfig

from .models import User, WaitlistRequest, WhatsAppAccessRequest
from .services import send_associate_invite


def make_member(username="tu123", complete=True, **extra):
    """A Raven member; ``complete=False`` leaves the profile unfinished."""
    defaults = dict(
        crsid=username,
        account_type=User.AccountType.RAVEN,
        email=f"{username}@cam.ac.uk",
    )
    if complete:
        defaults.update(
            first_name="Test", last_name="User", college="wolfson",
            mobile="+447700900000",
        )
    defaults.update(extra)
    return User.objects.create_user(username=username, **defaults)


def make_associate(email="assoc@example.com", password="a-strong-pw-9", **extra):
    defaults = dict(
        account_type=User.AccountType.ASSOCIATE,
        first_name="Ann", last_name="Associate", college="other",
        mobile="+447700900111",
    )
    defaults.update(extra)
    return User.objects.create_user(
        username=email, email=email, password=password, **defaults
    )


class WaitlistTests(TestCase):
    def valid_data(self, **overrides):
        data = {
            "first_name": "Pat",
            "last_name": "Partner",
            "email": "pat@example.com",
            "mobile": "07700 900222",
            "connection": "Partner of Test User (Wolfson).",
            "website": "",
        }
        data.update(overrides)
        return data

    def test_waitlist_creates_request(self):
        response = self.client.post(reverse("accounts:waitlist"), self.valid_data())
        self.assertRedirects(
            response, reverse("accounts:waitlist_done"),
            fetch_redirect_response=False,
        )
        req = WaitlistRequest.objects.get(email="pat@example.com")
        self.assertEqual(req.status, WaitlistRequest.Status.PENDING)
        self.assertEqual(req.mobile, "07700900222")  # spaces stripped

    def test_honeypot_pretends_success_without_saving(self):
        response = self.client.post(
            reverse("accounts:waitlist"), self.valid_data(website="spam.example")
        )
        self.assertRedirects(
            response, reverse("accounts:waitlist_done"),
            fetch_redirect_response=False,
        )
        self.assertFalse(WaitlistRequest.objects.exists())

    def test_duplicate_email_is_gentle_not_a_500(self):
        WaitlistRequest.objects.create(
            first_name="Pat", last_name="Partner", email="pat@example.com",
            connection="Partner.",
        )
        response = self.client.post(reverse("accounts:waitlist"), self.valid_data())
        self.assertEqual(response.status_code, 302)  # no IntegrityError trace
        self.assertEqual(WaitlistRequest.objects.count(), 1)

    def test_existing_account_email_gives_uniform_response(self):
        # An email that already has an account must land on the SAME thanks
        # page as any other submission — otherwise the form is a membership
        # enumeration oracle for anonymous visitors. No new request is created.
        make_associate(email="pat@example.com")
        response = self.client.post(reverse("accounts:waitlist"), self.valid_data())
        self.assertRedirects(
            response, reverse("accounts:waitlist_done"), fetch_redirect_response=False
        )
        self.assertFalse(WaitlistRequest.objects.exists())

    def test_cam_email_rejected_on_waitlist(self):
        # Cambridge addresses belong on Raven, not the associate waitlist.
        data = self.valid_data()
        data["email"] = "abc123@cam.ac.uk"
        response = self.client.post(reverse("accounts:waitlist"), data)
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        self.assertFalse(WaitlistRequest.objects.exists())


@override_settings(RAVEN_MODE="dev", DEBUG=True)
class DevImpersonationTests(TestCase):
    def setUp(self):
        self.member = make_member("ab123")

    def test_dev_login_works_in_dev_mode(self):
        response = self.client.post(
            reverse("accounts:dev_login"), {"user_id": self.member.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), self.member.pk
        )

    def test_dev_login_creates_raven_user_from_crsid(self):
        self.client.post(reverse("accounts:dev_login"), {"crsid": "zz999"})
        user = User.objects.get(username="zz999")
        self.assertEqual(user.crsid, "zz999")
        self.assertEqual(user.account_type, User.AccountType.RAVEN)
        self.assertEqual(user.email, "zz999@cam.ac.uk")

    @override_settings(RAVEN_MODE="header")
    def test_404_when_raven_mode_is_not_dev(self):
        response = self.client.post(
            reverse("accounts:dev_login"), {"user_id": self.member.pk}
        )
        self.assertEqual(response.status_code, 404)

    @override_settings(DEBUG=False)
    def test_404_when_debug_is_off_even_in_dev_mode(self):
        response = self.client.post(
            reverse("accounts:dev_login"), {"user_id": self.member.pk}
        )
        self.assertEqual(response.status_code, 404)

    def test_banned_users_cannot_be_impersonated(self):
        admin = make_member("ad111", is_portal_admin=True)
        self.member.ban(admin)
        response = self.client.post(
            reverse("accounts:dev_login"), {"user_id": self.member.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)


class WhatsAppTests(TestCase):
    def setUp(self):
        config = SiteConfig.get()
        config.whatsapp_group_link = "https://chat.whatsapp.com/TEST-LINK"
        config.save()
        self.member = make_member("wa123")
        self.client.force_login(self.member)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("accounts:whatsapp"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_reveal_is_one_time(self):
        url = reverse("accounts:whatsapp")

        # Before reveal: interstitial, no link in the page.
        response = self.client.get(url)
        self.assertContains(response, "Reveal my invite link")
        self.assertNotContains(response, "TEST-LINK")

        # The reveal POST shows the link exactly once.
        response = self.client.post(url, {"action": "reveal"})
        self.assertContains(response, "https://chat.whatsapp.com/TEST-LINK")
        self.member.refresh_from_db()
        self.assertIsNotNone(self.member.whatsapp_link_viewed_at)

        # Second GET: the link is gone; the re-access request form is shown.
        response = self.client.get(url)
        self.assertNotContains(response, "TEST-LINK")
        self.assertContains(response, "Request access again")

        # A second reveal POST must not show the link either.
        response = self.client.post(url, {"action": "reveal"})
        self.assertEqual(response.status_code, 302)

    def test_only_one_open_access_request(self):
        self.member.mark_whatsapp_link_viewed()
        url = reverse("accounts:whatsapp")
        self.client.post(url, {"action": "request", "message": "Lost my phone"})
        self.client.post(url, {"action": "request", "message": "Again"})
        self.assertEqual(
            self.member.whatsapp_requests.filter(
                status=WhatsAppAccessRequest.Status.OPEN
            ).count(),
            1,
        )


class ProfileCompletionTests(TestCase):
    def test_incomplete_profile_is_redirected_to_setup(self):
        user = make_member("in123", complete=False)
        self.client.force_login(user)
        response = self.client.get("/me/")
        self.assertRedirects(
            response, reverse("accounts:profile_setup"),
            fetch_redirect_response=False,
        )

    def test_complete_profile_is_not_redirected(self):
        user = make_member("ok123")
        self.client.force_login(user)
        response = self.client.get("/me/")
        # Anything but the setup redirect (dashboard is another app's view).
        if response.status_code == 302:
            self.assertNotEqual(response.url, reverse("accounts:profile_setup"))

    def test_setup_completes_profile_and_offers_whatsapp(self):
        config = SiteConfig.get()
        config.whatsapp_group_link = "https://chat.whatsapp.com/TEST-LINK"
        config.save()
        user = make_member("nu123", complete=False)
        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:profile_setup"),
            {
                "first_name": "New",
                "last_name": "User",
                "college": "hughes-hall",
                "mobile": "07700 900333",
                "email": "nu123@cam.ac.uk",
            },
        )
        self.assertRedirects(
            response, reverse("accounts:whatsapp"), fetch_redirect_response=False
        )
        user.refresh_from_db()
        self.assertTrue(user.profile_complete)
        self.assertEqual(user.mobile, "07700900333")


class LoginTests(TestCase):
    def test_banned_associate_cannot_log_in_with_password(self):
        admin = make_member("ad123", is_portal_admin=True)
        assoc = make_associate(password="a-strong-pw-9")
        assoc.ban(admin)
        response = self.client.post(
            reverse("accounts:login"),
            {"username": assoc.email, "password": "a-strong-pw-9"},
        )
        self.assertEqual(response.status_code, 200)  # form re-shown, no login
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_associate_can_log_in_with_email_and_password(self):
        assoc = make_associate(password="a-strong-pw-9")
        response = self.client.post(
            reverse("accounts:login"),
            {"username": assoc.email, "password": "a-strong-pw-9"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), assoc.pk)

    @override_settings(RAVEN_MODE="header", DEBUG=False)
    def test_no_impersonation_card_outside_dev(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertNotContains(response, "Development impersonation")


class PageRenderTests(TestCase):
    """Smoke tests: template typos only surface at render time."""

    @override_settings(RAVEN_MODE="dev", DEBUG=True)
    def test_login_page_shows_impersonation_card_in_dev(self):
        make_member("dv123")
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, "Development impersonation")
        self.assertContains(response, "dv123")

    @override_settings(RAVEN_MODE="header")
    def test_raven_error_page_when_header_auth_missing(self):
        response = self.client.get(reverse("accounts:raven"))
        self.assertEqual(response.status_code, 500)
        self.assertContains(
            response, "Raven", status_code=500,
        )

    @override_settings(RAVEN_MODE="dev")
    def test_raven_redirects_to_login_in_dev(self):
        response = self.client.get(reverse("accounts:raven"))
        self.assertRedirects(
            response, reverse("accounts:login"), fetch_redirect_response=False
        )

    def test_waitlist_form_renders_with_honeypot(self):
        response = self.client.get(reverse("accounts:waitlist"))
        self.assertContains(response, "hp-field")

    def test_profile_pages_render(self):
        user = make_member("pr123")
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("accounts:profile")).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("accounts:profile_setup")).status_code, 200
        )

    def test_password_change_is_for_associates_only(self):
        raven_user = make_member("pw123")
        self.client.force_login(raven_user)
        response = self.client.get(reverse("accounts:password_change"))
        self.assertRedirects(
            response, reverse("accounts:profile"), fetch_redirect_response=False
        )
        assoc = make_associate()
        self.client.force_login(assoc)
        response = self.client.get(reverse("accounts:password_change"))
        self.assertEqual(response.status_code, 200)


class AssociateInviteTests(TestCase):
    def test_invite_email_link_sets_password(self):
        assoc = make_associate(email="newbie@example.com", password=None)
        request = RequestFactory().get("/", secure=True)
        url = send_associate_invite(assoc, request)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Your MSS account is approved")
        self.assertEqual(mail.outbox[0].to, ["newbie@example.com"])
        self.assertIn(url, mail.outbox[0].body)

        # Follow the link: Django redirects to the tokenless set-password URL.
        match = re.search(r"https?://testserver(/accounts/set-password/\S+/)", url)
        self.assertIsNotNone(match)
        path = match.group(1)
        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose your password")

        form_url = response.request["PATH_INFO"]
        response = self.client.post(
            form_url,
            {"new_password1": "orange-bicycle-42", "new_password2": "orange-bicycle-42"},
        )
        self.assertRedirects(
            response, reverse("accounts:login"), fetch_redirect_response=False
        )
        assoc.refresh_from_db()
        self.assertTrue(assoc.check_password("orange-bicycle-42"))


class ProfileDetailTests(TestCase):
    """The talk-to-me-about / work / interests profile fields."""

    def _member(self, username, **extra):
        return User.objects.create_user(
            username=username, password="pw", first_name="Test",
            last_name="Member", college="wolfson", mobile="+44 7700 900001",
            email=f"{username}@cam.ac.uk", **extra,
        )

    def test_profile_page_shows_detail_fields(self):
        member = self._member(
            "pd001",
            talk_to_me_about="sourdough and the Civil War",
            work="15 years in logistics",
            interests="hill walking, chess",
        )
        viewer = self._member("pd002")
        self.client.force_login(viewer)
        response = self.client.get(
            reverse("members:profile", args=[member.username])
        )
        self.assertContains(response, "Talk to me about")
        self.assertContains(response, "sourdough and the Civil War")
        self.assertContains(response, "15 years in logistics")
        self.assertContains(response, "hill walking, chess")

    def test_directory_searches_work_and_interests(self):
        self._member("pd003", interests="amateur radio, chess")
        viewer = self._member("pd004", work="ex-barrister")
        self.client.force_login(viewer)
        response = self.client.get(reverse("members:directory"), {"q": "amateur radio"})
        self.assertContains(response, "pd003")
        response = self.client.get(reverse("members:directory"), {"q": "barrister"})
        self.assertContains(response, "pd004")

    def test_own_profile_form_saves_new_fields(self):
        member = self._member("pd005")
        self.client.force_login(member)
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Test", "last_name": "Member", "college": "wolfson",
            "course": "MBA", "bio": "", "talk_to_me_about": "supply chains",
            "work": "Procurement", "interests": "salsa",
            "mobile": "+44 7700 900001", "email": "pd005@cam.ac.uk",
        })
        self.assertEqual(response.status_code, 302)
        member.refresh_from_db()
        self.assertEqual(member.talk_to_me_about, "supply chains")
        self.assertEqual(member.work, "Procurement")
        self.assertEqual(member.interests, "salsa")


class TermsAcceptanceTests(TestCase):
    """The terms gate: who is stopped, what is recorded, and when the gate
    re-arms."""

    def _publish(self, number=1, content="Be kind."):
        from django.utils import timezone

        from core.models import TermsVersion

        return TermsVersion.objects.create(
            number=number, title="Terms and Conditions", content=content,
            is_published=True, published_at=timezone.now(),
        )

    def test_no_gate_while_nothing_is_published(self):
        # The seeded v1 is an unpublished draft, so members roam freely.
        member = make_member()
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def test_member_is_gated_until_they_accept(self):
        self._publish(number=2)
        member = make_member()
        self.client.force_login(member)

        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("accounts:terms"))
        # The public copy and the accept page itself stay reachable.
        self.assertEqual(self.client.get(reverse("core:terms")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:terms")).status_code, 200)

        # No tick, no acceptance.
        self.client.post(reverse("accounts:terms"), {})
        member.refresh_from_db()
        self.assertIsNone(member.terms_accepted_version)

        response = self.client.post(reverse("accounts:terms"), {"accept": "yes"})
        self.assertRedirects(response, reverse("dashboard:home"))
        member.refresh_from_db()
        self.assertEqual(member.terms_accepted_version, 2)
        self.assertIsNotNone(member.terms_accepted_at)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def test_acceptance_row_records_the_evidence(self):
        from core.models import TermsAcceptance

        self._publish(number=2)
        member = make_member()
        self.client.force_login(member)
        self.client.post(
            reverse("accounts:terms"), {"accept": "yes"},
            HTTP_USER_AGENT="TestBrowser/1.0",
            HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1",
        )
        acceptance = TermsAcceptance.objects.get(user=member)
        self.assertEqual(acceptance.version_number, 2)
        self.assertEqual(acceptance.ip_address, "203.0.113.7")
        self.assertEqual(acceptance.user_agent, "TestBrowser/1.0")
        self.assertEqual(acceptance.source, TermsAcceptance.Source.PORTAL)

        # Accepting again does not mint a second row.
        self.client.post(reverse("accounts:terms"), {"accept": "yes"})
        self.assertEqual(TermsAcceptance.objects.filter(user=member).count(), 1)

    def test_new_version_regates_everyone(self):
        self._publish(number=2)
        member = make_member()
        self.client.force_login(member)
        self.client.post(reverse("accounts:terms"), {"accept": "yes"})
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

        self._publish(number=3, content="Be kinder.")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("accounts:terms"))

    def test_waitlist_requires_the_tick_and_stamps_the_version(self):
        self._publish(number=2)
        data = {
            "first_name": "Pat", "last_name": "Partner",
            "email": "pat@example.com", "mobile": "",
            "connection": "Partner of Test User (Wolfson).", "website": "",
        }
        # Without the tick the form re-renders with an error.
        response = self.client.post(reverse("accounts:waitlist"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WaitlistRequest.objects.filter(email="pat@example.com").exists())

        data["accept_terms"] = "on"
        response = self.client.post(reverse("accounts:waitlist"), data)
        self.assertRedirects(response, reverse("accounts:waitlist_done"))
        request = WaitlistRequest.objects.get(email="pat@example.com")
        self.assertEqual(request.terms_version, 2)
        self.assertIsNotNone(request.terms_accepted_at)

    def test_waitlist_needs_no_tick_when_nothing_is_published(self):
        data = {
            "first_name": "Pat", "last_name": "Partner",
            "email": "pat2@example.com", "mobile": "",
            "connection": "Partner of Test User (Wolfson).", "website": "",
        }
        response = self.client.post(reverse("accounts:waitlist"), data)
        self.assertRedirects(response, reverse("accounts:waitlist_done"))
        request = WaitlistRequest.objects.get(email="pat2@example.com")
        self.assertIsNone(request.terms_version)
