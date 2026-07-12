"""Panel app tests: permission gating, the waitlist approval flow, member
management rules and the What's On mailer.

The suite runs against a self-contained URLconf (this module doubles as one)
so it does not depend on the other apps' URL files, which are developed in
parallel. Stub routes provide every URL name that base.html references.
"""

import datetime
import sys
import types
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from accounts.models import WaitlistRequest, WhatsAppAccessRequest
from events.models import Category, Event
from panel import services
from panel.models import AuditLog, MailLog

User = get_user_model()

# The panel calls accounts.services.send_associate_invite (built in parallel
# by the accounts app). Provide a stand-in module if it has not landed yet so
# these tests stay runnable in isolation; mock.patch targets it either way.
try:
    from accounts import services as _accounts_services  # noqa: F401
except ImportError:  # pragma: no cover - only pre-integration
    _stub_module = types.ModuleType("accounts.services")

    def _stub_invite(user, request):
        mail.EmailMessage("Set your password", "stub invite", to=[user.email]).send()

    _stub_module.send_associate_invite = _stub_invite
    sys.modules["accounts.services"] = _stub_module


def _stub_view(request, *args, **kwargs):
    return HttpResponse("stub")


def _ns(app_name, routes):
    return include(
        ([path(route, _stub_view, name=name) for route, name in routes], app_name)
    )


urlpatterns = [
    path("admin/", include("panel.urls")),
    path("", _ns("core", [
        ("", "home"), ("about/", "about"),
        ("wellbeing/", "wellbeing"), ("policies/", "policies"),
    ])),
    path("accounts/", _ns("accounts", [
        ("login/", "login"), ("logout/", "logout"), ("waitlist/", "waitlist"),
        ("profile-setup/", "profile_setup"),
    ])),
    path("events/", _ns("events", [("", "calendar"), ("<int:pk>/", "detail")])),
    path("guide/", _ns("guide", [("", "index")])),
    path("supper-club/", _ns("supper", [("", "index")])),
    path("me/", _ns("dashboard", [("", "home")])),
]


@override_settings(ROOT_URLCONF="panel.tests")
class PanelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="adm1", password="pw", email="adm1@cam.ac.uk", crsid="adm1",
            first_name="Ada", last_name="Admin", college="wolfson",
            mobile="+44 7700 900000", is_portal_admin=True,
        )
        cls.member = User.objects.create_user(
            username="mem1", password="pw", email="mem1@cam.ac.uk", crsid="mem1",
            first_name="Mia", last_name="Member", college="darwin",
            mobile="+44 7700 900001",
        )

    @staticmethod
    def all_panel_urls():
        """Every panel URL; pk-taking routes get an arbitrary pk because the
        admin gate fires before any object lookup."""
        return [
            reverse("panel:home"),
            reverse("panel:waitlist"),
            reverse("panel:members"),
            reverse("panel:whatsapp_requests"),
            reverse("panel:stats"),
            reverse("panel:mailer"),
            reverse("panel:audit"),
            reverse("panel:waitlist_review", args=[1]),
            reverse("panel:member_edit", args=[1]),
            reverse("panel:member_toggle_admin", args=[1]),
            reverse("panel:member_ban", args=[1]),
            reverse("panel:member_unban", args=[1]),
            reverse("panel:member_delete", args=[1]),
            reverse("panel:member_reset_whatsapp", args=[1]),
            reverse("panel:whatsapp_handle", args=[1]),
        ]


class PermissionTests(PanelTestCase):
    def test_anonymous_is_redirected_to_login(self):
        for url in self.all_panel_urls():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/accounts/login/", response.url, url)

    def test_logged_in_non_admin_gets_403(self):
        self.client.force_login(self.member)
        for url in self.all_panel_urls():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)

    def test_admin_can_load_every_page(self):
        self.client.force_login(self.admin)
        for name in ["home", "waitlist", "members", "whatsapp_requests",
                     "stats", "mailer", "audit"]:
            response = self.client.get(reverse(f"panel:{name}"))
            self.assertEqual(response.status_code, 200, name)


