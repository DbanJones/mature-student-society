"""gunicorn settings for the SRCF; run_on_srcf.sh starts gunicorn with them.

The socket is how Apache reaches the portal, and anyone who can connect to
it can claim to be any member, so it is closed to other SRCF users: owner
and group only. gunicorn gives the socket its own primary group after
creating it, which for an admin's account is their personal group, so once
it is up the socket is handed to the society's group (the SRCF runs the
society's site as the society). If that isn't possible the socket is left
open, as it always was, so the site keeps working, and the log says why.
"""

import datetime
import json
import os

SOCIETY = os.environ.get("SRCF_SOCIETY", "mss")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")

# Apache removes a forged X-MSS-Remote-User, but not a look-alike such as
# X_MSS_Remote_User, which gunicorn before 22 read as the same header. This
# drops every header with an underscore in its name (gunicorn 22 and later;
# older versions ignore the setting, hence gunicorn>=23 in requirements-srcf.txt).
header_map = "drop"
# Escape hatch: MSS_SOCKET_OPEN=1 in .env brings back the old, open socket,
# should Apache ever turn out not to reach it as the society.
OPEN = os.environ.get("MSS_SOCKET_OPEN") == "1"
umask = 0 if OPEN else 0o007


def worker_abort(worker):
    """gunicorn killed a worker that took longer than its timeout. Note when,
    and which part of the site it was stuck on, for the Site health page."""
    try:
        from panel.middleware import IN_FLIGHT

        part = IN_FLIGHT.get("path_kind", "")
    except Exception:
        part = ""
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "worker-timeouts.log"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "pid": worker.pid,
                "path": part,
            }) + "\n")
    except OSError:
        pass


def when_ready(server):
    if OPEN:
        server.log.warning("MSS_SOCKET_OPEN=1: the socket is open to every SRCF user.")
        return
    for listener in server.LISTENERS:
        path = getattr(listener, "cfg_addr", None)
        if not isinstance(path, str):
            continue  # a TCP address, nothing to hand over
        try:
            import grp  # Unix only

            os.chown(path, -1, grp.getgrnam(SOCIETY).gr_gid)
        except (ImportError, KeyError, OSError) as exc:
            os.chmod(path, 0o777)
            server.log.warning(
                "The socket is open to every SRCF user: it could not be given to group %s (%s).",
                SOCIETY, exc,
            )
