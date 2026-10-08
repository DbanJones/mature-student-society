"""Panel app tests: permission gating, the waitlist approval flow, member
management rules and the What's On mailer.

The suite runs against a self-contained URLconf (this module doubles as one)
so it does not depend on the other apps' URL files, which are developed in
parallel. Stub routes provide every URL name that base.html references.
"""

import datetime
import json
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
from core.models import BUILTIN_TABS, SiteConfig, SitePage
from events.models import Category, Event
from panel import ai, services
from panel.models import AuditLog, MailLog
from testimonials.models import Testimonial

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
        ("terms/", "terms"), ("pages/<slug:slug>/", "site_page"),
        ("search/", "search"), ("banner/dismiss/", "dismiss_banner"),
        ("pages/<slug:slug>/edit/", "site_page_edit"),
        ("pages/<slug:slug>/history/", "site_page_history"),
        ("winter-ball/", "winter_ball"),
    ])),
    path("accounts/", _ns("accounts", [
        ("login/", "login"), ("logout/", "logout"), ("waitlist/", "waitlist"),
        ("profile-setup/", "profile_setup"), ("profile/", "profile"),
        ("terms/", "terms"),
        ("set-password/<uidb64>/<token>/", "set_password"),
    ])),
    path("events/", _ns("events", [
        ("", "calendar"),
        ("groups/", "groups"),
        ("groups/<slug:slug>/", "tag_page"),
        ("groups/<slug:slug>/edit/", "tag_edit"),
        ("<slug:slug>/", "detail"),
        ("<slug:slug>/edit/", "edit"),
    ])),
    path("guide/", _ns("guide", [("", "index")])),
    path("faq/", _ns("faq", [("", "index"), ("who-to-contact/", "contacts"),
                             ("colleges/", "colleges"), ("departments/", "departments")])),
    path("supper-club/", _ns("supper", [("", "index")])),
    path("members/", _ns("members", [
        ("", "directory"), ("<str:username>/", "profile"),
    ])),
    path("messages/", _ns("inbox", [
        ("", "inbox"), ("<str:username>/", "thread"),
        ("<str:username>/block/", "block_toggle"),
    ])),
    path("me/", _ns("dashboard", [("", "home")])),
    path("testimonials/", _ns("testimonials", [
        ("", "index"), ("submit/", "submit"), ("<int:pk>/withdraw/", "withdraw"),
    ])),
    path("polls/", include("polls.urls")),
    path("surveys/", include("surveys.urls")),
    path("notifications/", include("notifications.urls")),
]


