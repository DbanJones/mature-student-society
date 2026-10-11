"""The site health checks' framework: statuses, sections, the registry, the
runner and snapshots.

There are two kinds of check. Quick checks only read the database, the
settings and a few files, so they run live every time someone opens the
Site health page. Slow checks need the network or a walk over the disk, so
they only run when someone presses "Run all checks", when the scheduled
``health_check`` command runs, and never on a plain page view. Every run of
the slow checks is kept as a HealthSnapshot, which the page shows with its
age.

Slow checks get their facts from probes. Probes run in threads, all at
once, inside one overall time budget that stays well inside gunicorn's
30-second timeout. They touch only the network and the disk, never the
database (threads would each open a connection). Each check then reads
its probe's result in the main thread, where it can query the database.

A status is one of:

- ``ok``: working;
- ``warn``: working, but something needs doing;
- ``fail``: broken for visitors or members now, or unsafe;
- ``unknown``: the check couldn't find out (a timeout is never a failure);
- ``off``: an optional thing that isn't set up;
- ``na``: doesn't apply here (a development copy, or Windows).

Only ``warn`` and ``fail`` ever colour the overall verdict.
"""

import dataclasses
import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, wait

from django.conf import settings
from django.utils import timezone

STATUSES = ("ok", "warn", "fail", "unknown", "off", "na")
SEVERITY = {"fail": 3, "warn": 2, "ok": 1, "unknown": 0, "off": 0, "na": 0}
LABELS = {
    "ok": "Working",
    "warn": "Needs attention",
    "fail": "Broken",
    "unknown": "Couldn't check",
    "off": "Not set up",
    "na": "Not applicable here",
}
SECTIONS = [
    ("visitors", "Visitors & pages"),
    ("security", "Logins & security"),
    ("email", "Email"),
    ("jobs", "Background jobs & backups"),
    ("deploy", "Deployment"),
    ("storage", "Storage"),
    ("services", "Connected services"),
    ("setup", "Society setup"),
]
SCHEMA = 1
BUDGET = 15  # seconds for every probe together; gunicorn kills a worker at 30
STALE = datetime.timedelta(days=2)  # older answers are shown with their age, but don't stand in for newer ones
FULL_LOOKBACK = 8  # full runs searched for each slow check's latest answer
USER_AGENT = "MSS portal health check"


@dataclasses.dataclass
class Result:
    """What a check found. ``summary`` and ``fix`` are for the committee, in
    plain words; ``details`` (label, value pairs) and ``webmaster`` are for
    the webmaster, and only super admins see them."""

    status: str
    summary: str
    metric: str = ""
    fix: str = ""
    who: str = ""  # "webmaster", "committee" or "" (nobody needs to act)
    details: list = dataclasses.field(default_factory=list)
    webmaster: str = ""
    action: dict = dataclasses.field(default_factory=dict)  # a button: {"url", "label"}
    vital: str = ""  # a shorter metric for the vital-sign tile, when it needs one

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")


@dataclasses.dataclass
class Check:
    key: str
    title: str
    section: str
    slow: bool
    func: object
    order: int


REGISTRY = {}


def check(key, title, section, slow=False):
    """Register a check: ``func(ctx) -> Result``."""

    def register(func):
        REGISTRY[key] = Check(key, title, section, slow, func, len(REGISTRY))
        return func

    return register


PROBES = {}


def probe(name):
    """Register a probe: ``func(ctx, deadline) -> dict``. Network and disk
    only; it must give up by ``deadline`` (a time.monotonic() value)."""

    def register(func):
        PROBES[name] = func
        return func

    return register


def worst(statuses):
    """The status that decides a group: fail, then warn, then ok; checks that
    couldn't run or don't apply never make things look worse."""
    statuses = list(statuses)
    for status in ("fail", "warn"):
        if status in statuses:
            return status
    if "ok" in statuses:
        return "ok"
    for status in ("unknown", "off", "na"):
        if status in statuses:
            return status
    return "unknown"


class Context:
    """What every check can see. ``origin`` is the address the site is
    reached at (DJANGO_SITE_URL, else the request's own), or "" when there's
    neither, and then the network probes say "not applicable"."""

    def __init__(self, request=None, *, full=False, site_url=None, now=None, trigger=""):
        from panel.health import _origin

        self.request = request
        self.full = full
        self.trigger = trigger
        self.now = now or timezone.now()
        self.production = getattr(settings, "IS_PRODUCTION_AUTH", False)
        self.posix = os.name == "posix"
        origin = (site_url or getattr(settings, "SITE_URL", "") or "").rstrip("/")
        if not origin and request is not None:
            try:
                origin = _origin(request)
            except Exception:
                origin = ""
        self.origin = origin
        self.probes = {}

    def probe(self, name):
        """The probe's result; {"skipped": True} on a quick run and
        {"timed_out": True} when it ran out of time."""
        if not self.full:
            return {"skipped": True}
        return self.probes.get(name, {"timed_out": True})


