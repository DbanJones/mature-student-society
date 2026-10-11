"""Run the site health checks from the command line, from cron, or as the
portal starts, and keep the result for the Site health page.

    manage.py health_check             every check, with a report
    manage.py health_check --quick     only the checks that need no network
    manage.py health_check --quiet     print nothing unless something needs attention
    manage.py health_check --json      the results as JSON
    manage.py health_check --prune     also tidy old runs, errors, timings and sessions

The exit status is 0 when nothing needs attention, 1 when something does
and 2 when something is broken. Cron emails whatever a job prints, so this
daily line (on sinkhole, ``crontab -e``) mails the webmaster only on a bad
day, and keeps the history tidy:

    30 7 * * * cd /public/societies/mss/mature-student-society && set -a && . ./.env && set +a && .venv/bin/python manage.py health_check --trigger cron --quiet --prune
"""

import datetime
import json
import os
import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

MARKS = {"ok": "[ ok ]", "warn": "[WARN]", "fail": "[FAIL]", "unknown": "[ ?? ]", "off": "[off ]", "na": "[ na ]"}
KEEP_ALL = datetime.timedelta(days=14)
KEEP_DAILY = datetime.timedelta(days=180)


class Command(BaseCommand):
    help = "Run the site health checks, keep the result, and exit 0 (fine), 1 (needs attention) or 2 (broken)."

    def add_arguments(self, parser):
        parser.add_argument("--quick", action="store_true", help="Only the checks that need no network or disk walk.")
        parser.add_argument("--quiet", action="store_true", help="Print nothing unless something needs attention.")
        parser.add_argument("--json", action="store_true", help="Print the results as JSON.")
        parser.add_argument("--prune", action="store_true", help="Also tidy old runs, errors, timings and sessions.")
        parser.add_argument("--no-save", action="store_true", help="Don't keep this run.")
        parser.add_argument("--trigger", choices=["command", "cron", "deploy"], default="command",
                            help="What started this run (shown on the Site health page).")
        parser.add_argument("--site-url", default="", help="Check this address instead of DJANGO_SITE_URL.")

    def handle(self, *args, **options):
        from panel.jobs import heartbeat

        if options["trigger"] == "cron":
            with heartbeat("health_check") as beat:
                snapshot = self._run(options)
                beat.detail = snapshot.overall
        else:
            snapshot = self._run(options)
        pruned = self.prune() if options["prune"] else None
        self.report(snapshot, options, pruned)
        code = {"fail": 2, "warn": 1}.get(snapshot.overall, 0)
        if code:
            sys.exit(code)

    def _run(self, options):
        from panel.healthchecks import run

        return run(full=not options["quick"], trigger=options["trigger"], save=not options["no_save"],
                   site_url=options["site_url"] or None)

    def _write(self, text):
        encoding = getattr(self.stdout._out, "encoding", None) or "utf-8"
        self.stdout.write(text.encode(encoding, "replace").decode(encoding))

    def report(self, snapshot, options, pruned):
        from panel.healthchecks import LABELS

        if options["json"]:
            self.stdout.write(json.dumps({
                "overall": snapshot.overall,
                "created_at": snapshot.created_at.isoformat(),
                "full": snapshot.full,
                "duration_ms": snapshot.duration_ms,
                "version": snapshot.version,
                "results": snapshot.results,
                "pruned": pruned,
            }, indent=2))
            return
        rows = snapshot.results
        if options["quiet"]:
            rows = [row for row in rows if row["status"] in ("warn", "fail")]
            if not rows:
                return
        when = timezone.localtime(snapshot.created_at)
        self._write(f"Site health: {LABELS.get(snapshot.overall, snapshot.overall).lower()} "
                    f"({when:%d %b %Y %H:%M}, {'every check' if snapshot.full else 'quick checks'}, "
                    f"{snapshot.duration_ms / 1000:.1f} s)")
        for row in rows:
            self._write(f"{MARKS.get(row['status'], row['status'])} {row['title']}: {row['summary']}")
            if row["status"] in ("warn", "fail"):
                if row.get("fix"):
                    self._write(f"       What to do: {row['fix']}")
                if row.get("webmaster"):
                    self._write(f"       For the webmaster: {row['webmaster']}")
                for label, value in row.get("details", [])[:6]:
                    self._write(f"       {label}: {value}")
        if pruned:
            self._write("Tidied: " + ", ".join(f"{count} {what}" for what, count in pruned.items()))

    def prune(self):
        """Keep every run from the last two weeks, then the worst run of each
        day for six months; old fixed errors, slow requests and hourly
        counts; and expired login sessions."""
        from panel.healthchecks.core import SEVERITY
        from panel.models import ErrorEvent, HealthSnapshot, RequestStats, SlowRequest

        now = timezone.now()
        newest_full = HealthSnapshot.objects.filter(full=True).values_list("pk", flat=True).first()
        older = HealthSnapshot.objects.filter(created_at__lt=now - KEEP_ALL)
        keep = {}
        for pk, created, overall in older.filter(created_at__gte=now - KEEP_DAILY).values_list(
                "pk", "created_at", "overall"):
            day = timezone.localtime(created).date()
            rank = (SEVERITY.get(overall, 0), pk)
            if day not in keep or rank > keep[day]:
                keep[day] = rank
        kept = {pk for _, pk in keep.values()} | {newest_full}
        doomed = list(older.exclude(pk__in=[pk for pk in kept if pk]).values_list("pk", flat=True))
        for start in range(0, len(doomed), 500):
            HealthSnapshot.objects.filter(pk__in=doomed[start:start + 500]).delete()
        counts = {
            "old runs": len(doomed),
            "fixed errors": ErrorEvent.objects.filter(resolved_at__lt=now - datetime.timedelta(days=90)).delete()[0],
            "slow requests": SlowRequest.objects.filter(at__lt=now - datetime.timedelta(days=30)).delete()[0],
            "hourly counts": RequestStats.objects.filter(hour__lt=now - KEEP_DAILY).delete()[0],
        }
        call_command("clearsessions")
        self._trim_timeouts()
        return counts

    @staticmethod
    def _trim_timeouts():
        from django.conf import settings

        path = os.path.join(str(settings.BASE_DIR), "state", "worker-timeouts.log")
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
            if len(lines) > 500:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.writelines(lines[-500:])
        except OSError:
            pass
