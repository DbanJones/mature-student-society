"""Downloading the website's mailing addresses, the society's own addresses
(mss-members@srcf.net for the list, mss-admins@srcf.net for its admins),
and keeping the list and the website's mailer apart."""

import csv
import datetime
import io

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import SiteConfig
from panel import services
from panel.models import AuditLog, OldSubscriber

FAST = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])  # many accounts, quickly


def member(username, first="M", last="", **extra):
    return User.objects.create_user(
        username=username, password="pw", first_name=first, last_name=last or username, college="wolfson",
        mobile="+44 7700 900100", email=extra.pop("email", f"{username}@cam.ac.uk"), **extra,
    )


@FAST
class MailingListDownloadTests(TestCase):
    def setUp(self):
        self.admin = member("dl001", first="Ada", last="Admin", is_portal_admin=True)
        self.client.force_login(self.admin)
        member("ab123", first='=HYPERLINK("http://example.com","x")', last="Evil")
        member("cd456", first="José", last="Núñez")
        member("ef789", wants_mailer=False)  # opted out on the profile
        member("gh012", is_banned=True)
        member("ij345", email="")  # no address
        OldSubscriber.objects.create(email="old.friend@example.com", first_name="Olive", last_name="Old")
        OldSubscriber.objects.create(email="ab123@cam.ac.uk", first_name="Abby", last_name="Old-list")  # also a member
        OldSubscriber.objects.create(email="gone@example.com", unsubscribed_at=timezone.now())
        OldSubscriber.objects.create(email="ef789@cam.ac.uk")  # a member who opted out: never mailed

    def download(self, audience="both", fmt="csv", client=None):
        return (client or self.client).post(reverse("panel:mailer_download"), {"audience": audience, "format": fmt})

    def rows(self, response):
        text = response.content.decode("utf-8")
        self.assertTrue(text.startswith("﻿"))  # Excel reads it as UTF-8
        return list(csv.reader(io.StringIO(text[1:])))

    def test_the_spreadsheet_is_everyone_the_mailer_reaches_each_inbox_once(self):
        response = self.download("both")
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        today = timezone.localdate().isoformat()
        self.assertEqual(response["Content-Disposition"], f'attachment; filename="mss-mailing-list-both-{today}.csv"')
        self.assertIn("no-store", response["Cache-Control"])
        rows = self.rows(response)
        self.assertEqual(rows[0], ["email", "first name", "last name", "on the list as"])
        emails = [row[0] for row in rows[1:]]
        members, old = services.mailer_recipients("both")
        self.assertEqual(emails, members + old)  # exactly what a send would reach, in order
        self.assertEqual(sorted(emails), sorted(["dl001@cam.ac.uk", "ab123@cam.ac.uk", "cd456@cam.ac.uk",
                                                 "old.friend@example.com"]))
        by_email = {row[0]: row for row in rows[1:]}
        self.assertEqual(by_email["cd456@cam.ac.uk"][1:], ["José", "Núñez", "website member"])
        self.assertEqual(by_email["old.friend@example.com"][1:], ["Olive", "Old", "old mailing list"])
        self.assertTrue(by_email["ab123@cam.ac.uk"][1].startswith("'="))  # never run as a formula

    def test_each_audience_downloads_its_own_people(self):
        for audience in services.LIST_AUDIENCES:
            members, old = services.mailer_recipients(audience)
            text = self.download(audience, "text").content.decode("utf-8")
            self.assertEqual(text.splitlines(), members + old, audience)
        old_only = self.download("old", "text").content.decode().splitlines()
        self.assertIn("ab123@cam.ac.uk", old_only)  # everyone still on the old list, members included
        self.assertNotIn("gone@example.com", old_only)
        self.assertNotIn("ef789@cam.ac.uk", old_only)
        self.assertEqual(self.download("old_new", "text").content.decode().splitlines(), ["old.friend@example.com"])

    def test_addresses_only_is_one_per_line_for_a_mass_subscription_box(self):
        response = self.download("members", "text")
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertTrue(response["Content-Disposition"].endswith('.txt"'))
        lines = response.content.decode("utf-8").splitlines()
        self.assertEqual(lines, services.member_addresses())
        self.assertTrue(all("@" in line and "," not in line and " " not in line for line in lines))

    def test_every_download_is_in_the_audit_log(self):
        self.download("old_new", "text")
        entry = AuditLog.objects.get(action="download_mailing_list")
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.target, services.AUDIENCE_LABELS["old_new"])
        self.assertEqual(entry.detail, "1 address, text")

    def test_only_admins_by_post_with_a_token(self):
        url = reverse("panel:mailer_download")
        anonymous = Client()
        self.assertEqual(anonymous.post(url, {"audience": "both"}).status_code, 302)
        plain = Client()
        plain.force_login(User.objects.get(username="cd456"))
        self.assertEqual(plain.post(url, {"audience": "both"}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(url, {"audience": "both"}).status_code, 403)
        self.assertFalse(AuditLog.objects.filter(action="download_mailing_list").exists())

    def test_an_unknown_audience_downloads_nothing(self):
        for audience in ("address", "everyone", ""):
            response = self.download(audience)
            self.assertRedirects(response, reverse("panel:mailer") + "#download", fetch_redirect_response=False)
        self.assertFalse(AuditLog.objects.filter(action="download_mailing_list").exists())

    def test_the_mailer_tab_offers_the_download_with_counts(self):
        page = self.client.get(reverse("panel:mailer"))
        self.assertContains(page, 'action="' + reverse("panel:mailer_download") + '"')
        self.assertContains(page, '<option value="both" selected>Both: website members and the old list, each person '
                                  'once (4)</option>', html=True)
        self.assertNotContains(page, '<option value="address"')  # nothing to download for "one address"
        self.assertContains(page, f'<option value="stopped">{services.STOPPED_LABEL} (3)</option>', html=True)
        self.assertContains(page, 'href="https://lists.srcf.net/mailman/admindb/mss-members"')  # held posts
        self.assertContains(page, 'href="' + reverse("panel:mailing_list") + '"')  # where the list is managed
        self.assertNotContains(page, 'name="since"')

    def test_people_who_stopped_the_mailer_for_taking_off_the_list(self):
        OldSubscriber.objects.create(email="xy987@hermes.cam.ac.uk", unsubscribed_at=timezone.now())
        member("xy987")  # still wants the mailer at xy987@cam.ac.uk: neither spelling is taken off
        OldSubscriber.objects.create(email="zz111@cam.ac.uk", unsubscribed_at=timezone.now())
        response = self.download(services.STOPPED, "text")
        lines = response.content.decode().splitlines()
        for gone in ("ef789@cam.ac.uk", "gh012@cam.ac.uk", "gone@example.com", "zz111@cam.ac.uk",
                     "zz111@hermes.cam.ac.uk"):
            self.assertIn(gone, lines)
        for still in ("xy987@cam.ac.uk", "xy987@hermes.cam.ac.uk", "cd456@cam.ac.uk", "old.friend@example.com",
                      "ab123@cam.ac.uk"):
            self.assertNotIn(still, lines)
        self.assertEqual(len(lines), len(set(line.lower() for line in lines)))
        rows = self.rows(self.download(services.STOPPED, "csv"))
        self.assertEqual(rows[0][3], "why")
        why = {row[0]: row[3] for row in rows[1:]}
        self.assertEqual(why["gh012@cam.ac.uk"], "banned")
        self.assertEqual(why["ef789@cam.ac.uk"], "opted out on the website")
        self.assertEqual(why["gone@example.com"], "unsubscribed")
        self.assertEqual(AuditLog.objects.filter(action="download_mailing_list",
                                                 target=services.STOPPED_LABEL).count(), 2)

    def test_the_other_downloads_of_people_are_formula_safe_and_on_the_record(self):
        self.assertEqual(self.client.get(reverse("panel:old_list") + "?export=csv")["Content-Type"],
                         "text/html; charset=utf-8")  # a link can't download it
        self.assertFalse(AuditLog.objects.filter(action="export_old_list").exists())
        export = self.client.post(reverse("panel:old_list"), {"export": "csv"})
        self.assertIn("no-store", export["Cache-Control"])
        self.assertTrue(AuditLog.objects.filter(action="export_old_list").exists())
        evil = User.objects.get(username="ab123")
        bulk = self.client.post(reverse("panel:members_bulk"), {"action": "export", "ids": [evil.pk]})
        self.assertIn("'=HYPERLINK", bulk.content.decode())


@FAST
class SocietyAddressesTests(TestCase):
    def test_the_list_is_the_societys_mailing_list(self):
        self.assertEqual(SiteConfig.get().mailing_list_address, "mss-members@srcf.net")
        admin = member("ad999", is_portal_admin=True)
        self.client.force_login(admin)
        page = self.client.get(reverse("panel:mailer"))
        self.assertContains(page, 'value="mss-members@srcf.net"')  # "One address" starts on the list
        self.assertContains(page, "The society's mailing list is mss-members@srcf.net.")
        self.assertContains(page, "One address: the society&#x27;s mailing list, or a check")

    def test_the_migration_fills_in_the_list_only_where_none_is_set(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module("core.migrations.0016_society_mailing_list")
        config = SiteConfig.get()
        config.mailing_list_address = ""
        config.save()
        migration.use_the_list(apps, None)
        config.refresh_from_db()
        self.assertEqual(config.mailing_list_address, "mss-members@srcf.net")
        config.mailing_list_address = "committee-list@example.org"
        config.save()
        migration.use_the_list(apps, None)
        config.refresh_from_db()
        self.assertEqual(config.mailing_list_address, "committee-list@example.org")

    @override_settings(DEFAULT_FROM_EMAIL="MSS <mss-admins@srcf.net>")
    def test_the_admins_address_is_a_good_sender_for_site_health(self):
        from panel.healthchecks import checks
        from panel.healthchecks.core import Context

        context = Context()
        context.production = True
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="localhost",
                               EMAIL_PORT=25):
            result = checks.email_setup(context)
        self.assertEqual(result.status, "ok")
        self.assertIn("mss-admins@srcf.net", result.summary)


class ListAdminLinkTests(TestCase):
    def test_srcf_lists_link_to_their_mailman_pages_and_nothing_else_does(self):
        from core import mailinglist

        self.assertEqual(mailinglist.list_url("mss-members@srcf.net", "admin", "members/add"),
                         "https://lists.srcf.net/mailman/admin/mss-members/members/add")
        self.assertEqual(mailinglist.list_url(" MSS-Members@SRCF.net "), "https://lists.srcf.net/mailman/admin/mss-members")
        self.assertEqual(mailinglist.list_url("mss-members@srcf.net", "admindb"),
                         "https://lists.srcf.net/mailman/admindb/mss-members")
        self.assertEqual(mailinglist.leave_address("mss-members@srcf.net"), "mss-members-request@srcf.net")
        for other in ("", None, "list@example.com", "a/b@srcf.net", "mss-members@lists.srcf.net", "@srcf.net",
                      "mss..members?x=1@srcf.net"):
            self.assertEqual(mailinglist.list_url(other, "admin", "members/add"), "", other)
            self.assertEqual(mailinglist.leave_address(other), "", other)
        self.assertEqual(mailinglist.leave_note(""), "")
        self.assertIn("instructions at the foot", mailinglist.leave_note("committee@example.org"))
        self.assertIn("reply to the email that asks you to confirm", mailinglist.leave_note("mss-members@srcf.net"))
        self.assertNotIn("-request", mailinglist.leave_note("committee@example.org"))

    @FAST
    def test_the_download_card_points_to_the_mailing_list_page(self):
        self.client.force_login(member("lk001", is_portal_admin=True))
        page = self.client.get(reverse("panel:mailer"))
        self.assertContains(page, "use <a href=\"" + reverse("panel:mailing_list") + "\">People → Mailing list</a>")
        config = SiteConfig.get()
        config.mailing_list_address = "committee@example.org"
        config.save()
        page = self.client.get(reverse("panel:mailer"))
        self.assertNotContains(page, "lists.srcf.net")
        self.assertContains(page, "keeps its own subscribers")


@FAST
class ListAndWebsiteTests(TestCase):
    """The society's list and the website's own mailing: no double sends,
    and people stopping the website's mailer are told about the list."""

    def setUp(self):
        self.admin = member("lw001", first="Ada", last="Admin", is_portal_admin=True)
        self.client.force_login(self.admin)
        member("lw002")

    def send(self, audience, recipient=""):
        return self.client.post(reverse("panel:mailer"), {
            "audience": audience, "recipient": recipient, "subject": "What's On", "body": "Hello", "action": "send",
        }, follow=True)

    def test_the_same_mailer_never_goes_to_the_list_and_a_website_audience(self):
        from django.core import mail

        self.send("address", "mss-members@srcf.net")
        self.assertEqual(len(mail.outbox), 1)
        refused = self.send("members")
        self.assertContains(refused, "This mailer went to “mss-members@srcf.net”")
        self.assertEqual(len(mail.outbox), 1)
        self.send("address", "someone.else@example.org")  # a check to another address still goes
        self.assertEqual(len(mail.outbox), 2)

    def test_and_the_other_way_round(self):
        from django.core import mail

        self.send("both")
        sent = len(mail.outbox)
        self.assertGreater(sent, 0)
        self.send("address", "MSS-Members@srcf.net")  # however it's typed
        self.assertEqual(len(mail.outbox), sent)

    def test_stopping_the_websites_mailer_says_the_list_is_separate(self):
        from django.core import mail

        visitor = Client()
        page = visitor.get(reverse("core:unsubscribe"))
        self.assertContains(page, "mss-members-request@srcf.net")
        visitor.post(reverse("core:unsubscribe"), {"email": "lw002@cam.ac.uk"})
        self.assertIn("mss-members-request@srcf.net", mail.outbox[-1].body)
        self.assertIn("won't get the website's What's On mailer", mail.outbox[-1].body)

    def test_the_profile_checkbox_says_the_list_is_separate(self):
        from accounts.forms import ProfileForm

        form = ProfileForm(instance=self.admin)
        self.assertIn("mss-members-request@srcf.net", form.fields["wants_mailer"].help_text)
        self.assertIn("round-up", form.fields["wants_mailer"].help_text)


class AuditLogLimitsTests(TestCase):
    def test_long_values_are_cut_to_fit_rather_than_failing_the_action(self):
        AuditLog.record(None, "x" * 80, target="t" * 400, detail="d" * 900)
        entry = AuditLog.objects.get()
        self.assertEqual((len(entry.action), len(entry.target), len(entry.detail)), (60, 200, 300))

    @FAST
    def test_the_old_list_export_logs_only_filters_it_knows(self):
        self.client.force_login(member("al001", is_portal_admin=True))
        OldSubscriber.objects.create(email="one@example.com")
        OldSubscriber.objects.create(email="two@example.com", unsubscribed_at=timezone.now())
        export = self.client.post(reverse("panel:old_list"), {"export": "csv", "status": "moved (per chair)"})
        self.assertEqual(len(export.content.decode().splitlines()), 3)  # header + everyone: no filter applied
        self.assertEqual(AuditLog.objects.get(action="export_old_list").detail, "2 rows")
