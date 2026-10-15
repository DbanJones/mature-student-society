"""Site health: is the website working, and if not, what to do about it.

See core.py for how checks and probes work, checks.py for the checks
themselves, and the Site health page (Admin → Health).
"""

import datetime

from django.utils import timezone

from . import checks, probes  # noqa: F401  (registers the checks and probes)
from .core import LABELS, REGISTRY, SECTIONS, STALE, grouped, page_rows, run, worst  # noqa: F401
from .visuals import history, sparkline, verdict, vitals

RUN_GAP = datetime.timedelta(seconds=60)  # one full run a minute at most


def run_locked(request):
    """A full run for the "Run all checks" button, unless one started in the
    last minute (still going, or just done). Returns the snapshot, or None
    when it didn't run."""
    from panel.models import JobHeartbeat

    now = timezone.now()
    JobHeartbeat.objects.get_or_create(name="health_run")
    claimed = JobHeartbeat.objects.filter(name="health_run").filter(
        last_started__isnull=True).update(last_started=now) or JobHeartbeat.objects.filter(
        name="health_run", last_started__lt=now - RUN_GAP).update(last_started=now)
    if not claimed:
        return None
    ok, error = False, ""
    try:
        snapshot = run(request, full=True, trigger="button", user=request.user)
        ok = True
        return snapshot
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        JobHeartbeat.objects.filter(name="health_run").update(
            last_finished=timezone.now(), last_ok=ok, last_error=error)


FRESH_FAILURE = datetime.timedelta(hours=1)
NOW_CHECKS = ("server_errors",)  # their "broken" means "now", so it fades to amber after an hour, as live


def latest_summary():
    """What the panel's tab icon and the dashboard's strip show: the Site
    health page's picture without re-running anything.

    The newest full run is the base, however old (the page shows it too).
    Every run from the last two days is laid over it in time order, so a
    restart's quick run updates the checks it repeated and can't hide a
    failure only a full run sees. A run that couldn't check something
    ("unknown") never replaces an answer.

    {"status", "label", "when" (of the newest full run), "ago", "full",
    "full_stale"}, or None when nothing has run."""
    from django.db import DatabaseError

    from panel.models import HealthSnapshot

    now = timezone.now()
    fields = ("created_at", "overall", "full", "statuses")
    try:
        full = HealthSnapshot.objects.filter(full=True).only(*fields).first()
        recent = list(HealthSnapshot.objects.filter(created_at__gte=now - STALE).only(*fields)[:50])
    except DatabaseError:  # a worker running new code before the restart's migrate
        return None
    runs = sorted({run.pk: run for run in recent + ([full] if full else [])}.values(), key=lambda run: run.created_at)
    if not runs:
        return None
    merged, seen = {}, {}
    for run in runs:  # oldest first, so newer answers win
        for key, status in (run.statuses or {}).items():
            if key in REGISTRY and (status != "unknown" or key not in merged):
                merged[key], seen[key] = status, run.created_at
    for key in NOW_CHECKS:
        if merged.get(key) == "fail" and now - seen[key] > FRESH_FAILURE:
            merged[key] = "warn"
    status = worst(merged.values()) if merged else runs[-1].overall
    when = (full or runs[-1]).created_at
    return {
        "status": status,
        "label": LABELS.get(status, ""),
        "when": when,
        "ago": checks.ago(when, now),
        "full": full is not None,
        "full_stale": full is None or now - full.created_at > STALE,
    }


def page_context(request):
    from panel.models import ErrorEvent

    rows, latest = page_rows(request)
    week = timezone.now() - datetime.timedelta(days=7)
    errors = []
    if request.user.is_super_admin:
        errors = list(ErrorEvent.objects.filter(resolved_at__isnull=True, last_seen__gte=week)[:30])
    from accounts.models import User

    webmasters = [user.get_full_name() or user.username
                  for user in User.objects.filter(is_super_admin=True, is_banned=False).order_by("first_name")[:3]]
    order = {"fail": 0, "warn": 1}
    return {
        # the page leads with what needs doing; the rest folds away
        "problems": sorted((row for row in rows if row["status"] in order), key=lambda row: order[row["status"]]),
        "unchecked": [row for row in rows if row["status"] == "unknown"],
        "not_set_up": [row for row in rows if row["status"] == "off"],
        "working": [row for row in rows if row["status"] == "ok"],
        "na_count": sum(1 for row in rows if row["status"] == "na"),
        "verdict": verdict(rows),
        "vitals": vitals(rows),
        "sections": grouped(rows),
        "history": history(),
        "spark": sparkline(),
        "latest_full": latest,
        "latest_full_ago": checks.ago(latest.created_at, timezone.now()) if latest else "",
        "latest_full_stale": latest is not None and timezone.now() - latest.created_at > STALE,
        "errors": errors,
        "webmasters": webmasters,
        "show_webmaster": request.user.is_super_admin,
        "status_labels": LABELS,
    }