class WaitlistTests(PanelTestCase):
    def setUp(self):
        self.wreq = WaitlistRequest.objects.create(
            first_name="Pat", last_name="Partner",
            email="Pat.Partner@Example.com", mobile="+44 7700 900999",
            connection="Partner of Mia Member (Darwin)",
        )
        self.client.force_login(self.admin)

    def _review_url(self):
        return reverse("panel:waitlist_review", args=[self.wreq.pk])

    def test_approve_creates_associate_and_sends_invite(self):
        def fake_invite(user, request):
            mail.EmailMessage("Set your MSS password", "link", to=[user.email]).send()

        with mock.patch(
            "accounts.services.send_associate_invite", side_effect=fake_invite
        ) as invite:
            response = self.client.post(
                self._review_url(),
                {"decision": "approve", "review_note": "Checked with Mia"},
            )
        self.assertRedirects(response, reverse("panel:waitlist"))

        user = User.objects.get(username="pat.partner@example.com")
        self.assertEqual(user.email, "pat.partner@example.com")
        self.assertEqual(user.account_type, User.AccountType.ASSOCIATE)
        self.assertEqual(user.first_name, "Pat")
        self.assertEqual(user.mobile, "+44 7700 900999")
        self.assertFalse(user.has_usable_password())

        self.wreq.refresh_from_db()
        self.assertEqual(self.wreq.status, WaitlistRequest.Status.APPROVED)
        self.assertEqual(self.wreq.created_user, user)
        self.assertEqual(self.wreq.reviewed_by, self.admin)
        self.assertEqual(self.wreq.review_note, "Checked with Mia")
        self.assertIsNotNone(self.wreq.reviewed_at)

        self.assertTrue(invite.called)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("pat.partner@example.com", mail.outbox[0].to)
        self.assertTrue(
            AuditLog.objects.filter(actor=self.admin, action="approve_waitlist").exists()
        )

    def test_approve_with_existing_account_links_without_crash(self):
        existing = User.objects.create_user(
            username="pat.partner@example.com",
            email="pat.partner@example.com",
            password="pw",
        )
        before = User.objects.count()
        with mock.patch("accounts.services.send_associate_invite") as invite:
            response = self.client.post(self._review_url(), {"decision": "approve"})
        self.assertRedirects(response, reverse("panel:waitlist"))
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(invite.called)
        self.wreq.refresh_from_db()
        self.assertEqual(self.wreq.status, WaitlistRequest.Status.APPROVED)
        self.assertEqual(self.wreq.created_user, existing)

    def test_reject_records_note_and_audit(self):
        response = self.client.post(
            self._review_url(), {"decision": "reject", "review_note": "No connection"}
        )
        self.assertRedirects(response, reverse("panel:waitlist"))
        self.wreq.refresh_from_db()
        self.assertEqual(self.wreq.status, WaitlistRequest.Status.REJECTED)
        self.assertEqual(self.wreq.review_note, "No connection")
        self.assertEqual(self.wreq.reviewed_by, self.admin)
        self.assertIsNone(self.wreq.created_user)
        self.assertTrue(
            AuditLog.objects.filter(actor=self.admin, action="reject_waitlist").exists()
        )
        self.assertEqual(User.objects.filter(email__iexact=self.wreq.email).count(), 0)


