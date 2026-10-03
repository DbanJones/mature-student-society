"""Minimal iCalendar (RFC 5545) output with no dependencies.

Used for the per-event "Add to calendar" download and each member's
personal subscription feed. Only the properties every calendar client
understands are written.
"""

import datetime

from django.utils import timezone

BS = chr(92)           # backslash: the RFC escape character
CRLF = chr(13) + chr(10)


def escape(text):
    text = text or ""
    return (
        text.replace(BS, BS + BS)
        .replace(";", BS + ";")
        .replace(",", BS + ",")
        .replace(chr(13), "")
        .replace(chr(10), BS + "n")
    )


def fold(line, width=70):
    """Long lines continue on the next line after a single space."""
    parts = []
    while len(line) > width:
        parts.append(line[:width])
        line = " " + line[width:]
    parts.append(line)
    return CRLF.join(parts)


def stamp(value):
    return value.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def vevent(event, url):
    end = event.end or event.start + datetime.timedelta(hours=2)
    description = f"{event.category.name}. Details and RSVP: {url}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:mss-event-{event.pk}@portal",
        f"DTSTAMP:{stamp(timezone.now())}",
        f"DTSTART:{stamp(event.start)}",
        f"DTEND:{stamp(end)}",
        "SUMMARY:" + escape(event.title),
        "DESCRIPTION:" + escape(description),
        "URL:" + url,
    ]
    if event.location:
        lines.append("LOCATION:" + escape(event.location))
    if event.is_cancelled:
        lines.append("STATUS:CANCELLED")
    lines.append("END:VEVENT")
    return lines


def build_calendar(events, name, url_for):
    """A complete VCALENDAR for ``events``; ``url_for(event)`` gives each
    event's absolute page URL."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MSS portal//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + escape(name),
    ]
    for event in events:
        lines += vevent(event, url_for(event))
    lines.append("END:VCALENDAR")
    return CRLF.join(fold(line) for line in lines) + CRLF
