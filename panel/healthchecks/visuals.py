"""What the Site health page draws: the verdict at the top, the vital-sign
tiles, a 30-day history strip and a 48-hour sparkline of visits. Plain
HTML and inline SVG made here, so the page needs no JavaScript and no chart
library. Days and hours are grouped in Python, never by the database (the
SRCF's MySQL has no time zone tables)."""

import datetime

from django.utils import timezone

from .core import LABELS, SEVERITY, worst

VITALS = [  # (check key, tile label)
    ("site_reachable", "Home page"),
    ("server_errors", "Server errors"),
    ("response_times", "Under a second"),
    ("static_served", "Stylesheets"),
    ("https_certificate", "Certificate"),
    ("backups", "Last backup"),
    ("storage", "Storage"),
]


def _count(rows, status):
    return sum(1 for row in rows if row["status"] == status)


def verdict(rows):
    """The headline: status, title and one sentence."""
    overall = worst(row["status"] for row in rows)
    broken, attention = _count(rows, "fail"), _count(rows, "warn")
    if overall == "fail":
        title = f"{broken} thing{'s' if broken != 1 else ''} to fix now"
        if attention:
            title += f", and {attention} to sort out soon"
        text = "Members or visitors may be affected. Each one below says what to do, and whose job it is."
    elif overall == "warn":
        title = f"Working, with {attention} thing{'s' if attention != 1 else ''} to sort out"
        text = "Nothing is broken. The things below are worth doing before they become problems."
    elif overall == "ok":
        title = "Everything is working"
        text = "Nothing needs doing."
    else:
        title = "Not checked yet"
        text = "Press “Run all checks” to check the site now."
    return {"status": overall, "title": title, "text": text, "label": LABELS.get(overall, "")}


def vitals(rows):
    by_key = {row["key"]: row for row in rows}
    tiles = []
    for key, label in VITALS:
        row = by_key.get(key)
        if row is None:
            continue
        tiles.append({
            "key": key,
            "label": label,
            "status": row["status"],
            "value": row.get("vital") or row.get("metric") or LABELS.get(row["status"], ""),
            "title": row["title"],
        })
    return tiles


def history(now=None, days=30):
    """One cell per day, oldest first: the worst result of that day's runs."""
    from panel.models import HealthSnapshot

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    start = today - datetime.timedelta(days=days - 1)
    since = now - datetime.timedelta(days=days + 1)
    worst_by_day = {}
    for created, overall in HealthSnapshot.objects.filter(created_at__gte=since).values_list("created_at", "overall"):
        day = timezone.localtime(created).date()
        if day < start:
            continue
        current = worst_by_day.get(day)
        if current is None or SEVERITY.get(overall, 0) > SEVERITY.get(current, 0):
            worst_by_day[day] = overall
    cells = []
    for offset in range(days):
        day = start + datetime.timedelta(days=offset)
        status = worst_by_day.get(day, "none")
        label = LABELS.get(status, "not checked")
        cells.append({"day": day, "status": status, "label": f"{day:%a} {day.day} {day:%b}: {label.lower()}"})
    return cells


def sparkline(now=None, hours=48, width=240, height=44):
    """Requests per hour as an SVG path, with the hours that had server
    errors marked."""
    from panel.models import RequestStats

    now = now or timezone.now()
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - datetime.timedelta(hours=hours - 1)
    counts = {}
    for hour, requests, errors in RequestStats.objects.filter(hour__gte=start).values_list("hour", "requests", "s5xx"):
        counts[hour] = (requests, errors)
    values = [counts.get(start + datetime.timedelta(hours=step), (0, 0)) for step in range(hours)]
    peak = max((requests for requests, _ in values), default=0)
    total = sum(requests for requests, _ in values)
    if not total:
        return None
    step = width / (hours - 1)
    top = 4

    def y(requests):
        return round(height - (requests / peak) * (height - top), 1) if peak else height

    points = [(round(index * step, 1), y(requests)) for index, (requests, _) in enumerate(values)]
    line = "M" + " L".join(f"{x},{y_}" for x, y_ in points)
    area = f"{line} L{width},{height} L0,{height} Z"
    errors = [round(index * step, 1) for index, (_, failed) in enumerate(values) if failed]
    return {"line": line, "area": area, "errors": errors, "peak": peak, "total": total,
            "width": width, "height": height, "hours": hours}
