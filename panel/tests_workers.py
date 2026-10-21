"""Background work must expose stopped delivery and genuine send failures."""
from datetime import timedelta
import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from formals.models import FormalEmail
from panel.healthchecks import checks
from panel.healthchecks.core import Context
from panel.models import JobHeartbeat


class DeliveryMonitoringTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="queue-member", email="queue@example.test")

    def context(self, production=True):
        context = Context(full=False)
        context.production = production
        return context

    def healthy_beats(self):
        for name in ("close_polls", "process_formals", "process_notifications"):
            JobHeartbeat.objects.create(name=name, last_finished=timezone.now(), last_ok=True)

    def test_stopped_failed_or_unfinished_email_workers_are_not_healthy(self):
        self.healthy_beats()
        self.assertEqual(checks.scheduled_jobs(self.context()).status, "ok")
        for name in ("process_formals", "process_notifications"):
            for change in (
                {"last_finished": timezone.now() - timedelta(minutes=20)},
                {"last_ok": False},
                {"last_started": timezone.now() - timedelta(minutes=20), "last_finished": timezone.now() - timedelta(minutes=30)},
            ):
                JobHeartbeat.objects.filter(name=name).update(last_finished=timezone.now(), last_ok=True, last_started=None)
                JobHeartbeat.objects.filter(name=name).update(**change)
                self.assertEqual(checks.scheduled_jobs(self.context()).status, "warn")
            JobHeartbeat.objects.filter(name=name).update(last_finished=timezone.now(), last_ok=True, last_started=None)

    def test_missing_delivery_workers_warn_only_on_established_production_copy(self):
        JobHeartbeat.objects.create(name="close_polls", last_finished=timezone.now(), last_ok=True)
        with patch("panel.healthchecks.checks._installed_lately", return_value=False):
            result = checks.scheduled_jobs(self.context())
            self.assertEqual(result.status, "warn")
            self.assertIn("member notification emails", result.summary)
            self.assertIn("formal invitations", result.summary)
        self.assertEqual(checks.scheduled_jobs(self.context(False)).status, "ok")

    def test_formal_worker_records_success_failure_and_exhausted_retries(self):
        with patch("formals.management.commands.process_formals.expire_proposals", return_value=2), patch("formals.management.commands.process_formals.deliver_emails", return_value=3):
            call_command("process_formals", quiet=True, stdout=io.StringIO())
        beat = JobHeartbeat.objects.get(name="process_formals")
        self.assertTrue(beat.last_ok)
        self.assertIn("delivered 3", beat.detail)
        FormalEmail.objects.create(recipient=self.member, subject="Update", body="Private body", attempts=5)
        with patch("formals.management.commands.process_formals.deliver_emails", return_value=0):
            with self.assertRaises(CommandError):
                call_command("process_formals", quiet=True, stdout=io.StringIO())
        beat.refresh_from_db()
        self.assertFalse(beat.last_ok)
        self.assertNotIn("Private body", beat.last_error)

    def test_pending_and_failed_formal_emails_are_visible_without_content(self):
        job = FormalEmail.objects.create(recipient=self.member, subject="Private subject", body="Private body")
        self.assertEqual(checks.queued_messages(self.context()).status, "ok")
        FormalEmail.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(checks.queued_messages(self.context()).status, "warn")
        self.assertEqual(checks.queued_messages(self.context(False)).status, "off")
        FormalEmail.objects.filter(pk=job.pk).update(attempts=5)
        result = checks.queued_messages(self.context())
        self.assertEqual(result.status, "warn")
        self.assertIn("repeated attempts", result.summary)
        self.assertNotIn("Private", str(result))
        self.assertNotIn(self.member.email, str(result))

    def test_notification_queue_failures_and_old_leases_are_visible(self):
        from notifications.models import NotificationEmail

        job = NotificationEmail.objects.create(recipient=self.member, subject="Private subject", body="Private body")
        NotificationEmail.objects.filter(pk=job.pk).update(status="sending", created_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(checks.queued_messages(self.context()).status, "warn")
        NotificationEmail.objects.filter(pk=job.pk).update(status="failed", attempts=5)
        self.assertIn("repeated attempts", checks.queued_messages(self.context()).summary)
