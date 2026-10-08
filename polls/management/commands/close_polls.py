"""Close every poll whose closing time has passed, applying results, and
every survey whose closing time has passed.

Polls also close themselves the next time anyone loads them; this command
is for cron so results are applied (and attendees emailed) on time even if
nobody visits. Suggested crontab line on the SRCF:

    */15 * * * * cd /societies/<soc>/portal && .venv/bin/python manage.py close_polls
"""

from django.core.management.base import BaseCommand

from polls.models import Poll


class Command(BaseCommand):
    help = "Close due polls (applying their outcomes) and due surveys."

    def handle(self, *args, **options):
        from surveys.models import Survey

        closed = 0
        for poll in Poll.objects.due().select_related("event"):
            poll.close()
            closed += 1
        ended = 0
        for survey in Survey.objects.due():
            survey.close()
            ended += 1
        self.stdout.write(f"Closed {closed} poll(s) and {ended} survey(s).")