@override_settings(ROOT_URLCONF="panel.tests")
class PanelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # The panel's default actor is the super admin (webmaster): admin
        # appointment/removal is reserved for them.
        cls.admin = User.objects.create_user(
            username="adm1", password="pw", email="adm1@cam.ac.uk", crsid="adm1",
            first_name="Ada", last_name="Admin", college="wolfson",
            mobile="+44 7700 900000", is_portal_admin=True, is_super_admin=True,
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
            reverse("panel:member_remind", args=[1]),
            reverse("panel:member_messaging", args=[1]),
            reverse("panel:messaging_settings"),
            reverse("panel:members_cleanup"),
            reverse("panel:whatsapp_handle", args=[1]),
            reverse("panel:testimonials"),
            reverse("panel:testimonial_action", args=[1]),
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
        for name in ["home", "waitlist", "members", "members_cleanup", "events",
                     "messages", "whatsapp_requests", "content", "contact_map",
                     "pages", "navigation", "testimonials", "terms", "stats",
                     "mailer", "audit", "superadmin", "polls", "about", "surveys"]:
            response = self.client.get(reverse(f"panel:{name}"))
            self.assertEqual(response.status_code, 200, name)

    def test_regular_admin_cannot_manage_admins_or_super_tab(self):
        regular = User.objects.create_user(
            username="adm2", password="pw", email="adm2@cam.ac.uk", crsid="adm2",
            first_name="Reg", last_name="Admin", college="darwin",
            mobile="+44 7700 900003", is_portal_admin=True,
        )
        self.client.force_login(regular)
        response = self.client.post(
            reverse("panel:member_toggle_admin", args=[self.member.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.get(reverse("panel:superadmin")).status_code, 403
        )


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

    def _other_admin(self):
        return User.objects.create_user(
            username="adm3", password="pw", email="adm3@cam.ac.uk", crsid="adm3",
            first_name="Otto", last_name="Admin", college="darwin",
            mobile="+44 7700 900004", is_portal_admin=True,
        )

    def test_admin_cannot_ban_another_admin(self):
        # A portal admin must be demoted before they can be banned, mirroring
        # the shadow-ban rule — otherwise a rogue admin can lock out peers/the
        # super admin (whose Raven login the ban would also block).
        target = self._other_admin()
        self.client.post(reverse("panel:member_ban", args=[target.pk]))
        target.refresh_from_db()
        self.assertFalse(target.is_banned)
        self.assertTrue(target.is_active)
        self.assertFalse(AuditLog.objects.filter(action="ban").exists())

    def test_admin_cannot_delete_another_admin(self):
        target = self._other_admin()
        response = self.client.post(
            reverse("panel:member_delete", args=[target.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=target.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="delete_user").exists())

    def test_admin_cannot_mute_another_admin(self):
        target = self._other_admin()
        self.client.post(
            reverse("panel:member_messaging", args=[target.pk]), {"level": "muted"}
        )
        target.refresh_from_db()
        self.assertEqual(target.messaging, User.Messaging.DEFAULT)
        self.assertFalse(AuditLog.objects.filter(action="mute_messages").exists())

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
        # Normalised the same way the member's own profile form does it.
        self.assertEqual(self.member.mobile, "+447700900002")
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
        # RSVP links are absolute and use the name-and-date slug.
        self.assertIn(f"http://testserver/events/{self.official.slug}/", body)

    def test_send_records_maillog_and_audit(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("panel:mailer"), {
            "recipient": "soc-mss-members@srcf.net",
            "subject": "MSS: What's On — test",
            "body": "Hello all,\n\nNothing this week.",
            "action": "send",
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


class MailerAIDraftTests(PanelTestCase):
    """The 'Draft with AI' button rewrites the body without sending."""

    def setUp(self):
        self.client.force_login(self.admin)
        config = SiteConfig.get()
        config.email_api_key = "sk-test-key"
        config.email_ai_engine = "anthropic"
        config.email_tone = "Warm and plain English."
        config.save()

    def _draft(self, **extra):
        data = {
            "recipient": "soc-mss-members@srcf.net",
            "subject": "MSS: What's On — test",
            "body": "Hello all,\n\nPub night on Friday.",
            "action": "ai_draft",
        }
        data.update(extra)
        return self.client.post(reverse("panel:mailer"), data)

    def test_ai_draft_replaces_body_logs_and_sends_nothing(self):
        with mock.patch(
            "panel.ai.draft_email", return_value="Hi everyone — pub night Friday!"
        ) as draft:
            response = self._draft()
        self.assertEqual(response.status_code, 200)
        # The configured engine and key are passed through to the dispatcher.
        engine, key = draft.call_args.args[0], draft.call_args.args[1]
        self.assertEqual(engine, "anthropic")
        self.assertEqual(key, "sk-test-key")
        # New body shown for review; nothing emailed; action audited.
        self.assertContains(response, "Hi everyone — pub night Friday!")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(MailLog.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(actor=self.admin, action="ai_draft_mailer").exists()
        )

    def test_ai_draft_without_key_errors_and_does_not_call_engine(self):
        config = SiteConfig.get()
        config.email_api_key = ""
        config.save()
        with mock.patch("panel.ai.draft_email") as draft:
            response = self._draft()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(draft.called)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(
            AuditLog.objects.filter(action="ai_draft_mailer").exists()
        )

    def test_ai_draft_failure_preserves_the_draft(self):
        with mock.patch("panel.ai.draft_email", side_effect=ai.AIDraftError("boom")):
            response = self._draft()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pub night on Friday.")  # original kept
        self.assertEqual(len(mail.outbox), 0)


class AIDispatchTests(TestCase):
    """Unit tests for the provider dispatch layer (no network)."""

    def test_unknown_engine_raises(self):
        with self.assertRaises(ai.AIDraftError):
            ai.draft_email("not-an-engine", "sk", "system", "user")

    def test_blank_key_raises(self):
        with self.assertRaises(ai.AIDraftError):
            ai.draft_email("openai", "   ", "system", "user")

    def _fake_urlopen(self, payload):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return cm

    def test_openai_shape_parses_content(self):
        payload = {"choices": [{"message": {"content": "  Drafted email.  "}}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_urlopen(payload)):
            out = ai.draft_email("deepseek", "sk", "system", "user")
        self.assertEqual(out, "Drafted email.")

    def test_anthropic_shape_parses_first_text_block(self):
        payload = {"content": [{"type": "text", "text": "Claude draft."}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_urlopen(payload)):
            out = ai.draft_email("anthropic", "sk", "system", "user")
        self.assertEqual(out, "Claude draft.")

    def test_unexpected_response_raises(self):
        with mock.patch("urllib.request.urlopen", return_value=self._fake_urlopen({"nope": 1})):
            with self.assertRaises(ai.AIDraftError):
                ai.draft_email("openai", "sk", "system", "user")


class TermsAdminTests(PanelTestCase):
    """Panel → Terms: any admin can add, change and delete versions, and every
    change leaves a who/what/when trail."""

    @staticmethod
    def _clear_terms_cache():
        # The current-version cache outlives each test's DB rollback; clear
        # it so a published version can't phantom-gate later tests.
        from django.core.cache import cache

        from core.models import TermsVersion

        cache.delete(TermsVersion.CURRENT_CACHE_KEY)

    def _version(self, number=1, published=False):
        from core.models import TermsVersion

        return TermsVersion.objects.create(
            number=number, title="Terms and Conditions",
            content=f"Version {number} text.", is_published=published,
            published_at=timezone.now() if published else None,
        )

    def setUp(self):
        # Start from a clean slate: a data migration seeds a v1 draft, which
        # would collide with the numbers these tests mint.
        from core.models import TermsVersion

        TermsVersion.objects.all().delete()
        self.addCleanup(self._clear_terms_cache)
        # A regular (non-super) admin: terms management is open to all admins.
        self.regular_admin = User.objects.create_user(
            username="adm3", password="pw", email="adm3@cam.ac.uk", crsid="adm3",
            first_name="Reg", last_name="Ular", college="darwin",
            mobile="+44 7700 900004", is_portal_admin=True,
        )
        self.client.force_login(self.regular_admin)

    def test_non_admin_is_kept_out(self):
        from core.models import TermsVersion

        self.client.force_login(self.member)
        version = self._version()
        for url in [
            reverse("panel:terms"),
            reverse("panel:terms_create"),
            reverse("panel:terms_edit", args=[version.pk]),
            reverse("panel:terms_acceptances", args=[version.pk]),
        ]:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(
            self.client.post(
                reverse("panel:terms_delete", args=[version.pk])
            ).status_code,
            403,
        )
        self.assertTrue(TermsVersion.objects.filter(pk=version.pk).exists())

    def test_create_logs_who_and_what(self):
        from core.models import TermsRevision, TermsVersion

        response = self.client.post(reverse("panel:terms_create"), {
            "title": "Terms and Conditions", "number": 1,
            "content": "Fresh terms.", "change_note": "First version",
        })
        self.assertRedirects(response, reverse("panel:terms"))
        version = TermsVersion.objects.get(number=1)
        self.assertEqual(version.created_by, self.regular_admin)
        self.assertFalse(version.is_published)

        revision = TermsRevision.objects.get(terms=version)
        self.assertEqual(revision.action, TermsRevision.Action.CREATED)
        self.assertEqual(revision.editor, self.regular_admin)
        self.assertEqual(revision.content, "Fresh terms.")
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.regular_admin, action="create_terms", target="v1"
            ).exists()
        )

    def test_edit_and_publish_are_both_recorded(self):
        from core.models import TermsRevision

        version = self._version(number=1)
        response = self.client.post(
            reverse("panel:terms_edit", args=[version.pk]),
            {"title": "Terms and Conditions", "number": 1,
             "content": "Edited text.", "change_note": "Tightened wording",
             "is_published": "on"},
        )
        # Publishing re-gates everyone — including the admin who pressed the
        # button — so only check the redirect target, without following it.
        self.assertRedirects(
            response, reverse("panel:terms"), fetch_redirect_response=False
        )
        version.refresh_from_db()
        self.assertTrue(version.is_published)
        self.assertIsNotNone(version.published_at)
        self.assertEqual(version.updated_by, self.regular_admin)
        revision = version.revisions.first()
        self.assertEqual(revision.action, TermsRevision.Action.PUBLISHED)
        self.assertEqual(revision.content, "Edited text.")

    def test_delete_keeps_the_acceptance_log(self):
        from core.models import TermsAcceptance, TermsRevision, TermsVersion

        version = self._version(number=1, published=True)
        self.member.record_terms_acceptance(version, source="portal")
        # The gate applies to admins too; accept so the panel is reachable.
        self.regular_admin.record_terms_acceptance(version, source="portal")

        response = self.client.post(reverse("panel:terms_delete", args=[version.pk]))
        self.assertRedirects(response, reverse("panel:terms"))
        self.assertFalse(TermsVersion.objects.filter(pk=version.pk).exists())

        # The evidence outlives the version: acceptance and revisions remain,
        # carrying their own copy of the number.
        acceptance = TermsAcceptance.objects.get(user=self.member)
        self.assertEqual(acceptance.version_number, 1)
        self.assertIsNone(acceptance.terms)
        self.assertTrue(
            TermsRevision.objects.filter(
                version_number=1, action=TermsRevision.Action.DELETED,
                editor=self.regular_admin,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action="delete_terms", target="v1").exists()
        )

    def test_acceptances_page_lists_who_agreed_and_who_has_not(self):
        version = self._version(number=1, published=True)
        self.member.record_terms_acceptance(version, source="portal")
        self.regular_admin.record_terms_acceptance(version, source="portal")
        response = self.client.get(
            reverse("panel:terms_acceptances", args=[version.pk])
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Mia Member", body)          # accepted
        self.assertIn("Not yet accepted", body)    # the outstanding section


class StatsTimezoneSafetyTests(PanelTestCase):
    """Regression: the statistics service must not lean on database-side
    timezone conversion (TruncMonth / __date), which fails on MySQL hosts
    without the mysql.time_zone* tables — exactly the SRCF setup."""

    def test_rsvps_per_month_buckets_in_python(self):
        category = Category.objects.create(name="Social", slug="social")
        event = Event.objects.create(
            title="Coffee", slug="coffee", category=category,
            start=timezone.now() + datetime.timedelta(days=3),
            created_by=self.admin,
        )
        from events.models import RSVP

        RSVP.objects.create(event=event, user=self.member, status=RSVP.Status.GOING)
        rows = services.rsvps_per_month(months=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["count"], 1)  # this month's bucket
        self.assertEqual(sum(row["count"] for row in rows), 1)

    def test_home_counts_members_this_term(self):
        counts = services.home_counts()
        # Both fixture members were created "now", inside the current term.
        self.assertEqual(counts["members_this_term"], 2)


class TaggedEventsTests(PanelTestCase):
    """The tag owners' workspace: who gets in, what they may promote, and the
    super-event tier above them."""

    def setUp(self):
        self.tag = Category.objects.create(name="Supper Club", slug="supper-club")
        self.other_tag = Category.objects.create(name="History Club", slug="history-club")
        self.owner = User.objects.create_user(
            username="own1", password="pw", email="own1@cam.ac.uk", crsid="own1",
            first_name="Olive", last_name="Owner", college="wolfson",
            mobile="+44 7700 900005",
        )
        self.tag.owners.add(self.owner)
        self.tag.owners.add(self.member)  # tags need an owner; member owns none relevant
        self.tag.owners.remove(self.member)
        start = timezone.now() + datetime.timedelta(days=3)
        self.event = Event.objects.create(
            title="Dinner", category=self.tag, start=start,
            created_by=self.member,
        )
        self.other_event = Event.objects.create(
            title="Battlefield walk", category=self.other_tag,
            start=start + datetime.timedelta(days=1), created_by=self.member,
        )

    def test_access(self):
        url = reverse("panel:tagged_events")
        # A plain member has no business here.
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url).status_code, 403)
        # A tag owner does — and /admin/ takes them straight there.
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertRedirects(self.client.get(reverse("panel:home")), url)
        # Owners see their tags only; admins see all.
        # (Every group is named in the About menu, so check the events.)
        self.assertNotContains(self.client.get(url), "Battlefield walk")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), "Battlefield walk")

    def _action(self, event, **data):
        return self.client.post(
            reverse("panel:tagged_event_action", args=[event.pk]), data
        )

    def test_owner_promotes_and_demotes_within_own_tag(self):
        self.client.force_login(self.owner)
        self._action(self.event, action="promote")
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_official)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.owner, action="mark_official", target="Dinner"
            ).exists()
        )
        self._action(self.event, action="demote")
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_official)

    def test_owner_cannot_touch_another_tag(self):
        self.client.force_login(self.owner)
        response = self._action(self.other_event, action="promote")
        self.assertEqual(response.status_code, 403)
        self.other_event.refresh_from_db()
        self.assertFalse(self.other_event.is_official)
        # Nor adopt INTO a tag they don't own.
        response = self._action(
            self.event, action="adopt", tag=self.other_tag.pk
        )
        self.assertEqual(response.status_code, 403)

    def test_owner_adopts_an_event_into_their_tag(self):
        self.client.force_login(self.owner)
        response = self._action(
            self.other_event, action="adopt", tag=self.tag.pk
        )
        self.assertEqual(response.status_code, 302)
        self.other_event.refresh_from_db()
        self.assertEqual(self.other_event.category, self.tag)
        self.assertTrue(self.other_event.is_official)
        # The creator keeps editing rights, and the owner gains them.
        self.assertTrue(self.other_event.can_edit(self.member))
        self.assertTrue(self.other_event.can_edit(self.owner))

    def test_super_toggle_is_super_admin_only(self):
        regular = User.objects.create_user(
            username="adm9", password="pw", email="adm9@cam.ac.uk", crsid="adm9",
            first_name="Reg", last_name="Admin", college="darwin",
            mobile="+44 7700 900006", is_portal_admin=True,
        )
        url = reverse("panel:event_action", args=[self.event.pk])
        self.client.force_login(regular)
        self.assertEqual(
            self.client.post(url, {"action": "toggle_super"}).status_code, 403
        )
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_super)
        self.client.force_login(self.admin)  # fixture admin IS super admin
        self.client.post(url, {"action": "toggle_super"})
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_super)
        self.assertTrue(
            AuditLog.objects.filter(action="mark_super", target="Dinner").exists()
        )

    def test_promotion_ordering(self):
        self.event.is_super = True
        self.event.save()
        early_plain = Event.objects.create(
            title="Early coffee", category=self.tag,
            start=timezone.now() + datetime.timedelta(hours=2),
            created_by=self.member,
        )
        self.other_event.is_official = True
        self.other_event.save()
        ordered = list(Event.objects.filter(is_cancelled=False).by_promotion())
        self.assertEqual(
            [e.pk for e in ordered],
            [self.event.pk, self.other_event.pk, early_plain.pk],
        )