def run_probes(ctx, budget=BUDGET):
    """Run every probe at once; collect what finished within the budget."""
    deadline = time.monotonic() + budget
    pool = ThreadPoolExecutor(max_workers=max(1, len(PROBES)), thread_name_prefix="health")
    try:
        futures = {name: pool.submit(func, ctx, deadline) for name, func in PROBES.items()}
        wait(futures.values(), timeout=budget)
        for name, future in futures.items():
            if not future.done():
                ctx.probes[name] = {"timed_out": True}
            elif future.exception() is not None:
                exc = future.exception()
                ctx.probes[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            else:
                ctx.probes[name] = future.result()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _as_row(item, result, checked_at):
    return {
        "key": item.key,
        "title": item.title,
        "section": item.section,
        "slow": item.slow,
        "status": result.status,
        "summary": result.summary,
        "metric": result.metric,
        "fix": result.fix,
        "who": result.who,
        "details": [[str(label), str(value)] for label, value in result.details],
        "webmaster": result.webmaster,
        "action": result.action,
        "vital": result.vital,
        "checked_at": checked_at.isoformat(),
    }


def evaluate(ctx):
    """Run the checks this context allows (every one on a full run, the
    quick ones otherwise) and return their rows, in display order."""
    rows = []
    for item in sorted(REGISTRY.values(), key=lambda c: c.order):
        if item.slow and not ctx.full:
            continue
        try:
            result = item.func(ctx)
        except Exception as exc:  # a broken check must never break the page
            result = Result(
                "unknown", "The check itself went wrong, so this says nothing about the site.",
                who="webmaster", details=[("Error", f"{type(exc).__name__}: {exc}"[:300])],
            )
        rows.append(_as_row(item, result, ctx.now))
    return rows


def run(request=None, *, full=True, trigger="command", user=None, save=True, site_url=None, budget=BUDGET):
    """Run the checks and (by default) keep the run as a HealthSnapshot.
    Returns the snapshot, saved or not."""
    from panel.models import HealthSnapshot

    from .deploy import version_label

    started = time.monotonic()
    ctx = Context(request, full=full, site_url=site_url, trigger=trigger)
    if full:
        run_probes(ctx, budget)
    rows = evaluate(ctx)
    snapshot = HealthSnapshot(
        created_at=ctx.now,
        trigger=trigger,
        full=full,
        overall=worst(row["status"] for row in rows),
        statuses={row["key"]: row["status"] for row in rows},
        results=rows,
        schema=SCHEMA,
        duration_ms=int((time.monotonic() - started) * 1000),
        version=version_label()[:60],
        run_by=user if getattr(user, "pk", None) else None,
    )
    if save:
        snapshot.save()
    return snapshot


def saved_row(key, fulls, now):
    """The row to show for a slow check from saved full runs (newest first):
    the newest one that found something, so a run that couldn't check (a
    timeout, or the cron on another machine that can't judge the web
    server's mail or uploads) doesn't hide a recent answer. Answers older
    than STALE don't count; with none, the newest row, whatever it says."""
    newest = None
    for snapshot in fulls:
        for row in snapshot.results or []:
            if row.get("key") != key:
                continue
            if newest is None:
                newest = (snapshot, row)
            if row.get("status") != "unknown" and now - snapshot.created_at <= STALE:
                return snapshot, row
    return newest


def page_rows(request):
    """The rows for the Site health page: quick checks run now, slow checks
    from recent full runs (each row says when it was checked; see
    saved_row), and a "not checked yet" row for any slow check that has
    never run."""
    from panel.models import HealthSnapshot

    ctx = Context(request, full=False)
    live = {row["key"]: row for row in evaluate(ctx)}
    fulls = list(HealthSnapshot.objects.filter(full=True, schema=SCHEMA)[:FULL_LOOKBACK])
    latest = fulls[0] if fulls else None
    rows = []
    for item in sorted(REGISTRY.values(), key=lambda c: c.order):
        found = saved_row(item.key, fulls, ctx.now) if item.slow else None
        if not item.slow:
            row = dict(live[item.key], live=True)
        elif found:
            snapshot, saved = found
            row = dict(saved, live=False, title=item.title, section=item.section)
            try:
                row["checked"] = datetime.datetime.fromisoformat(row.get("checked_at", ""))
            except (TypeError, ValueError):
                row["checked"] = snapshot.created_at
            row["checked_ago"] = _ago(row["checked"], ctx.now)
        else:
            row = _as_row(item, Result(
                "unknown", "Not checked yet. Press “Run all checks” to check it.", vital="Not run yet",
            ), ctx.now)
            row.update(live=False, never=True)
        rows.append(row)
    return rows, latest


def _ago(when, now):
    from .checks import ago

    return ago(when, now)


def grouped(rows):
    """[(section key, label, status, rows)] in display order."""
    out = []
    for key, label in SECTIONS:
        members = [row for row in rows if row["section"] == key]
        if members:
            out.append((key, label, worst(row["status"] for row in members), members))
    return out