class MemberManagementTests(PanelTestCase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_admin_cannot_demote_themselves(self):
        response = self.client.post(
            reverse("panel:member_toggle_admin", args=[self.admin.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_portal_admin)
        self.assertFalse(AuditLog.objects.filter(action="demote_admin").exists())

    def test_promote_then_demote_other_member(self):
        self.client.post(reverse("panel:member_toggle_admin", args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_portal_admin)
        self.assertTrue(AuditLog.objects.filter(action="promote_admin").exists())

        self.client.post(reverse("panel:member_toggle_admin", args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_portal_admin)
        self.assertTrue(AuditLog.objects.filter(action="demote_admin").exists())

    def test_ban_and_unban(self):
        self.client.post(reverse("panel:member_ban", args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_banned)
        self.assertFalse(self.member.is_active)
        self.assertEqual(self.member.banned_by, self.admin)
        self.assertTrue(AuditLog.objects.filter(action="ban").exists())

        self.client.post(reverse("panel:member_unban", args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_banned)
        self.assertTrue(self.member.is_active)
        self.assertTrue(AuditLog.objects.filter(action="unban").exists())

    def test_admin_cannot_ban_themselves(self):
        self.client.post(reverse("panel:member_ban", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_banned)

    def test_reset_whatsapp_link(self):
        self.member.whatsapp_link_viewed_at = timezone.now()
        self.member.save(update_fields=["whatsapp_link_viewed_at"])
        self.client.post(reverse("panel:member_reset_whatsapp", args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertIsNone(self.member.whatsapp_link_viewed_at)
        self.assertTrue(AuditLog.objects.filter(action="reset_whatsapp").exists())

    def test_delete_confirm_then_delete(self):
        response = self.client.get(reverse("panel:member_delete", args=[self.member.pk]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("panel:member_delete", args=[self.member.pk]))
        self.assertRedirects(response, reverse("panel:members"))
        self.assertFalse(User.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_user").exists())

    def test_admin_cannot_delete_themselves(self):
        response = self.client.post(reverse("panel:member_delete", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_edit_member_details(self):
        response = self.client.post(
            reverse("panel:member_edit", args=[self.member.pk]),
            {
                "first_name": "Mia", "last_name": "Member-Smith",
                "email": "mem1@cam.ac.uk", "college": "darwin",
                "mobile": "+44 7700 900002",
            },
        )
        self.assertRedirects(response, reverse("panel:members"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.last_name, "Member-Smith")
        self.assertEqual(self.member.mobile, "+44 7700 900002")
        self.assertTrue(AuditLog.objects.filter(action="edit_member").exists())


class WhatsAppQueueTests(PanelTestCase):
    def setUp(self):
        self.member.whatsapp_link_viewed_at = timezone.now()
        self.member.save(update_fields=["whatsapp_link_viewed_at"])
        self.wa = WhatsAppAccessRequest.objects.create(
            user=self.member, message="Got a new phone, lost the group."
        )
        self.client.force_login(self.admin)

    def test_handled_with_link_reset(self):
        self.client.post(
            reverse("panel:whatsapp_handle", args=[self.wa.pk]),
            {"action": "handled", "reset_link": "1"},
        )
        self.wa.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(self.wa.status, WhatsAppAccessRequest.Status.HANDLED)
        self.assertEqual(self.wa.handled_by, self.admin)
        self.assertIsNotNone(self.wa.handled_at)
        self.assertIsNone(self.member.whatsapp_link_viewed_at)

    def test_declined_leaves_link_used(self):
        self.client.post(
            reverse("panel:whatsapp_handle", args=[self.wa.pk]), {"action": "declined"}
        )
        self.wa.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(self.wa.status, WhatsAppAccessRequest.Status.DECLINED)
        self.assertIsNotNone(self.member.whatsapp_link_viewed_at)


class MailerTests(PanelTestCase):
    def setUp(self):
        category = Category.objects.create(
            name="Pub Nights", slug="pub-nights", emoji="🍺"
        )
        now = timezone.now()

        def make(title, days, **kwargs):
            return Event.objects.create(
                title=title,
                category=category,
                start=now + datetime.timedelta(days=days),
                created_by=self.admin,
                **kwargs,
            )

        # The official event starts LATER than the casual one, proving the
        # official section leads regardless of chronology.
        self.official = make(
            "Official Garden Party", 10, is_official=True, location="Wolfson gardens"
        )
        self.casual = make("Casual Pub Night", 2)
        self.members_only = make("Members Only Social", 5, members_only=True)
        make("Too Far Away", 30)
        make("Cancelled Thing", 3, is_cancelled=True)

    def test_body_puts_official_events_first(self):
        request = RequestFactory().get("/admin/mailer/")
        subject, body = services.build_whats_on_email(request)

        self.assertTrue(subject.startswith("MSS: What's On — "))
        self.assertIn("⭐ OFFICIAL EVENTS", body)
        self.assertIn("ALSO ON", body)
        self.assertLess(
            body.index("Official Garden Party"), body.index("Casual Pub Night")
        )
        self.assertIn("★ [OFFICIAL] Official Garden Party", body)
        # Members-only events are included: the list is members.
        self.assertIn("Members Only Social", body)
        # Window and cancellation filters hold.
        self.assertNotIn("Too Far Away", body)
        self.assertNotIn("Cancelled Thing", body)
        # RSVP links are absolute.
        self.assertIn(f"http://testserver/events/{self.official.pk}/", body)

    def test_send_records_maillog_and_audit(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("panel:mailer"), {
            "recipient": "soc-mss-members@srcf.net",
            "subject": "MSS: What's On — test",
            "body": "Hello all,\n\nNothing this week.",
        })
        self.assertRedirects(response, reverse("panel:mailer"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["soc-mss-members@srcf.net"])

        log = MailLog.objects.get()
        self.assertTrue(log.ok)
        self.assertEqual(log.sent_by, self.admin)
        self.assertEqual(log.recipients, "soc-mss-members@srcf.net")
        self.assertTrue(
            AuditLog.objects.filter(actor=self.admin, action="send_mailer").exists()
        )
