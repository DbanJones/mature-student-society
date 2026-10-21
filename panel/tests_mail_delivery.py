"""Mail acceptance is checked and concurrent sends claim a durable guard."""
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock, patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import close_old_connections, connection
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature

from accounts.models import User
from core.models import SiteConfig
from panel import services, views
from panel.forms import MailerForm
from panel.models import MailLog, OldSubscriber


def send_request(user):
    data = {"audience": "address", "recipient": "list@example.test", "subject": "Member news", "body": "Hello", "action": "send"}
    request = RequestFactory().post("/admin/mailer/", data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    form = MailerForm(data)
    assert form.is_valid(), form.errors
    return request, form


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MailAcceptanceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="mailchecker", email="admin@example.test", is_portal_admin=True)
        self.config = SiteConfig.get()
        self.request, self.form = send_request(self.admin)

    def test_zero_return_is_a_failed_batch_but_later_batches_still_send(self):
        backend = Mock()
        backend.send_messages.side_effect = [0, 1]
        with patch("panel.services.get_connection", return_value=backend):
            sent, failed, error = services.send_in_batches(self.request, self.config, "News", "Body", [f"m{i}@example.test" for i in range(55)])
        self.assertEqual((sent, failed), (5, 50))
        self.assertIn("did not accept", error)
        self.assertEqual(backend.send_messages.call_count, 2)

    def test_zero_return_is_rejected_for_test_and_single_recipient_sends(self):
        with patch.object(services.MailerMessage, "send", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "did not accept"):
                services.send_test(self.request, self.config, "News", "Body", "members", self.admin.email)
            with self.assertRaisesRegex(RuntimeError, "did not accept"):
                services.send_one(self.request, self.config, "News", "Body", self.admin.email)
            views._send_mailer(self.request, self.config, self.form)
        log = MailLog.objects.get()
        self.assertFalse(log.ok)
        self.assertIn("did not accept", log.error)

    def test_send_is_claimed_before_smtp_and_claim_transaction_is_released(self):
        depth = len(connection.savepoint_ids)

        def send(*args):
            self.assertEqual(len(connection.savepoint_ids), depth)
            self.assertEqual(MailLog.objects.get().error, views.STARTED)

        with patch("panel.services.send_one", side_effect=send) as smtp:
            views._send_mailer(self.request, self.config, self.form)
            again, form = send_request(self.admin)
            self.assertIsNone(views._send_mailer(again, self.config, form))
        self.assertEqual(smtp.call_count, 1)
        self.assertEqual(MailLog.objects.count(), 1)
        self.assertTrue(MailLog.objects.get().ok)

    def test_old_list_does_not_reintroduce_inactive_members(self):
        inactive = User.objects.create_user(username="closed-mail", email="closed@example.test", is_active=False, wants_mailer=True)
        OldSubscriber.objects.create(email=inactive.email)
        OldSubscriber.objects.create(email="legacy@example.test")
        for audience in ("members", "old", "old_new", "both"):
            members, old = services.mailer_recipients(audience)
            self.assertNotIn(inactive.email, members + old)
        self.assertIn("legacy@example.test", services.mailer_recipients("old")[1])

    def test_address_case_variation_does_not_bypass_duplicate_guard(self):
        with patch("panel.services.send_one") as smtp:
            views._send_mailer(self.request, self.config, self.form)
            again, form = send_request(self.admin)
            form.cleaned_data["recipient"] = "list@EXAMPLE.TEST"
            self.assertIsNone(views._send_mailer(again, self.config, form))
        self.assertEqual(smtp.call_count, 1)
        self.assertEqual(MailLog.objects.count(), 1)


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentMailerClaimTests(TransactionTestCase):
    def test_second_send_is_refused_while_first_is_contacting_smtp(self):
        admin = User.objects.create_user(username="parallelmailer", email="admin@example.test", is_portal_admin=True)
        config = SiteConfig.get()
        started, finish = threading.Event(), threading.Event()

        def smtp(*args):
            started.set()
            if not finish.wait(10):
                raise RuntimeError("Test did not release simulated SMTP")

        def submit():
            close_old_connections()
            try:
                request, form = send_request(User.objects.get(pk=admin.pk))
                response = views._send_mailer(request, config, form)
                return response is None
            finally:
                close_old_connections()

        with patch("panel.services.send_one", side_effect=smtp) as delivery, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit)
            try:
                self.assertTrue(started.wait(10))
                self.assertTrue(pool.submit(submit).result(timeout=10))
            finally:
                finish.set()
            self.assertFalse(first.result(timeout=10))
        self.assertEqual(delivery.call_count, 1)
        self.assertEqual(MailLog.objects.count(), 1)
