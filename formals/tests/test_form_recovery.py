"""Rejected exchange changes keep members' drafts without mutating the ledger."""
from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from formals import services


class FormalFormRecoveryTests(TestCase):
    def setUp(self):
        def member(name, college="wolfson", **extra):
            return User.objects.create_user(
                username=name, first_name=name.title(), last_name="Member",
                college=college, mobile="07700900000", terms_accepted_version=999,
                **extra,
            )

        self.host = member("recoveryhost")
        self.guest = member("recoveryguest", "darwin")
        self.admin = member("recoveryadmin", is_portal_admin=True)
        self.other = member("recoveryother")
        start = timezone.now() + timedelta(days=7)
        self.listing = services.create_listing(
            self.host, title="Recovery dinner", college="wolfson", start=start,
            end=start + timedelta(hours=2), location="Lodge", capacity=3,
            booking_closes_at=start - timedelta(days=1), price_text="18 pounds",
            invitation_type="reciprocal", booking_mode="instant",
            hosting_confirmed=True, return_by=timezone.localdate(start + timedelta(days=45)),
        )
        self.booking = services.apply_for_place(
            self.guest, self.listing.pk, self.listing.terms_version,
            return_college="darwin", can_host=True, accept_terms=True,
        )
        self.swap = self.booking.exchange

    def action_url(self, action):
        return reverse("formals:exchange_action", args=[self.swap.pk, action])

    def test_long_reasons_keep_the_draft_and_do_not_change_the_exchange(self):
        draft = "<script>draft only</script>\n" + "More detail. " * 100
        before = self.swap.activity.count()
        for action, actor, form_name, field, extra, disclosure in (
            ("issue", self.guest, "issue_form", "reason", {}, "Ask the committee for help"),
            ("waive", self.host, "reason_form", "reason", {}, "Release the return promise"),
            ("resolve", self.admin, "resolution_form", "note", {"resolution": "waive"}, "Committee decision"),
        ):
            with self.subTest(action=action):
                self.client.force_login(actor)
                response = self.client.post(self.action_url(action), {field: draft, **extra})
                self.assertEqual(response.status_code, 400)
                form = response.context[form_name]
                self.assertEqual(form[field].value(), draft)
                self.assertIn(field, form.errors)
                self.assertContains(response, "&lt;script&gt;draft only&lt;/script&gt;", status_code=400)
                self.assertNotContains(response, "<script>draft only</script>", status_code=400)
                self.assertContains(response, f"<details open><summary>{disclosure}</summary>", status_code=400)
                self.swap.refresh_from_db()
                self.assertEqual(self.swap.status, "conditional")
                self.assertEqual(self.swap.activity.count(), before)

    def test_review_reason_can_be_corrected_and_submitted_once(self):
        self.client.force_login(self.guest)
        self.client.post(self.action_url("issue"), {"reason": "x" * 1001})
        response = self.client.post(self.action_url("issue"), {"reason": "Please help arrange the return date."})
        self.assertRedirects(response, self.swap.get_absolute_url())
        self.swap.refresh_from_db()
        self.assertEqual(self.swap.status, "disputed")
        self.assertEqual(self.swap.activity.filter(action="issue_reported").count(), 1)

    def test_invalid_and_rejected_deadlines_preserve_the_submitted_value(self):
        self.client.force_login(self.guest)
        original_deadline = self.swap.return_by
        for value, service_error in (("not-a-date", False), (original_deadline.isoformat(), True)):
            with self.subTest(value=value):
                response = self.client.post(self.action_url("extension"), {"return_by": value})
                self.assertEqual(response.status_code, 400)
                form = response.context["extension_form"]
                self.assertEqual(form["return_by"].value(), value)
                self.assertTrue(form.non_field_errors() if service_error else form["return_by"].errors)
                self.assertContains(response, f'value="{value}"', status_code=400)
                self.assertContains(response, "<details open><summary>Change the deadline</summary>", status_code=400)
                self.swap.refresh_from_db()
                self.assertEqual(self.swap.return_by, original_deadline)
                self.assertIsNone(self.swap.extension_date)
        later = original_deadline + timedelta(days=7)
        response = self.client.post(self.action_url("extension"), {"return_by": later.isoformat()})
        self.assertRedirects(response, self.swap.get_absolute_url())
        self.swap.refresh_from_db()
        self.assertEqual(self.swap.extension_date, later)
        self.assertEqual(self.swap.return_by, original_deadline)

    def test_rejected_committee_decision_keeps_reason_and_explains_failure(self):
        self.client.force_login(self.admin)
        reason = "Members asked us to close this after the dinner."
        response = self.client.post(self.action_url("resolve"), {"resolution": "complete", "note": reason})
        self.assertEqual(response.status_code, 400)
        form = response.context["resolution_form"]
        self.assertEqual(form["note"].value(), reason)
        self.assertEqual(form["resolution"].value(), "complete")
        self.assertTrue(form.non_field_errors())
        self.assertContains(response, "Confirm the first dinner and every agreed return place", status_code=400)
        self.assertContains(response, reason, status_code=400)
        self.swap.refresh_from_db()
        self.assertEqual(self.swap.status, "conditional")

    def test_recovery_does_not_grant_exchange_or_committee_access(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.action_url("issue"), {"reason": "x" * 1001}).status_code, 404)
        self.client.force_login(self.guest)
        self.assertEqual(self.client.post(self.action_url("resolve"), {"resolution": "waive", "note": "x" * 1001}).status_code, 403)
        response = self.client.post(self.action_url("waive"), {"reason": "Please release this."})
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, "Waive the promise</button>", status_code=400)
        self.swap.refresh_from_db()
        self.assertEqual(self.swap.status, "conditional")

    def test_invalid_formal_cancellation_preserves_reason_without_cancelling(self):
        self.client.force_login(self.host)
        draft = "Dining arrangements changed. " * 50
        response = self.client.post(reverse("formals:cancel", args=[self.listing.pk]), {"reason": draft})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["reason_form"]["reason"].value(), draft)
        self.assertContains(response, "<details open><summary>Cancel this formal</summary>", status_code=400)
        self.listing.event.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertFalse(self.listing.event.is_cancelled)
        self.assertEqual(self.booking.status, "reserved")

    def test_return_candidates_include_the_last_london_evening_only(self):
        with timezone.override("Europe/London"):
            deadline = date(timezone.localdate().year + 1, 7, 1)
            self.swap.return_by = deadline
            self.swap.save(update_fields=["return_by"])
            last_evening = timezone.make_aware(datetime.combine(deadline, time(23, 30)))
            candidates = []
            for title, start in (("Last evening", last_evening), ("Following morning", last_evening + timedelta(hours=1))):
                candidates.append(services.create_listing(
                    self.guest, title=title, college="darwin", start=start,
                    end=start + timedelta(hours=2), location="Lodge", capacity=3,
                    booking_closes_at=start - timedelta(days=1), price_text="18 pounds",
                    invitation_type="open", booking_mode="instant", hosting_confirmed=True,
                ))
            self.client.force_login(self.guest)
            response = self.client.get(self.swap.get_absolute_url())
            choices = response.context["proposal_form"].fields["listing_id"].choices
            self.assertEqual([value for value, label in choices], [candidates[0].pk])
