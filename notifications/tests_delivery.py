from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.core import mail
from django.core.management import call_command, CommandError
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from panel.models import JobHeartbeat
from notifications.delivery import _claim, deliver_emails, retry_failed
from notifications.models import Notification, NotificationEmail
from notifications.services import notify, queue_email


class NotificationDeliveryTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="recipient", email="recipient@example.test")
        mail.outbox = []

    def queued(self):
        return queue_email([self.member], "A place is ready", "Your place is confirmed.")[0]

    def test_rollback_leaves_neither_notification_nor_email(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            notify([self.member], "event", "Confirmed", email_subject="Confirmed")
            raise RuntimeError("The booking did not commit")
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(NotificationEmail.objects.exists())
        self.assertEqual(mail.outbox, [])

    @override_settings(SITE_URL="https://portal.example.test")
    def test_email_has_an_absolute_link_back_to_the_action(self):
        notify([self.member], "event", "Moved", "/events/dinner/", email_subject="Dinner moved", email_body="The dinner is now at 7pm.")
        self.assertIn("https://portal.example.test/events/dinner/", NotificationEmail.objects.get().body)
        self.assertEqual(Notification.objects.get().url, "/events/dinner/")

    def test_private_delivery_and_repeat_worker_does_not_resend(self):
        other = User.objects.create_user(username="other", email="other@example.test")
        queue_email([self.member, self.member, other], "Update\nfor members", "Details")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(deliver_emails(), 2)
        self.assertEqual(deliver_emails(), 0)
        self.assertEqual({tuple(message.to) for message in mail.outbox}, {(self.member.email,), (other.email,)})
        self.assertTrue(all(not message.cc and not message.bcc for message in mail.outbox))
        self.assertTrue(all(message.subject == "Update for members" for message in mail.outbox))

    def test_delivery_failure_retries_without_repeating_notification(self):
        notify([self.member], "event", "Confirmed", email_subject="Confirmed")
        with patch("notifications.delivery.send_mail", side_effect=OSError("secret SMTP details")):
            self.assertEqual(deliver_emails(), 0)
        job = NotificationEmail.objects.get()
        self.assertEqual(job.last_error, "OSError")
        self.assertEqual(job.status, "pending")
        self.assertGreater(job.available_at, timezone.now())
        self.assertEqual(deliver_emails(), 0)
        NotificationEmail.objects.update(available_at=timezone.now())
        self.assertEqual(deliver_emails(), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_zero_return_is_a_failure_and_exhausted_attempts_are_visible(self):
        job = self.queued()
        for attempt in range(5):
            NotificationEmail.objects.filter(pk=job.pk).update(available_at=timezone.now())
            with patch("notifications.delivery.send_mail", return_value=0):
                self.assertEqual(deliver_emails(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNone(job.sent_at)
        self.assertEqual(job.attempts, 5)
        self.assertEqual(deliver_emails(), 0)
        self.assertEqual(retry_failed(), 1)
        self.assertEqual(deliver_emails(), 1)

    def test_live_lease_cannot_be_claimed_twice_and_expired_lease_recovers(self):
        job = self.queued()
        first = _claim()
        self.assertEqual(first.pk, job.pk)
        self.assertIsNone(_claim())
        NotificationEmail.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))
        second = _claim()
        self.assertEqual(second.pk, first.pk)
        self.assertNotEqual(second.claim_token, first.claim_token)
        self.assertEqual(second.attempts, 2)

    def test_stale_worker_cannot_acknowledge_another_workers_lease(self):
        job = self.queued()
        replacement = uuid4()
        def reclaimed(*args, **kwargs):
            NotificationEmail.objects.filter(pk=job.pk).update(claim_token=replacement)
            return 1
        with patch("notifications.delivery.send_mail", side_effect=reclaimed):
            self.assertEqual(deliver_emails(), 0)
        job.refresh_from_db()
        self.assertEqual(job.claim_token, replacement)
        self.assertIsNone(job.sent_at)

    def test_crash_on_final_attempt_becomes_failed_after_lease(self):
        job = self.queued()
        NotificationEmail.objects.filter(pk=job.pk).update(status="sending", attempts=5, available_at=timezone.now() - timedelta(minutes=1))
        self.assertIsNone(_claim())
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.last_error, "DeliveryInterrupted")

    def test_recipient_suspended_after_enqueue_is_not_mailed(self):
        job = self.queued()
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        self.assertEqual(deliver_emails(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "skipped")
        self.assertEqual(mail.outbox, [])

    def test_command_records_success_failure_and_explicit_retry(self):
        job = self.queued()
        NotificationEmail.objects.filter(pk=job.pk).update(status="failed", attempts=5)
        with self.assertRaises(CommandError):
            call_command("process_notifications", quiet=True)
        self.assertFalse(JobHeartbeat.objects.get(name="process_notifications").last_ok)
        call_command("process_notifications", retry_failed=True, stdout=StringIO())
        self.assertTrue(JobHeartbeat.objects.get(name="process_notifications").last_ok)
        job.refresh_from_db()
        self.assertEqual(job.status, "sent")

    def test_view_only_admin_cannot_retry_email_delivery(self):
        from django.contrib import admin
        from django.contrib.auth.models import Permission
        from django.test import RequestFactory
        from notifications.admin import NotificationEmailAdmin

        viewer = User.objects.create_user(username="mail-reviewer", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(
            content_type__app_label="notifications", codename="view_notificationemail",
        ))
        request = RequestFactory().get("/dj-admin/notifications/notificationemail/")
        request.user = viewer
        model_admin = NotificationEmailAdmin(NotificationEmail, admin.site)
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertNotIn("retry_delivery", model_admin.get_actions(request))

        viewer.user_permissions.add(Permission.objects.get(
            content_type__app_label="notifications", codename="change_notificationemail",
        ))
        request.user = User.objects.get(pk=viewer.pk)
        self.assertIn("retry_delivery", model_admin.get_actions(request))
