"""Opt-in publication alerts, matching, privacy and deduplicated delivery."""
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from formals import services
from formals.alerts import notify_matching_members
from formals.models import FormalAlert, FormalAlertDelivery, FormalEmail, FormalListing
from formals.tests.test_views import member
from inbox.models import MessageBlock
from notifications.models import Notification


class FormalAlertTests(TestCase):
    def setUp(self):
        self.host = member("alerthost")
        self.guest = member("alertguest", "darwin")
        self.other = member("alertother", "clare")
        self.start = (timezone.now() + timedelta(days=7)).replace(second=0, microsecond=0)
        self.dinner_date = timezone.localdate(self.start)
        self.url = reverse("formals:alerts")
        self.save_url = reverse("formals:save_alert")

    def listing(self, **changes):
        data = {
            "title": "New matching formal", "location": "College lodge", "college": self.host.college,
            "start": self.start, "end": self.start + timedelta(hours=2), "capacity": 3,
            "invitation_type": "open", "booking_mode": "instant", "allowed_return_colleges": [],
            "booking_closes_at": self.start - timedelta(days=1), "return_by": None,
            "price_text": "£20", "hosting_confirmed": True,
        }
        data.update(changes)
        if data["invitation_type"] == "reciprocal":
            data["return_by"] = self.dinner_date + timedelta(days=45)
        return services.create_listing(self.host, **data)

    def alert(self, user=None, **changes):
        return FormalAlert.objects.create(member=user or self.guest, **changes)

    def test_member_saves_filters_without_notifying_about_existing_formals(self):
        self.listing()
        self.client.force_login(self.guest)
        response = self.client.post(self.save_url, {
            "college": "wolfson", "invitation_type": "open", "booking_mode": "instant",
            "after": self.dinner_date, "before": self.dinner_date, "min_places": 2, "next": "browse",
        })
        self.assertRedirects(response, reverse("formals:index"))
        alert = FormalAlert.objects.get(member=self.guest)
        self.assertEqual(alert.min_places, 2)
        self.assertEqual(alert.after, self.dinner_date)
        self.assertEqual(alert.before, self.dinner_date)
        self.assertFalse(FormalAlertDelivery.objects.exists())
        self.assertFalse(FormalEmail.objects.exists())
        self.assertContains(self.client.get(self.url), "Wolfson")

    def test_overlapping_alerts_and_repeated_hook_queue_only_once(self):
        self.alert()
        self.alert(college="wolfson", min_places=2)
        with patch("formals.services.send_mail") as smtp:
            listing = self.listing()
            self.assertEqual(notify_matching_members(listing), 0)
            smtp.assert_not_called()
        self.assertEqual(FormalAlertDelivery.objects.filter(member=self.guest).count(), 1)
        self.assertEqual(FormalEmail.objects.filter(recipient=self.guest).count(), 1)
        note = Notification.objects.get(recipient=self.guest)
        self.assertEqual(note.url, listing.get_absolute_url())
        self.assertIn("matches your alert", note.text)

    def test_all_filters_and_inclusive_local_dates_must_match(self):
        mismatches = [
            {"college": "darwin"}, {"invitation_type": "reciprocal"}, {"booking_mode": "approval"},
            {"min_places": 3}, {"after": self.dinner_date + timedelta(days=1)},
            {"before": self.dinner_date - timedelta(days=1)}, {"active": False},
        ]
        for i, preference in enumerate(mismatches):
            self.alert(member(f"mismatch{i}"), **preference)
        self.alert(college="wolfson", invitation_type="open", booking_mode="instant", min_places=2, after=self.dinner_date, before=self.dinner_date)
        self.listing(capacity=2)
        self.assertEqual(list(FormalAlertDelivery.objects.values_list("member_id", flat=True)), [self.guest.pk])

    def test_ineligible_suspended_shadow_blocked_and_own_accounts_are_excluded(self):
        self.alert(self.host)
        for name, extra in (("inactivealert", {"is_active": False}), ("bannedalert", {"is_banned": True}), ("shadowalert", {"is_shadow_banned": True})):
            self.alert(member(name, "darwin", **extra))
        self.alert(member("noncollegealert", "other"))
        self.alert(self.other)  # Clare cannot offer the requested Darwin return.
        blocked = member("blockedalert", "darwin")
        self.alert(blocked)
        MessageBlock.objects.create(user=blocked, blocked=self.host)
        self.alert()
        self.listing(invitation_type="reciprocal", allowed_return_colleges=["darwin"])
        self.assertEqual(list(FormalAlertDelivery.objects.values_list("member_id", flat=True)), [self.guest.pk])

    def test_shadow_banned_host_does_not_generate_publication_alerts(self):
        self.alert()
        self.host.is_shadow_banned = True
        self.host.save(update_fields=["is_shadow_banned"])
        self.listing()
        self.assertFalse(FormalAlertDelivery.objects.exists())

    def test_alert_pages_are_private_and_preferences_are_owned_by_member(self):
        alert = self.alert()
        delete = reverse("formals:delete_alert", args=[alert.pk])
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.save_url, {}).status_code, 302)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.url), alert.description)
        self.assertEqual(self.client.post(delete).status_code, 404)
        self.assertTrue(FormalAlert.objects.filter(pk=alert.pk).exists())
        self.client.force_login(self.guest)
        response = self.client.get(self.url)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(self.client.get(self.save_url).status_code, 405)
        self.assertEqual(self.client.get(delete).status_code, 405)
        for change in ({"is_banned": True}, {"is_banned": False, "is_active": False}):
            for name, value in change.items():
                setattr(self.guest, name, value)
            self.guest.save(update_fields=list(change))
            self.client.force_login(self.guest)
            self.assertNotEqual(self.client.get(self.url).status_code, 200)
            before = FormalAlert.objects.count()
            self.client.post(self.save_url, {"college": "clare"})
            self.assertEqual(FormalAlert.objects.count(), before)

    def test_mutation_requires_csrf_and_pause_resume_remove_work(self):
        alert = self.alert()
        url = reverse("formals:delete_alert", args=[alert.pk])
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.guest)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertEqual(csrf_client.post(self.save_url, {}).status_code, 403)
        self.client.force_login(self.guest)
        self.client.post(url, {"action": "pause"})
        self.listing()
        self.assertFalse(FormalAlertDelivery.objects.exists())
        self.client.post(url, {"action": "resume"})
        self.listing()
        self.assertEqual(FormalAlertDelivery.objects.count(), 1)
        self.client.post(url, {"action": "delete"})
        self.assertFalse(FormalAlert.objects.filter(pk=alert.pk).exists())

    def test_invalid_filters_create_nothing_and_missing_optional_fields_default(self):
        self.client.force_login(self.guest)
        for data in ({"college": "unknown"}, {"min_places": 4}, {"before": "bad-date"}, {"after": self.dinner_date, "before": self.dinner_date - timedelta(days=1)}):
            self.assertEqual(self.client.post(self.save_url, data).status_code, 400)
        self.assertFalse(FormalAlert.objects.exists())
        self.assertEqual(self.client.post(self.save_url, {}).status_code, 302)
        self.assertEqual(FormalAlert.objects.get().min_places, 1)
        self.assertEqual(self.client.post(self.save_url, {}).status_code, 302)
        self.assertEqual(FormalAlert.objects.count(), 1)

    def test_active_alert_limit_applies_to_creating_and_resuming(self):
        for i in range(20):
            self.alert(after=self.dinner_date + timedelta(days=i))
        paused = self.alert(active=False)
        self.client.force_login(self.guest)
        self.assertEqual(self.client.post(self.save_url, {}).status_code, 400)
        self.client.post(reverse("formals:delete_alert", args=[paused.pk]), {"action": "resume"})
        paused.refresh_from_db()
        self.assertFalse(paused.active)
        self.assertEqual(FormalAlert.objects.filter(active=True).count(), 20)

    def test_failed_publication_transaction_does_not_leave_delivery_marker(self):
        self.alert()
        with patch("formals.services._tell", side_effect=RuntimeError("queue failed")):
            with self.assertRaises(RuntimeError):
                self.listing()
        self.assertFalse(FormalListing.objects.exists())
        self.assertFalse(FormalAlertDelivery.objects.exists())
