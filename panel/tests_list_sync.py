"""The website's copy of the society's Mailman list: which accounts are on
it, and adding the ones who aren't (Admin > People > Mailing list)."""

import datetime

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from panel import listsync
from panel.models import AuditLog, ListMember, ListSync, OldSubscriber

FAST = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])

MAILMAN_PAGE = """mss-members mailing list membership management
Ab123@cam.ac.uk  [ ] mod  Abby
cd456 at hermes.cam.ac.uk
friend at gmail.com
Lunch at 12.30, by the river.
Mss-members list run by mss-members-owner at srcf.net
mss-members-owner@srcf.net  mailman@lists.srcf.net  mss-members@srcf.net
"""


def member(username, **extra):
    return User.objects.create_user(
        username=username, password="pw", first_name=extra.pop("first", username.title()), last_name="Tester",
        college="darwin", mobile="+44 7700 900100", email=extra.pop("email", f"{username}@cam.ac.uk"), **extra,
    )


class ReadingPastesTests(TestCase):
    def test_addresses_are_picked_out_of_a_mailman_page(self):
        self.assertEqual(listsync.addresses_in(MAILMAN_PAGE, "mss-members@srcf.net"),
                         ["ab123@cam.ac.uk", "cd456@hermes.cam.ac.uk", "friend@gmail.com"])

    def test_a_mailbox_has_one_name(self):
        self.assertEqual(listsync.mailbox_key(" CD456@Hermes.CAM.ac.uk "), "cd456@cam.ac.uk")
        self.assertEqual(listsync.mailbox_key("Jo.Bloggs@Gmail.com"), "jo.bloggs@gmail.com")
        self.assertEqual(listsync.mailbox_key("not-a-crsid@cam.ac.uk"), "not-a-crsid@cam.ac.uk")
        keys = {"cd456@cam.ac.uk"}
        self.assertTrue(listsync.on_list("cd456@hermes.cam.ac.uk", keys))
        # the same person's Cambridge address isn't where they now want the mail
        self.assertFalse(listsync.on_list("cd.personal@gmail.com", keys))
        self.assertFalse(listsync.on_list("", keys))

    def test_the_list_s_own_addresses_aren_t_members_on_any_host(self):
        page = ("Mss-members list run by mss-members-owner at lists.srcf.net\n"
                "mss-members-request@srcf.net mailman-owner@lists.srcf.net\n"
                "* (jane at Example.com)\n"   # delivery stopped: still a member
                "mss-admins at srcf.net\n")   # kept, so the page can say it shouldn't be there
        self.assertEqual(listsync.addresses_in(page, "mss-members@srcf.net"), ["jane@example.com", "mss-admins@srcf.net"])

    def test_mailman_s_own_counts_are_read(self):
        roster = "12 Non-digested Members of Mss-members:\n3 Digested Members of Mss-members:\n(2 private members not shown)"
        self.assertEqual(listsync.mailman_counts(roster), {"total": 15, "hidden": 2})
        self.assertEqual(listsync.mailman_counts("240 members total, 30 shown"), {"total": 240, "shown": 30})
        self.assertEqual(listsync.mailman_counts("240 members total"), {"total": 240})
        self.assertEqual(listsync.mailman_counts("Lunch at 1, 30 of us"), {})

    def test_a_long_run_of_text_with_no_address_is_quick(self):
        import time

        started = time.perf_counter()
        found = listsync.addresses_in("a" * 200_000 + " x@example.org " + "1" * 200_000)
        listsync.mailman_counts("1" * 200_000)
        self.assertEqual(found, ["x@example.org"])
        self.assertLess(time.perf_counter() - started, 10)  # took over a minute before the patterns had limits

    def test_nothing_longer_than_an_address_can_be(self):
        too_long = "a" * 64 + "@" + ".".join(["b" * 60] * 4) + ".com"
        self.assertEqual(listsync.addresses_in(too_long + " ok@example.org"), ["ok@example.org"])

    def test_nothing_but_noise_finds_nothing(self):
        self.assertEqual(listsync.addresses_in("Meet at 6.30 at the bar. Then at noon.", "mss-members@srcf.net"), [])
        self.assertEqual(listsync.addresses_in("", ""), [])


