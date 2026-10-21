from django.core.management.base import BaseCommand, CommandError

from formals.models import FormalEmail
from formals.services import deliver_emails, expire_proposals


class Command(BaseCommand):
    help = "Release expired return holds and deliver queued formal notifications. Run every five minutes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Maximum emails attempted this run.")
        parser.add_argument("--quiet", action="store_true", help="Only report exhausted email retries (for cron).")

    def handle(self, *args, **options):
        from panel.jobs import heartbeat

        if not 1 <= options["limit"] <= 1000:
            raise CommandError("--limit must be between 1 and 1000.")
        with heartbeat("process_formals") as beat:
            expired = expire_proposals()
            sent = deliver_emails(options["limit"])
            failed = FormalEmail.objects.filter(sent_at__isnull=True, attempts__gte=5).count()
            beat.detail = f"Released {expired} expired holds; delivered {sent} emails; {failed} need attention."
            if failed:
                raise CommandError(f"{failed} formal emails could not be delivered after repeated attempts. Fix the mail service, then retry the failed emails.")
        if not options["quiet"]:
            self.stdout.write(f"Expired return holds: {expired}. Emails delivered: {sent}. Exhausted email retries: {failed}.")
