from django.core.management.base import BaseCommand, CommandError

from notifications.delivery import deliver_emails, retry_failed
from notifications.models import NotificationEmail
from panel.jobs import heartbeat


class Command(BaseCommand):
    help = "Deliver queued member notifications. Run every five minutes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--quiet", action="store_true")
        parser.add_argument("--retry-failed", action="store_true",
                            help="Retry exhausted messages after resolving the mail fault.")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("--limit must be between 1 and 1000.")
        with heartbeat("process_notifications") as beat:
            if options["retry_failed"]:
                retry_failed()
            sent = deliver_emails(options["limit"])
            failed = NotificationEmail.objects.filter(status=NotificationEmail.Status.FAILED).count()
            beat.detail = f"{sent} emails delivered; {failed} need attention."
            if not options["quiet"]:
                self.stdout.write(beat.detail)
            if failed:
                raise CommandError("Some notification emails need attention. Check the email service, then use --retry-failed.")