class OnboardingTests(PanelTestCase):
    """Accounts that never finished signing up: how the panel surfaces them.

    An account exists from the moment Raven first logs someone in (or an
    associate is approved), before the terms gate and the profile form, so
    the panel has to show which step each unfinished account stopped at.
    """

    def setUp(self):
        self.client.force_login(self.admin)
        # A Raven member who logged in once and stopped at the profile form.
        self.stuck = User.objects.create_user(
            username="stk1", email="stk1@cam.ac.uk", crsid="stk1",
            last_login=timezone.now(),
        )
        # An approved associate who never set a password.
        self.ghost = User.objects.create_user(
            username="ghost@example.com", email="ghost@example.com",
            first_name="Gus", last_name="Ghost",
            account_type=User.AccountType.ASSOCIATE,
        )
        self.ghost.set_unusable_password()
        self.ghost.save()

    def _mark_logged_in(self, user):
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

    def test_status_follows_the_sign_up_gates_in_order(self):
        self.assertEqual(self.stuck.onboarding_status(None), User.Onboarding.PROFILE)
        self.assertEqual(
            self.ghost.onboarding_status(None), User.Onboarding.NEVER_LOGGED_IN
        )
        self._mark_logged_in(self.member)
        self.assertEqual(self.member.onboarding_status(None), User.Onboarding.COMPLETE)
        # Published terms gate everyone who hasn't accepted the current version.
        self.assertEqual(self.member.onboarding_status(3), User.Onboarding.TERMS)

    def test_incomplete_filter_lists_only_unfinished_accounts(self):
        self._mark_logged_in(self.member)
        response = self.client.get(reverse("panel:members") + "?incomplete=1")
        self.assertContains(response, "stk1")
        self.assertContains(response, "Gus Ghost")
        self.assertNotContains(response, "Mia Member")
        self.assertContains(response, "Profile incomplete")
        self.assertContains(response, "Never logged in")

    def test_home_counts_unfinished_accounts(self):
        # stuck, ghost and the never-logged-in member; the admin is complete.
        self.assertEqual(services.home_counts()["incomplete_accounts"], 3)
        self.assertEqual(services.stats_summary()["incomplete_members"], 3)

    def test_remind_resends_the_invite_to_an_associate(self):
        mail.outbox = []
        response = self.client.post(reverse("panel:member_remind", args=[self.ghost.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("set-password", mail.outbox[0].body)
        self.assertTrue(AuditLog.objects.filter(action="remind_member").exists())

    def test_remind_nudges_a_raven_member_to_finish_their_profile(self):
        mail.outbox = []
        self.client.post(reverse("panel:member_remind", args=[self.stuck.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Finish setting up", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["stk1@cam.ac.uk"])

    def test_remind_does_nothing_for_a_complete_account(self):
        mail.outbox = []
        self._mark_logged_in(self.member)
        self.client.post(reverse("panel:member_remind", args=[self.member.pk]))
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AuditLog.objects.filter(action="remind_member").exists())

    def test_cleanup_removes_only_old_unfinished_accounts(self):
        old = timezone.now() - datetime.timedelta(days=120)
        User.objects.filter(pk__in=[self.stuck.pk, self.ghost.pk]).update(created_at=old)
        # An old but complete member, and a recent unfinished one, both stay.
        User.objects.filter(pk=self.member.pk).update(created_at=old)
        recent = User.objects.create_user(username="new1", email="new1@cam.ac.uk")

        response = self.client.get(reverse("panel:members_cleanup"))
        self.assertContains(response, "stk1")
        self.assertContains(response, "ghost@example.com")
        self.assertNotContains(response, "new1")
        self.assertNotContains(response, "Mia Member")

        response = self.client.post(reverse("panel:members_cleanup"))
        self.assertRedirects(response, reverse("panel:members"))
        self.assertFalse(
            User.objects.filter(pk__in=[self.stuck.pk, self.ghost.pk]).exists()
        )
        self.assertTrue(User.objects.filter(pk__in=[self.member.pk, recent.pk]).exists())
        self.assertTrue(AuditLog.objects.filter(action="cleanup_incomplete").exists())

    def test_cleanup_spares_accounts_that_took_part(self):
        old = timezone.now() - datetime.timedelta(days=120)
        User.objects.filter(pk=self.stuck.pk).update(created_at=old)
        category = Category.objects.create(name="Pub", slug="pub")
        Event.objects.create(
            title="Quiz", category=category, start=timezone.now(),
            created_by=self.stuck,
        )
        self.client.post(reverse("panel:members_cleanup"))
        self.assertTrue(User.objects.filter(pk=self.stuck.pk).exists())

    def test_edit_form_normalises_mobile_and_warns_on_blank_college(self):
        response = self.client.post(
            reverse("panel:member_edit", args=[self.member.pk]),
            {
                "first_name": "Mia", "last_name": "Member",
                "email": "mem1@cam.ac.uk", "college": "",
                "mobile": "07700 900 123",
            },
            follow=True,
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.mobile, "07700900123")
        self.assertContains(response, "still has no college")


class PanelNavTests(PanelTestCase):
    """The two-tier admin bar: groups on top, the active group's pages beneath."""

    def test_groups_and_subtabs_render_for_an_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("panel:waitlist"))
        self.assertContains(response, 'class="panel-tabs"')
        self.assertContains(response, 'class="panel-subtabs"')
        for label in ["Overview", "People", "Events", "Messages", "Content",
                      "Mailer", "Stats", "Super admin"]:
            self.assertContains(response, ">" + label, msg_prefix=label)
        # The active group's pages appear on the second row.
        self.assertContains(response, ">Waitlist</a>")
        self.assertContains(response, ">WhatsApp</a>")
        # Pages from other groups do not.
        self.assertNotContains(response, ">Audit log</a>")

    def test_single_page_groups_have_no_second_row(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("panel:mailer"))
        self.assertNotContains(response, 'class="panel-subtabs"')

    def test_regular_admin_has_no_super_tab(self):
        regular = User.objects.create_user(
            username="adm9", password="pw", email="adm9@cam.ac.uk", crsid="adm9",
            first_name="Reg", last_name="Admin", college="darwin",
            mobile="+44 7700 900009", is_portal_admin=True,
        )
        self.client.force_login(regular)
        response = self.client.get(reverse("panel:home"))
        self.assertNotContains(response, "tab-super")

    def test_pending_items_badge_the_people_group(self):
        WaitlistRequest.objects.create(
            first_name="Pat", last_name="Pending", email="pat@example.com",
            connection="partner",
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("panel:home"))
        self.assertContains(response, 'People<span class="nav-badge">1</span>')


class MessagingAdminTests(PanelTestCase):
    """Admins set who may message whom: per member, and site-wide."""

    def setUp(self):
        self.client.force_login(self.admin)

    def test_enable_mute_and_reset_a_member(self):
        url = reverse("panel:member_messaging", args=[self.member.pk])
        self.client.post(url, {"level": "enabled"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.messaging, User.Messaging.ENABLED)
        self.assertTrue(AuditLog.objects.filter(action="enable_messaging").exists())

        self.client.post(url, {"level": "muted"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.messaging, User.Messaging.MUTED)
        self.assertTrue(AuditLog.objects.filter(action="mute_messages").exists())

        self.client.post(url, {"level": "default"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.messaging, User.Messaging.DEFAULT)
        self.assertTrue(AuditLog.objects.filter(action="reset_messaging").exists())

    def test_unknown_level_changes_nothing(self):
        self.client.post(
            reverse("panel:member_messaging", args=[self.member.pk]), {"level": "loud"}
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.messaging, User.Messaging.DEFAULT)

    def test_site_wide_mode_toggle_is_audited(self):
        response = self.client.post(
            reverse("panel:messaging_settings"), {"messaging_mode": "open"}
        )
        self.assertRedirects(response, reverse("panel:messages"))
        self.assertEqual(SiteConfig.get().messaging_mode, "open")
        self.assertTrue(
            AuditLog.objects.filter(action="update_messaging_mode", detail="open").exists()
        )

    def test_messages_page_lists_enabled_and_muted_members(self):
        self.member.messaging = User.Messaging.ENABLED
        self.member.save(update_fields=["messaging"])
        quiet = User.objects.create_user(
            username="qt1", email="qt1@cam.ac.uk", first_name="Quinn", last_name="Quiet",
            college="darwin", mobile="+44 7700 900010", messaging=User.Messaging.MUTED,
        )
        response = self.client.get(reverse("panel:messages"))
        self.assertContains(response, "Mia Member")
        self.assertContains(response, "Quinn Quiet")
        self.assertContains(response, "Switch off")
        self.assertContains(response, "Unmute")


class PagesAdminTests(PanelTestCase):
    """The Pages tab: admins own every page and name its editors."""

    def setUp(self):
        self.client.force_login(self.admin)

    def test_create_with_editors_writes_a_revision_and_lists_the_page(self):
        response = self.client.post(reverse("panel:page_create"), {
            "title": "Sponsors", "content": "Thanks.", "is_published": "on",
            "nav_label": "", "nav_visibility": "public", "sort_order": 100,
            "editors": [self.member.pk],
        })
        self.assertRedirects(response, reverse("panel:pages"))
        page = SitePage.objects.get(slug="sponsors")
        self.assertEqual(list(page.editors.all()), [self.member])
        self.assertEqual(page.revisions.count(), 1)
        self.assertEqual(page.revisions.first().action, "created")
        response = self.client.get(reverse("panel:pages"))
        self.assertContains(response, "Sponsors")
        self.assertContains(response, "Mia Member")
        self.assertContains(response, "Fixed pages")

    def test_edit_records_editors_in_the_audit_log(self):
        page = SitePage.objects.create(title="Rules", slug="rules", content="Be kind.")
        self.client.post(reverse("panel:page_edit", args=[page.pk]), {
            "title": "Rules", "content": "Be kinder.", "is_published": "on",
            "nav_label": "", "nav_visibility": "public", "sort_order": 100,
            "editors": [self.member.pk],
        })
        page.refresh_from_db()
        self.assertEqual(page.content, "Be kinder.")
        entry = AuditLog.objects.get(action="edit_page")
        self.assertIn("Mia Member", entry.detail)

    def test_navigation_tab_saves_tab_visibility(self):
        data = {f"tab_{key}": "public" for key, _label, _default in BUILTIN_TABS}
        data["tab_supper"] = "members"
        response = self.client.post(reverse("panel:navigation"), data)
        self.assertRedirects(response, reverse("panel:navigation"))
        self.assertEqual(SiteConfig.get().tab_visibility_for("supper"), "members")
        self.assertTrue(AuditLog.objects.filter(action="update_tab_visibility").exists())


class TestimonialsAdminTests(PanelTestCase):
    """The committee approves testimonials before they go public."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.testimonial = Testimonial.objects.create(
            author=self.member, author_name="Mia Member", author_college="Darwin",
            body="Brilliant society.", is_anonymous=True,
        )
        self.action = reverse("panel:testimonial_action", args=[self.testimonial.pk])

    def test_queue_shows_the_author_even_when_anonymous(self):
        response = self.client.get(reverse("panel:testimonials"))
        self.assertContains(response, "Mia Member")
        self.assertContains(response, "will show as anonymous")
        self.assertContains(response, "Brilliant society.")

    def test_approve_feature_unpublish_and_delete_are_audited(self):
        self.client.post(self.action, {"action": "approve"})
        self.testimonial.refresh_from_db()
        self.assertEqual(self.testimonial.status, Testimonial.Status.APPROVED)
        self.assertEqual(self.testimonial.reviewed_by, self.admin)
        self.assertTrue(AuditLog.objects.filter(action="approve_testimonial").exists())

        self.client.post(self.action, {"action": "feature"})
        self.testimonial.refresh_from_db()
        self.assertTrue(self.testimonial.is_featured)

        self.client.post(self.action, {"action": "reject", "review_note": "Too long"})
        self.testimonial.refresh_from_db()
        self.assertEqual(self.testimonial.status, Testimonial.Status.REJECTED)
        self.assertEqual(self.testimonial.review_note, "Too long")
        self.assertFalse(self.testimonial.is_featured)

        self.client.post(self.action, {"action": "delete"})
        self.assertFalse(Testimonial.objects.filter(pk=self.testimonial.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_testimonial").exists())

    def test_only_published_testimonials_can_be_featured(self):
        self.client.post(self.action, {"action": "feature"})
        self.testimonial.refresh_from_db()
        self.assertFalse(self.testimonial.is_featured)

    def test_pending_count_reaches_the_overview_and_the_content_badge(self):
        self.assertEqual(services.home_counts()["pending_testimonials"], 1)
        response = self.client.get(reverse("panel:home"))
        self.assertContains(response, "Testimonials to review")
        self.assertContains(response, 'Content<span class="nav-badge">1</span>')


class AboutAndBannerAdminTests(PanelTestCase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_committee_and_activity_crud(self):
        from core.models import Activity, CommitteeMember

        response = self.client.post(reverse("panel:committee_add"), {
            "name": "Pat President", "role": "President", "sort_order": 1, "is_active": "on",
        })
        self.assertRedirects(response, reverse("panel:about"))
        member = CommitteeMember.objects.get(name="Pat President")
        self.client.post(reverse("panel:committee_edit", args=[member.pk]), {
            "name": "Pat President", "role": "Chair", "sort_order": 1, "is_active": "on",
        })
        member.refresh_from_db()
        self.assertEqual(member.role, "Chair")
        self.client.post(reverse("panel:activity_add"), {
            "emoji": "🎲", "name": "Board games", "blurb": "Monthly.", "sort_order": 5,
        })
        activity = Activity.objects.get(name="Board games")
        response = self.client.get(reverse("panel:about"))
        self.assertContains(response, "Pat President")
        self.assertContains(response, "Board games")
        self.client.post(reverse("panel:activity_delete", args=[activity.pk]))
        self.assertFalse(Activity.objects.filter(pk=activity.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_activity").exists())

    def test_banner_saves_and_clears_with_audit(self):
        response = self.client.post(reverse("panel:banner"), {
            "banner_text": "Freshers Fair this Saturday", "banner_until": "",
        })
        self.assertRedirects(response, reverse("panel:navigation"))
        self.assertEqual(SiteConfig.get().banner_text, "Freshers Fair this Saturday")
        self.assertContains(self.client.get(reverse("panel:navigation")), "live now")
        self.client.post(reverse("panel:banner"), {"banner_text": "", "banner_until": ""})
        self.assertEqual(SiteConfig.get().banner_text, "")
        self.assertEqual(AuditLog.objects.filter(action="update_banner").count(), 2)


class BulkAndQuickToggleTests(PanelTestCase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.other = User.objects.create_user(
            username="blk1", email="blk1@cam.ac.uk", first_name="Bea", last_name="Bulk",
            college="darwin", mobile="+44 7700 900031",
        )

    def test_bulk_export_is_csv_and_audited(self):
        response = self.client.post(reverse("panel:members_bulk"), {
            "action": "export", "ids": [self.member.pk, self.other.pk],
        })
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        body = response.content.decode()
        self.assertIn("Mia Member", body)
        self.assertIn("Bea Bulk", body)
        self.assertTrue(AuditLog.objects.filter(action="export_members").exists())

    def test_bulk_ban_confirms_then_bans_but_never_admins(self):
        response = self.client.post(reverse("panel:members_bulk"), {
            "action": "ban", "ids": [self.member.pk, self.admin.pk],
        })
        self.assertContains(response, "Ban 1 member?")
        self.assertContains(response, "Mia Member")
        self.client.post(reverse("panel:members_bulk"), {
            "action": "ban", "confirm": "1", "ids": [self.member.pk, self.admin.pk],
        })
        self.member.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertTrue(self.member.is_banned)
        self.assertFalse(self.admin.is_banned)

    def test_bulk_remind_only_emails_unfinished_accounts(self):
        stuck = User.objects.create_user(username="stk2", email="stk2@cam.ac.uk", last_login=timezone.now())
        mail.outbox = []
        self.client.post(reverse("panel:members_bulk"), {
            "action": "remind", "ids": [stuck.pk, self.admin.pk],
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["stk2@cam.ac.uk"])

    def test_page_quick_toggles(self):
        page = SitePage.objects.create(title="Rules", slug="rules", content="x")
        self.client.post(reverse("panel:page_toggle", args=[page.pk]))
        page.refresh_from_db()
        self.assertFalse(page.is_published)
        self.client.post(reverse("panel:page_toggle", args=[page.pk]), {"nav_visibility": "members"})
        page.refresh_from_db()
        self.assertEqual(page.nav_visibility, "members")
        self.assertTrue(AuditLog.objects.filter(action="unpublish_page").exists())


class VisualToolsTests(PanelTestCase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_stats_page_renders_svg_charts_and_tables(self):
        category = Category.objects.create(name="Pub", slug="pub")
        Event.objects.create(title="Quiz", category=category, start=timezone.now(), created_by=self.member)
        response = self.client.get(reverse("panel:stats"))
        self.assertContains(response, "<svg", count=3)
        self.assertContains(response, "Members over the last year")
        self.assertContains(response, "Tags that fill up")
        self.assertContains(response, "Pub")

    def test_charts_escape_labels(self):
        from panel.charts import heatmap, line_chart
        svg = line_chart([("<b>Jan</b>", 1), ("Feb", 3)])
        self.assertIn("&lt;b&gt;Jan&lt;/b&gt;", svg)
        self.assertIn("<circle", svg)
        svg = heatmap([("Mon", [0, 2])], ["8-10", "10-12"])
        self.assertIn("<rect", svg)

    def test_member_edit_shows_an_activity_timeline(self):
        category = Category.objects.create(name="Pub", slug="pub")
        event = Event.objects.create(title="Quiz", category=category, start=timezone.now(), created_by=self.member)
        from events.models import RSVP
        RSVP.objects.create(event=event, user=self.member)
        response = self.client.get(reverse("panel:member_edit", args=[self.member.pk]))
        self.assertContains(response, "Created “Quiz”")
        self.assertContains(response, "Going to “Quiz”")

    def test_term_dates_save_and_terms_diff_renders(self):
        import datetime
        response = self.client.post(reverse("panel:term_dates"), {
            "michaelmas_start": "2026-10-06", "lent_start": "", "easter_start": "",
        })
        self.assertRedirects(response, reverse("panel:navigation"))
        config = SiteConfig.get()
        self.assertEqual(config.michaelmas_start, datetime.date(2026, 10, 6))
        self.assertEqual(config.term_week_label(datetime.date(2026, 10, 19)), "Michaelmas wk 3")
        self.assertEqual(config.term_week_label(datetime.date(2026, 9, 1)), "")

        from core.models import TermsRevision, TermsVersion
        version = TermsVersion.objects.create(number=7, content="one")
        first = version.save_revision(self.admin, TermsRevision.Action.CREATED)
        version.content = "one" + chr(10) + "two"
        version.save()
        second = version.save_revision(self.admin, TermsRevision.Action.EDITED)
        response = self.client.get(reverse("panel:terms_diff", args=[second.pk]))
        self.assertContains(response, "diff-ins")
        self.assertContains(response, "+1")
