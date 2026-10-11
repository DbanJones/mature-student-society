"""Heartbeats: scheduled jobs say when they last ran, and how it went, so
the Site health page can tell a job that runs from one that silently
stopped (a cron line lost in a move, a crontab on the wrong machine).

    with heartbeat("close_polls") as beat:
        ...
        beat.detail = "Closed 2 polls"

Recording never gets in the job's way: if the database can't be written,
the job still runs and its own error, if any, still reaches cron.
"""

import contextlib
import logging
import socket

from django.utils import timezone

logger = logging.getLogger(__name__)


class _Beat:
    detail = ""


def _save(name, **fields):
    from django.db import DatabaseError

    from panel.models import JobHeartbeat

    try:
        JobHeartbeat.objects.update_or_create(name=name, defaults=fields)
    except DatabaseError as exc:  # e.g. new code copied in, its table not migrated yet
        logger.info("Couldn't record the %s heartbeat: %s", name, exc)
    except Exception:
        logger.warning("Couldn't record the %s heartbeat", name, exc_info=True)


@contextlib.contextmanager
def heartbeat(name):
    beat = _Beat()
    _save(name, last_started=timezone.now(), host=socket.gethostname()[:60])
    try:
        yield beat
    except BaseException as exc:
        _save(name, last_finished=timezone.now(), last_ok=False,
              last_error=f"{type(exc).__name__}: {exc}"[:300], detail=str(beat.detail)[:200])
        raise
    _save(name, last_finished=timezone.now(), last_ok=True, last_error="", detail=str(beat.detail)[:200])