@FAST
class MailingListPageTests(TestCase):
    def setUp(self):
        self.admin = member("ad001", is_portal_admin=True)
        self.client.force_login(self.admin)
        member("ab123")                         # on the list
        member("cd456")                         # on the list, spelled @hermes there
        member("ef789")                         # wants the mailer, not on the list
        member("gh012", wants_mailer=False)     # opted out, still on the list
        member("ij345", is_banned=True)         # banned, not on the list
        member("kl678", email="kl.personal@gmail.com", crsid="kl678")  # on the list only at their old address
        OldSubscriber.objects.create(email="old.only@example.org")     # old list, not on the website

    def update(self, text, **extra):
        return self.client.post(reverse("panel:mailing_list_update"), {"members": text, **extra}, follow=True)

    def page(self, **params):
        return self.client.get(reverse("panel:mailing_list"), params)

    def test_pasting_the_list_tells_who_is_on_it(self):
        response = self.update(MAILMAN_PAGE + "\ngh012@cam.ac.uk\nkl678@cam.ac.uk\n")
        self.assertContains(response, "The site&#x27;s copy of the list has 5 addresses: 5 new")
        self.assertEqual(ListMember.objects.count(), 5)
        self.assertEqual(ListSync.objects.get().kind, ListSync.Kind.UPDATE)
        self.assertTrue(AuditLog.objects.filter(action="update_mailing_list").exists())
        counts = self.page().context["counts"]
        self.assertEqual(counts["on"], 3)        # ab123, cd456 (via @hermes), and gh012 who opted out
        self.assertEqual(counts["missing"], 3)   # ef789, the admin, and kl678 at their personal address
        self.assertEqual(counts["stopped"], 1)   # gh012
        self.assertEqual(counts["to_remove"], 1)  # gh012@cam.ac.uk
        self.assertEqual(counts["outside"], 2)   # friend@gmail.com and kl678's old Cambridge address
        self.assertEqual(counts["old_missing"], 1)
        missing = {row["user"].username for row in self.page(show="missing").context["page"].object_list}
        self.assertEqual(missing, {"ef789", "ad001", "kl678"})
        stopped = [row["user"].username for row in self.page(show="stopped").context["page"].object_list]
        self.assertEqual(stopped, ["gh012"])
        found = [row["user"].username for row in self.page(show="all", q="ef7").context["page"].object_list]
        self.assertEqual(found, ["ef789"])

    def test_before_any_update_everyone_shows_as_not_on_it(self):
        response = self.page()
        self.assertContains(response, "doesn't know who is on the list yet")
        self.assertEqual(response.context["counts"]["on"], 0)

    def test_a_much_smaller_paste_asks_first_and_part_of_the_list_adds(self):
        self.update(MAILMAN_PAGE + "\ngh012@cam.ac.uk\nkl678@cam.ac.uk\n")
        response = self.update("ab123@cam.ac.uk")
        self.assertContains(response, "only one page of the membership list")
        self.assertEqual(ListMember.objects.count(), 5)  # nothing replaced yet
        self.assertContains(response, 'name="confirm"')
        self.update("ab123@cam.ac.uk", confirm=listsync.paste_digest(["ab123@cam.ac.uk"]))
        self.assertEqual(list(ListMember.objects.values_list("email", flat=True)), ["ab123@cam.ac.uk"])
        self.update("ef789@cam.ac.uk", partial="on")  # one more page: added, nothing taken away
        self.assertEqual(ListMember.objects.count(), 2)

    def test_downloading_the_ones_to_add_then_marking_them_added(self):
        self.update(MAILMAN_PAGE + "\ngh012@cam.ac.uk\nkl678@cam.ac.uk\n")
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "add"})
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(sorted(response.content.decode().splitlines()),
                         ["ad001@cam.ac.uk", "ef789@cam.ac.uk", "kl.personal@gmail.com"])
        batch = ListSync.objects.get(kind=ListSync.Kind.ADD)
        self.assertEqual(batch.count, 3)
        page = self.page()
        self.assertContains(page, "Mark them as added")
        self.client.post(reverse("panel:mailing_list_done", args=[batch.pk]))
        self.assertEqual(self.page().context["counts"]["missing"], 0)
        self.assertEqual(ListMember.objects.get(email="ef789@cam.ac.uk").source, ListMember.Source.SITE)
        again = self.client.post(reverse("panel:mailing_list_done", args=[batch.pk]), follow=True)
        self.assertContains(again, "That was already recorded.")
        self.assertEqual(AuditLog.objects.filter(action="list_add_done").count(), 1)

    def test_the_old_list_can_be_added_too(self):
        self.update(MAILMAN_PAGE)
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "add", "old_list": "1"})
        self.assertIn("old.only@example.org", response.content.decode().splitlines())

    def test_taking_off_the_ones_who_stopped_uses_the_lists_spelling(self):
        member("mn901", wants_mailer=False)  # opted out; on the list as @hermes
        self.update(MAILMAN_PAGE + "\ngh012@cam.ac.uk\nmn901 at hermes.cam.ac.uk\n")
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "remove"})
        self.assertEqual(response.content.decode().splitlines(), ["gh012@cam.ac.uk", "mn901@hermes.cam.ac.uk"])
        batch = ListSync.objects.get(kind=ListSync.Kind.REMOVE)
        self.client.post(reverse("panel:mailing_list_done", args=[batch.pk]))
        self.assertFalse(ListMember.objects.filter(email__in=["gh012@cam.ac.uk", "mn901@hermes.cam.ac.uk"]).exists())
        self.assertEqual(self.page().context["counts"]["stopped"], 0)

    def test_a_mailbox_another_account_still_gets_is_never_taken_off(self):
        member("op111", wants_mailer=False, email="shared@example.org")
        member("op222", email="SHARED@example.org")  # same inbox, still wants the mailer
        self.update("shared@example.org\nab123@cam.ac.uk\n")
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "remove"}, follow=True)
        self.assertContains(response, "nobody to take off")

    def test_nothing_to_add_says_so(self):
        everyone = "\n".join(User.objects.exclude(email="").values_list("email", flat=True))
        self.update(everyone)
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "add"}, follow=True)
        self.assertContains(response, "nobody to add")
        self.assertFalse(ListSync.objects.filter(kind=ListSync.Kind.ADD).exists())

    def test_only_admins_and_only_by_post(self):
        plain = Client()
        plain.force_login(User.objects.get(username="ef789"))
        self.assertEqual(plain.get(reverse("panel:mailing_list")).status_code, 403)
        self.assertEqual(plain.post(reverse("panel:mailing_list_update"), {"members": "x@y.com"}).status_code, 403)
        self.assertEqual(plain.post(reverse("panel:mailing_list_download"), {"kind": "add"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("panel:mailing_list_update")).status_code, 405)
        self.assertEqual(self.client.get(reverse("panel:mailing_list_download")).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(reverse("panel:mailing_list_update"), {"members": "x@y.com"}).status_code, 403)
        self.assertFalse(ListMember.objects.exists())

    def test_the_people_tab_has_the_page(self):
        response = self.client.get(reverse("panel:members"))
        self.assertContains(response, 'href="' + reverse("panel:mailing_list") + '"')
        page = self.page()
        self.assertContains(page, 'href="https://lists.srcf.net/mailman/roster/mss-members"')
        self.assertContains(page, 'href="https://lists.srcf.net/mailman/admin/mss-members/members/add"')

    def test_an_empty_paste_changes_nothing(self):
        self.update(MAILMAN_PAGE)
        response = self.update("nothing useful here")
        self.assertContains(response, "No email addresses found")
        self.assertEqual(ListMember.objects.count(), 3)

    def test_old_batches_are_tidied_away(self):
        from io import StringIO

        from django.core.management import call_command

        old = ListSync.objects.create(kind=ListSync.Kind.ADD, count=1, addresses=["x@example.org"],
                                      created_at=timezone.now() - datetime.timedelta(days=100))
        recent = ListSync.objects.create(kind=ListSync.Kind.ADD, count=1, addresses=["y@example.org"])
        update = ListSync.objects.create(kind=ListSync.Kind.UPDATE, count=3,
                                         created_at=timezone.now() - datetime.timedelta(days=100))
        try:
            call_command("health_check", "--quick", "--prune", "--no-save", stdout=StringIO())
        except SystemExit:
            pass
        remaining = set(ListSync.objects.values_list("pk", flat=True))
        self.assertNotIn(old.pk, remaining)
        self.assertIn(recent.pk, remaining)
        self.assertIn(update.pk, remaining)

    def test_the_members_page_shows_and_filters_by_the_list(self):
        unknown = self.client.get(reverse("panel:members"), {"unlisted": "1"})
        self.assertContains(unknown, "doesn't know who is on the mailing list yet")
        self.assertNotContains(unknown, ">Not on the mailing list</span>")  # no badges before the site knows
        self.update(MAILMAN_PAGE + "\ngh012@cam.ac.uk\nkl678@cam.ac.uk\n")
        page = self.client.get(reverse("panel:members"))
        self.assertContains(page, ">On the mailing list</span>", count=2)  # ab123 and cd456
        self.assertContains(page, ">Still on the mailing list</span>", count=1)  # gh012 stopped the mailer
        unlisted = self.client.get(reverse("panel:members"), {"unlisted": "1"})
        shown = {member.username for member in unlisted.context["page"].object_list}
        self.assertEqual(shown, {"ad001", "ef789", "kl678"})
        self.assertContains(unlisted, 'href="' + reverse("panel:mailing_list") + '"')

    def test_deleting_an_account_queues_its_list_address_to_take_off(self):
        self.update(MAILMAN_PAGE + "\nef789@cam.ac.uk\n")
        gone = User.objects.get(username="ef789")
        response = self.client.post(reverse("panel:member_delete", args=[gone.pk]), follow=True)
        self.assertContains(response, "take them off there too")
        batch = ListSync.objects.get(kind=ListSync.Kind.REMOVE)
        self.assertEqual((batch.addresses, batch.note, batch.created_by),
                         (["ef789@cam.ac.uk", "ef789@hermes.cam.ac.uk"], "an account was deleted", self.admin))
        page = self.page()
        self.assertContains(page, "<code>ef789@cam.ac.uk, ef789@hermes.cam.ac.uk</code>")
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "remove"})
        self.assertEqual(response.content.decode().splitlines(), ["ef789@cam.ac.uk", "ef789@hermes.cam.ac.uk"])
        download = ListSync.objects.get(kind=ListSync.Kind.REMOVE, note="")
        self.client.post(reverse("panel:mailing_list_done", args=[download.pk]))
        self.assertFalse(ListMember.objects.filter(email="ef789@cam.ac.uk").exists())
        batch.refresh_from_db()
        self.assertIsNotNone(batch.done_at)  # the file had them, so the queued batch is done too

    def test_a_deleted_raven_account_takes_its_cambridge_address_too(self):
        self.update(MAILMAN_PAGE + "\nkl678@cam.ac.uk\n")
        gone = User.objects.get(username="kl678")
        self.client.post(reverse("panel:member_delete", args=[gone.pk]))
        self.assertEqual(ListSync.objects.get(kind=ListSync.Kind.REMOVE).addresses,
                         ["kl.personal@gmail.com", "kl678@cam.ac.uk", "kl678@hermes.cam.ac.uk"])

    def test_a_new_address_queues_the_old_one_and_shows_the_new_one_to_add(self):
        self.update(MAILMAN_PAGE)
        moving = User.objects.get(username="ab123")
        self.client.post(reverse("panel:member_edit", args=[moving.pk]), {
            "first_name": moving.first_name, "last_name": moving.last_name, "email": "ab.new@example.org",
            "college": "darwin", "mobile": "+44 7700 900100",
        })
        moving.refresh_from_db()
        self.assertEqual(moving.email, "ab.new@example.org")
        self.assertEqual(ListSync.objects.get(kind=ListSync.Kind.REMOVE).addresses,
                         ["ab123@cam.ac.uk", "ab123@hermes.cam.ac.uk"])
        missing = {row["user"].username for row in self.page(show="missing").context["page"].object_list}
        self.assertIn("ab123", missing)

    def test_the_society_s_own_addresses_on_the_list_are_flagged(self):
        self.update(MAILMAN_PAGE + "\nmaturesoc@cambridgesu.co.uk\n")
        self.assertContains(self.page(), "maturesoc@cambridgesu.co.uk is on the list.")

    def test_the_set_up_checklist_links_to_each_setting(self):
        page = self.page()
        for path in ("privacy/subscribing", "privacy/sender", "members/list", "archive", "general", "nondigest"):
            self.assertContains(page, f'href="https://lists.srcf.net/mailman/admin/mss-members/{path}"')
        self.assertContains(page, 'href="https://lists.srcf.net/mailman/admindb/mss-members"')
        self.assertContains(page, "https://lists.srcf.net/pipermail/mss-members/")
        self.assertContains(page, "mss-members-request@srcf.net")
        self.assertContains(page, "Confirm and approve")  # nobody joins by emailing the list
        self.assertContains(page, "choose Approve, and Discard the rest")  # Accept releases a sender's every post

    def test_one_page_of_a_split_membership_list_is_refused_unless_it_s_part(self):
        page = "240 members total, 30 shown\nab123@cam.ac.uk\nef789@cam.ac.uk\n"
        response = self.update(page)
        self.assertContains(response, "one page of the Membership List (30 of its 240 members)")
        self.assertFalse(ListMember.objects.exists())
        self.update(page, partial="on")
        self.assertEqual(ListMember.objects.count(), 2)

    def test_a_subscriber_list_short_of_mailman_s_count_warns(self):
        response = self.update("5 Non-digested Members of Mss-members:\n" + MAILMAN_PAGE)
        self.assertContains(response, "Mailman&#x27;s page counted 5 members, but what you pasted had 3 addresses")
        self.assertEqual(ListMember.objects.count(), 3)  # still updated
        response = self.update("4 Non-digested Members of Mss-members:\n(1 private member not shown)\n" + MAILMAN_PAGE)
        self.assertContains(response, "left out 1 member who chose to hide")

    @override_settings(DEFAULT_FROM_EMAIL="MSS <mss-admins@srcf.net>")
    def test_the_mailer_s_sending_address_on_the_list_is_flagged(self):
        self.update(MAILMAN_PAGE + "\nmss-admins at srcf.net\n")
        self.assertContains(self.page(), "mss-admins@srcf.net is on the list.")

    def test_the_copy_step_names_the_subscriber_list(self):
        page = self.page()
        self.assertContains(page, 'href="https://lists.srcf.net/mailman/roster/mss-members"')
        self.assertContains(page, 'href="https://lists.srcf.net/mailman/admin/mss-members"')
        self.assertContains(page, 'href="https://lists.srcf.net/mailman/admin/mss-members/general"')

    def test_a_member_changing_their_own_address_queues_the_old_one(self):
        self.update(MAILMAN_PAGE)
        moving = User.objects.get(username="ab123")
        self.client.force_login(moving)
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Ab", "last_name": "Tester", "college": "darwin", "course": "MBA", "bio": "",
            "talk_to_me_about": "x", "work": "x", "interests": "x", "mobile": "+44 7700 900100",
            "email": "ab.new@example.org", "wants_mailer": "on",
        })
        self.assertEqual(response.status_code, 302)
        batch = ListSync.objects.get(kind=ListSync.Kind.REMOVE)
        self.assertEqual((batch.addresses, batch.note),
                         (["ab123@cam.ac.uk", "ab123@hermes.cam.ac.uk"], "a member changed their email address"))

    def test_marking_added_copes_with_an_address_already_in_the_copy(self):
        self.update(MAILMAN_PAGE)
        self.client.post(reverse("panel:mailing_list_download"), {"kind": "add"})
        batch = ListSync.objects.get(kind=ListSync.Kind.ADD)
        ListMember.objects.create(email="ef789@cam.ac.uk", key="ef789@cam.ac.uk")  # pasted in the meantime
        response = self.client.post(reverse("panel:mailing_list_done", args=[batch.pk]), follow=True)
        self.assertContains(response, "Recorded:")
        self.assertEqual(ListMember.objects.filter(email="ef789@cam.ac.uk").count(), 1)

    def prune(self):
        from io import StringIO

        from django.core.management import call_command

        try:
            call_command("health_check", "--quick", "--prune", "--no-save", stdout=StringIO())
        except SystemExit:
            pass

    def test_addresses_owed_wait_until_a_take_off_file_has_them(self):
        old = ListSync.objects.create(kind=ListSync.Kind.REMOVE, count=12,
                                      addresses=[f"gone{n:02d}@example.org" for n in range(12)],
                                      note="unfinished accounts were deleted",
                                      created_at=timezone.now() - datetime.timedelta(days=100))
        ListSync.objects.create(kind=ListSync.Kind.REMOVE, count=1, addresses=["moved@example.org"],
                                note="a member changed their email address")
        ListMember.objects.create(email="moved@example.org", key="moved@example.org")
        page = self.page()
        self.assertContains(page, "Waiting to come off the list</strong>: 13 addresses")
        self.assertContains(page, "unfinished accounts were deleted</li>")
        self.assertContains(page, "a member changed their email address</li>")
        self.assertEqual(page.context["counts"]["to_remove"], 13)
        self.prune()
        self.assertTrue(ListSync.objects.filter(pk=old.pk).exists())  # kept until they've come off
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "remove"})
        self.assertEqual(len(response.content.decode().splitlines()), 13)
        download = ListSync.objects.get(kind=ListSync.Kind.REMOVE, note="")
        self.client.post(reverse("panel:mailing_list_done", args=[download.pk]))
        self.assertFalse(ListMember.objects.filter(email="moved@example.org").exists())
        self.assertIsNone(self.page().context["owed"])
        self.prune()
        self.assertFalse(ListSync.objects.filter(pk=old.pk).exists())  # done, and old: tidied away

    def test_a_download_never_used_can_be_forgotten(self):
        self.update(MAILMAN_PAGE)
        self.client.post(reverse("panel:mailing_list_download"), {"kind": "add"})
        batch = ListSync.objects.get(kind=ListSync.Kind.ADD)
        self.assertContains(self.page(), "Not used</button>")
        self.client.post(reverse("panel:mailing_list_forget", args=[batch.pk]))
        self.assertFalse(ListSync.objects.filter(pk=batch.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="forget_list_download").exists())
        owed = ListSync.objects.create(kind=ListSync.Kind.REMOVE, count=1, addresses=["x@example.org"],
                                       note="an account was deleted")
        self.assertEqual(self.client.post(reverse("panel:mailing_list_forget", args=[owed.pk])).status_code, 404)

    def test_every_way_of_deleting_an_account_queues_its_address(self):
        # before the site has any copy of the list, as the Django admin or a shell would
        User.objects.get(username="ef789").delete()
        batch = ListSync.objects.get(kind=ListSync.Kind.REMOVE)
        self.assertEqual((batch.addresses, batch.created_by), (["ef789@cam.ac.uk", "ef789@hermes.cam.ac.uk"], None))
        with listsync.deleting(self.admin, "unfinished accounts were deleted") as queued:
            User.objects.filter(username__in=["ab123", "cd456"]).delete()
        self.assertEqual(queued, ["ab123@cam.ac.uk", "ab123@hermes.cam.ac.uk", "cd456@cam.ac.uk",
                                  "cd456@hermes.cam.ac.uk"])
        self.assertEqual(ListSync.objects.filter(kind=ListSync.Kind.REMOVE).count(), 2)  # one batch for the two
        with self.assertRaises(RuntimeError):
            with listsync.deleting(self.admin, "never finished"):
                User.objects.get(username="gh012").delete()
                raise RuntimeError
        self.assertEqual(ListSync.objects.filter(kind=ListSync.Kind.REMOVE).count(), 2)  # nothing for a failure

    def test_an_old_address_the_list_never_had_isn_t_queued(self):
        self.update(MAILMAN_PAGE)
        moving = User.objects.get(username="ef789")  # not on the list
        self.client.force_login(moving)
        self.client.post(reverse("accounts:profile"), {
            "first_name": "Ef", "last_name": "Tester", "college": "darwin", "course": "MBA", "bio": "",
            "talk_to_me_about": "x", "work": "x", "interests": "x", "mobile": "+44 7700 900100",
            "email": "ef.new@example.org", "wants_mailer": "on",
        })
        moving.refresh_from_db()
        self.assertEqual(moving.email, "ef.new@example.org")
        self.assertFalse(ListSync.objects.filter(kind=ListSync.Kind.REMOVE).exists())

    def test_old_list_people_who_unsubscribe_come_off_too(self):
        from django.core.cache import cache

        cache.clear()  # the unsubscribe page's rate limit lives in the cache
        self.update(MAILMAN_PAGE + "\nold.only@example.org\n")
        self.client.post(reverse("core:unsubscribe"), {"email": "old.only@example.org"})
        self.assertEqual(self.page().context["counts"]["to_remove"], 1)
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "remove"})
        self.assertEqual(response.content.decode().splitlines(), ["old.only@example.org"])
        self.assertFalse(ListSync.objects.filter(kind=ListSync.Kind.REMOVE).exclude(note="").exists())

    def test_someone_only_on_the_list_who_unsubscribes_waits_to_come_off(self):
        from django.core.cache import cache

        cache.clear()  # the unsubscribe page's rate limit lives in the cache
        from django.core import mail

        self.update(MAILMAN_PAGE)  # friend@gmail.com is on the list and nowhere else
        self.client.post(reverse("core:unsubscribe"), {"email": "Friend@Gmail.com"})
        batch = ListSync.objects.get(kind=ListSync.Kind.REMOVE)
        self.assertEqual((batch.addresses, batch.created_by), (["friend@gmail.com"], None))
        self.assertEqual(mail.outbox[-1].to, ["friend@gmail.com"])
        self.assertIn("If that wasn't you", mail.outbox[-1].body)
        self.client.post(reverse("core:unsubscribe"), {"email": "friend@gmail.com"})
        self.client.post(reverse("core:unsubscribe"), {"email": "stranger@example.org"})  # not on the list
        self.assertEqual(ListSync.objects.filter(kind=ListSync.Kind.REMOVE).count(), 1)

    def test_a_shared_mailbox_still_reached_isn_t_counted_to_take_off(self):
        member("tw001", email="tw001@hermes.cam.ac.uk", wants_mailer=False)
        member("tw002", email="tw001@cam.ac.uk")  # the same mailbox, and it wants the mailer
        self.update(MAILMAN_PAGE + "\ntw001@hermes.cam.ac.uk\n")
        counts = self.page().context["counts"]
        self.assertEqual((counts["stopped"], counts["to_remove"]), (0, 0))
        self.assertEqual(list(self.page(show="stopped").context["page"].object_list), [])

    def test_the_confirm_page_s_links_go_to_the_mailing_list_page(self):
        self.update(MAILMAN_PAGE + "\ngh012@cam.ac.uk\nkl678@cam.ac.uk\n")
        response = self.update("ab123@cam.ac.uk")
        self.assertContains(response, 'name="confirm"')
        self.assertNotContains(response, 'href="?')
        self.assertContains(response, 'href="' + reverse("panel:mailing_list") + '?show=on"')
        self.assertContains(response, 'action="' + reverse("panel:mailing_list") + '"')

    def test_an_address_the_old_list_still_reaches_isn_t_taken_off(self):
        OldSubscriber.objects.create(email="cd456@cam.ac.uk")  # cd456 was on the old list too
        with listsync.deleting(self.admin, "unfinished accounts were deleted") as queued:
            User.objects.get(username="cd456").delete()
        self.assertEqual(queued, [])  # the old list's mailer still writes to it

    def test_an_owed_address_wanted_again_drops_out(self):
        self.update(MAILMAN_PAGE + "\nef789@cam.ac.uk\n")
        User.objects.get(username="ef789").delete()
        self.assertContains(self.page(), "<code>ef789@cam.ac.uk, ef789@hermes.cam.ac.uk</code>")
        member("ef789")  # back, with the same address, and wants the mailer
        self.assertIsNone(self.page().context["owed"])
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "remove"}, follow=True)
        self.assertContains(response, "There&#x27;s nobody to take off.")

    def test_marking_a_take_off_file_done_closes_only_what_it_had(self):
        self.update(MAILMAN_PAGE + "\nef789@cam.ac.uk\nkl678@cam.ac.uk\n")
        User.objects.get(username="ef789").delete()
        response = self.client.post(reverse("panel:mailing_list_download"), {"kind": "remove"})
        self.assertEqual(response.content.decode().splitlines(), ["ef789@cam.ac.uk", "ef789@hermes.cam.ac.uk"])
        download = ListSync.objects.get(kind=ListSync.Kind.REMOVE, note="")
        User.objects.get(username="kl678").delete()  # queued after the file was downloaded
        done = self.client.post(reverse("panel:mailing_list_done", args=[download.pk]), follow=True)
        self.assertContains(done, "Recorded: the 2 addresses in that file are off the list now.")
        self.assertFalse(ListMember.objects.filter(email="ef789@cam.ac.uk").exists())
        self.assertTrue(ListMember.objects.filter(email="kl678@cam.ac.uk").exists())
        page = self.page()
        self.assertEqual(page.context["owed"]["addresses"],
                         ["kl.personal@gmail.com", "kl678@cam.ac.uk", "kl678@hermes.cam.ac.uk"])
        self.assertContains(page, "Since then, 1 taken off here.")

    def test_confirming_a_small_paste_confirms_that_paste_only(self):
        self.update(MAILMAN_PAGE + "\ngh012@cam.ac.uk\nkl678@cam.ac.uk\n")
        digest = self.update("ab123@cam.ac.uk").context["form"].initial["confirm"]
        self.update("zz005@cam.ac.uk", confirm=digest)  # another paste, sent with the first one's confirmation
        self.assertEqual(ListMember.objects.count(), 5)  # asked again; nothing replaced
        self.update("ab123@cam.ac.uk", confirm=digest)
        self.assertEqual(list(ListMember.objects.values_list("email", flat=True)), ["ab123@cam.ac.uk"])

    def test_part_of_the_list_doesn_t_count_as_a_fresh_copy(self):
        self.update(MAILMAN_PAGE)
        long_ago = timezone.now() - datetime.timedelta(days=60)
        ListSync.objects.filter(kind=ListSync.Kind.UPDATE).update(created_at=long_ago)
        ListMember.objects.update(added_at=long_ago)
        self.update("ef789@cam.ac.uk", partial="on")
        page = self.page()
        self.assertTrue(page.context["update_old"])
        self.assertContains(page, "Part of the list was pasted on")
        self.assertContains(page, "Since then, 1 added here.")

    def test_the_members_page_warns_only_about_what_would_come_off(self):
        member("op111", email="shared@example.org", wants_mailer=False)
        member("op222", email="shared@example.org")  # the same inbox still gets the mailer
        self.update(MAILMAN_PAGE + "\nshared@example.org\n")
        page = self.client.get(reverse("panel:members"))
        self.assertNotContains(page, ">Still on the mailing list</span>")
        self.assertContains(page, ">On the mailing list</span>")

    def test_a_member_who_moved_has_their_old_list_address_taken_off(self):
        mover = member("mv123", crsid="mv123")
        OldSubscriber.objects.create(email="mv123@cam.ac.uk")  # on the old list too
        self.update(MAILMAN_PAGE + "\nmv123@cam.ac.uk\n")
        self.client.post(reverse("panel:member_edit", args=[mover.pk]), {
            "first_name": mover.first_name, "last_name": mover.last_name, "email": "mv.new@example.org",
            "college": "darwin", "mobile": "+44 7700 900100",
        })
        # the old-list entry is theirs, and they get the mail at the new address, so the old one comes off
        self.assertEqual(ListSync.objects.get(kind=ListSync.Kind.REMOVE).addresses,
                         ["mv123@cam.ac.uk", "mv123@hermes.cam.ac.uk"])

    def test_each_card_has_its_own_done_button(self):
        self.update(MAILMAN_PAGE)
        page = self.page()
        self.assertContains(page, '<button type="button" class="btn btn-small" disabled>Mark them as added</button>',
                            html=True)
        self.assertContains(page, '<details class="card ml-guide">', count=2)  # the guides start closed
        self.client.post(reverse("panel:mailing_list_download"), {"kind": "add"})
        first = ListSync.objects.get(kind=ListSync.Kind.ADD)
        self.client.post(reverse("panel:mailing_list_download"), {"kind": "add"})
        second = ListSync.objects.get(kind=ListSync.Kind.ADD)  # a new download replaces an unmarked one
        self.assertNotEqual(first.pk, second.pk)
        page = self.page()
        self.assertContains(page, 'action="' + reverse("panel:mailing_list_done", args=[second.pk]) + '"')
        self.assertContains(page, "Ready once you've downloaded the file.", count=1)  # only the take-off card waits
        self.assertContains(page, 'href="' + reverse("panel:old_list") + '"')

    def test_a_download_replaces_only_one_it_covers_and_a_whole_paste_clears_them(self):
        download = reverse("panel:mailing_list_download")
        self.update(MAILMAN_PAGE)
        self.client.post(download, {"kind": "add", "old_list": "1"})  # with old.only@example.org too
        wider = ListSync.objects.get(kind=ListSync.Kind.ADD)
        self.client.post(download, {"kind": "add"})
        self.assertTrue(ListSync.objects.filter(pk=wider.pk).exists())  # it had more, so it stays
        self.client.post(download, {"kind": "add"})
        self.assertEqual(ListSync.objects.filter(kind=ListSync.Kind.ADD).count(), 2)  # the same again replaced
        self.update(MAILMAN_PAGE)  # the whole list, pasted again
        self.assertFalse(ListSync.objects.filter(kind=ListSync.Kind.ADD, done_at__isnull=True).exists())
