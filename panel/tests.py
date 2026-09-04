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
from core.models import SiteConfig
from events.models import Category, Event
from panel import ai, services
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
        ("terms/", "terms"), ("pages/<slug:slug>/", "site_page"),
        ("winter-ball/", "winter_ball"),
    ])),
    path("accounts/", _ns("accounts", [
        ("login/", "login"), ("logout/", "logout"), ("waitlist/", "waitlist"),
        ("profile-setup/", "profile_setup"), ("profile/", "profile"),
        ("terms/", "terms"),
    ])),
    path("events/", _ns("events", [
        ("", "calendar"),
        ("tags/<slug:slug>/", "tag_page"),
        ("tags/<slug:slug>/edit/", "tag_edit"),
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
        for name in ["home", "waitlist", "members", "events", "messages",
                     "whatsapp_requests", "content", "contact_map", "terms",
                     "stats", "mailer", "audit", "superadmin"]:
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
        self.client.post(reverse("panel:member_toggle_mute", args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.can_send_messages)

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
        self.assertNotContains(self.client.get(url), "History Club")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), "History Club")

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
