"""The space saver (Admin > Events > Space saver): event pictures take most of
the society's upload space, and once an event is a week behind us nobody
needs its picture. It also finds pictures nothing uses any more (Django
keeps the old file when a picture is replaced or an event deleted).

Only files under events/ are ever touched: never members' photos or the
pages' pictures. Nothing happens until an admin presses the button."""

import datetime
import os

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

GRACE = datetime.timedelta(days=7)  # an event's picture stays this long after it ends
FOLDER = "events"
SETTLING = datetime.timedelta(days=1)  # a file this new may be an upload still being saved


def _size(name):
    try:
        return os.path.getsize(os.path.join(settings.MEDIA_ROOT, name))
    except OSError:
        return 0


def finished_events(now=None):
    """Events with a picture that ended (or, with no end, started) more than
    a week ago, most recent first."""
    from events.models import Event

    cutoff = (now or timezone.now()) - GRACE
    return (Event.objects.exclude(image="").filter(Q(end__lt=cutoff) | Q(end__isnull=True, start__lt=cutoff))
            .order_by("-start"))


def unused_files(now=None):
    """Files under events/ that no event's picture is, left from replaced
    pictures and deleted events; not the newest (an upload in progress)."""
    from events.models import Event

    root = os.path.join(settings.MEDIA_ROOT, FOLDER)
    if not os.path.isdir(root):
        return []
    used = {os.path.normpath(name) for name in Event.objects.exclude(image="").values_list("image", flat=True)}
    settled = ((now or timezone.now()) - SETTLING).timestamp()
    found = []
    for folder, _dirs, files in os.walk(root):
        for filename in files:
            path = os.path.join(folder, filename)
            name = os.path.normpath(os.path.relpath(path, settings.MEDIA_ROOT))
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if name not in used and stat.st_mtime < settled:
                found.append((name, stat.st_size))
    return sorted(found)


def survey(now=None):
    """What the page shows: every event picture's space, and what could go."""
    events = [(event, _size(event.image.name)) for event in finished_events(now)]
    unused = unused_files(now)
    root = os.path.join(settings.MEDIA_ROOT, FOLDER)
    total = 0
    for folder, _dirs, files in os.walk(root):
        for filename in files:
            try:
                total += os.path.getsize(os.path.join(folder, filename))
            except OSError:
                pass
    return {
        "events": events,
        "event_bytes": sum(size for _event, size in events),
        "unused": unused,
        "unused_bytes": sum(size for _name, size in unused),
        "total_bytes": total,
        "grace_days": GRACE.days,
    }


def free_up(now=None):
    """Delete the pictures of finished events (the events stay) and the
    unused files. Returns (pictures, files, bytes freed)."""
    pictures = files = freed = 0
    for event in finished_events(now):
        size = _size(event.image.name)
        try:
            event.image.delete(save=False)  # the file, if it's still there
        except OSError:
            pass
        event.image = ""
        event.save(update_fields=["image"])
        pictures += 1
        freed += size
    root = os.path.realpath(os.path.join(settings.MEDIA_ROOT, FOLDER))
    for name, size in unused_files(now):
        path = os.path.realpath(os.path.join(settings.MEDIA_ROOT, name))
        if not path.startswith(root + os.sep):
            continue  # never outside events/
        try:
            os.remove(path)
        except OSError:
            continue
        files += 1
        freed += size
    return pictures, files, freed
