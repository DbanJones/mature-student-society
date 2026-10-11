"""The slow checks' fact-finding: web requests to the site itself, TLS
certificates, the mail server, and walks over the disk.

Every probe runs in its own thread, touches only the network and the disk
(never the database), gives up by its deadline, and returns plain data that
a check in panel/healthchecks/checks.py turns into a Result.

The requests that reach Django (rather than Apache's static files) are made
one after another, never at once: gunicorn runs two workers and one of
them is busy running these checks.
"""

import fnmatch
import json
import os
import re
import secrets
import shutil
import smtplib
import socket
import ssl
import stat
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing

from .core import USER_AGENT, probe

TIMEOUT = 4  # seconds per request
PROBE_SALT = "mss.health.probe"
PROBE_USER = "-mss-health-probe-"  # can never be a CRSid: it starts with a dash
FORGED_IP = "192.0.2.1"  # TEST-NET-1: an address nobody is really at
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver", "0.0.0.0"}


def _left(deadline):
    return deadline - time.monotonic()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, deadline, headers=None, limit=200_000):
    """One GET that never follows a redirect. Returns {"status", "body",
    "headers", "ms"}, or {"error", "timed_out", "ms"} when there was no
    answer at all."""
    remaining = _left(deadline)
    if remaining < 0.3:
        return {"error": "out of time", "timed_out": True, "ms": 0}
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirect())
    started = time.monotonic()
    try:
        with opener.open(request, timeout=min(TIMEOUT, remaining)) as response:
            body = response.read(limit)
            status, head = response.status, response.headers
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(limit)
        except Exception:
            body = b""
        status, head = exc.code, exc.headers
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"[:200]
        return {"error": text, "timed_out": "timed out" in text.lower() or isinstance(exc, TimeoutError),
                "ms": int((time.monotonic() - started) * 1000)}
    return {
        "status": status,
        "body": body,
        "headers": {key.lower(): value for key, value in (head.items() if head else [])},
        "ms": int((time.monotonic() - started) * 1000),
    }


def probe_token():
    """A token only this site can make, good for a minute."""
    nonce = secrets.token_hex(8)
    return nonce, signing.TimestampSigner(salt=PROBE_SALT).sign(nonce)


def read_probe_token(token):
    """The nonce inside a valid probe token, or None."""
    try:
        return signing.TimestampSigner(salt=PROBE_SALT).unsign(token, max_age=60)
    except (signing.BadSignature, ValueError):
        return None


def _probe_answer(result, nonce):
    """The JSON the site's /healthz/probe gave back, if it was really ours."""
    if result.get("status") != 200:
        return None
    try:
        data = json.loads(result["body"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError, KeyError):
        return None
    return data if isinstance(data, dict) and data.get("nonce") == nonce else None


@probe("self")
def self_requests(ctx, deadline):
    """Requests to the site's own address, through Apache, one at a time:
    /healthz, the home page, two forged-login probes and the Raven gate."""
    if not ctx.origin:
        return {"skipped": True}
    origin = ctx.origin
    out = {"origin": origin}
    out["healthz"] = fetch(origin + "/healthz", deadline, limit=200)
    out["home"] = fetch(origin + "/", deadline)
    out["home"].pop("body", None)
    if getattr(settings, "RAVEN_MODE", "dev") == "header":
        for spelling in ("X-MSS-Remote-User", "X_MSS_Remote_User"):
            nonce, token = probe_token()
            result = fetch(origin + "/healthz/probe", deadline, headers={
                "X-MSS-Health-Token": token,
                spelling: PROBE_USER,
                "X-Forwarded-Proto": "http",
                "X-Forwarded-For": FORGED_IP,
            })
            answer = _probe_answer(result, nonce)
            out[f"guard {spelling}"] = {
                "status": result.get("status"),
                "error": result.get("error", ""),
                "timed_out": result.get("timed_out", False),
                "answer": answer,
            }
        raven = fetch(origin + "/accounts/raven/", deadline, limit=0)
        raven.pop("body", None)
        out["raven"] = raven
    return out


def public_hosts(ctx):
    """The site's public host names: the address it's reached at, and any
    real domain in ALLOWED_HOSTS."""
    hosts = []
    if ctx.origin:
        hosts.append(urlsplit(ctx.origin).hostname or "")
    for entry in settings.ALLOWED_HOSTS:
        name = entry.lstrip(".").strip().lower()
        if "*" in name or not name or name in LOCAL_HOSTS:
            continue
        if re.fullmatch(r"[\d.]+|[0-9a-f:]+", name) or "." not in name:
            continue
        hosts.append(name)
    seen, out = set(), []
    for host in hosts:
        if host and host not in seen and host not in LOCAL_HOSTS:
            seen.add(host)
            out.append(host)
    return out[:4]


def certificate(host, deadline):
    """Days until the host's certificate expires, or why it isn't trusted."""
    remaining = _left(deadline)
    if remaining < 0.3:
        return {"host": host, "error": "out of time", "timed_out": True}
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=min(TIMEOUT, remaining)) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return {"host": host, "invalid": (getattr(exc, "verify_message", "") or str(exc))[:160]}
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"[:160]
        return {"host": host, "error": text, "timed_out": "timed out" in text.lower()}
    expires = ssl.cert_time_to_seconds(cert["notAfter"])
    issuer = {}
    for part in cert.get("issuer", ()):
        issuer.update(dict(part))
    return {
        "host": host,
        "days": round((expires - time.time()) / 86400, 1),
        "expires": time.strftime("%Y-%m-%d", time.gmtime(expires)),
        "issuer": issuer.get("organizationName", "") or issuer.get("commonName", ""),
    }


@probe("tls")
def tls_hosts(ctx, deadline):
    """Each public host's certificate, and whether plain http:// moves
    visitors to https://."""
    if not ctx.production:
        return {"skipped": True}
    hosts = public_hosts(ctx)
    out = {"hosts": []}
    for host in hosts:
        entry = certificate(host, deadline)
        redirect = fetch(f"http://{host}/", deadline, limit=0)
        location = redirect.get("headers", {}).get("location", "")
        entry["http_status"] = redirect.get("status")
        entry["http_error"] = redirect.get("error", "")
        entry["redirects_to_https"] = redirect.get("status") in (301, 302, 303, 307, 308) and location.startswith("https://")
        out["hosts"].append(entry)
    return out


@probe("static")
def static_files(ctx, deadline):
    """What the site serves under /static/, file by file (panel/health.py)."""
    if not ctx.origin:
        return {"skipped": True}
    from panel import health

    return health.static_health(origin=ctx.origin, budget=max(1, min(health.BUDGET, _left(deadline) - 0.5)))


@probe("smtp")
def mail_server(ctx, deadline):
    """Does the mail server answer?"""
    if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
        return {"skipped": True}
    host, port = settings.EMAIL_HOST, settings.EMAIL_PORT
    # localhost is a different machine from the cron's sinkhole than from the
    # web server, so a failure there says nothing about the mailer.
    elsewhere = os.environ.get("MSS_WEB_PROCESS") != "1" and str(host).lower() in LOCAL_HOSTS
    remaining = _left(deadline)
    if remaining < 0.3:
        return {"error": "out of time", "timed_out": True, "host": host, "port": port, "elsewhere": elsewhere,
                "from": socket.gethostname()}
    started = time.monotonic()
    timeout = min(TIMEOUT, remaining)
    try:
        if getattr(settings, "EMAIL_USE_SSL", False):
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        try:
            code, _message = server.noop()
        finally:
            try:
                server.quit()
            except Exception:
                server.close()
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"[:160]
        return {"host": host, "port": port, "error": text, "timed_out": "timed out" in text.lower(),
                "ms": int((time.monotonic() - started) * 1000), "elsewhere": elsewhere, "from": socket.gethostname()}
    return {"host": host, "port": port, "code": code, "ms": int((time.monotonic() - started) * 1000),
            "elsewhere": elsewhere, "from": socket.gethostname()}


# --- the disk ------------------------------------------------------------------------------

# Secrets and copies of the database or settings; spreadsheets, which may hold
# members' details; and archives, which may hold anything.
SECRET_PATTERNS = (".env", ".env.*", "*.sqlite3", "*.sql", "*.sql.gz", "*.dump", "*.pem", "*.key", "*.bak",
                   ".my.cnf", "settings.py", "manage.py", "id_rsa*")
PERSONAL_PATTERNS = ("*.xlsx", "*.xls", "*.csv")
ARCHIVE_PATTERNS = ("*.zip", "*.tar", "*.tar.gz", "*.tgz", "*.7z")
MAX_ENTRIES = 20_000


def open_to_others(path):
    """Can every other user on the server read this file? Only when it is
    world-readable and every folder above it lets others through."""
    try:
        if not os.stat(path).st_mode & stat.S_IROTH:
            return False
        folder = os.path.dirname(os.path.abspath(path))
        while True:
            if not os.stat(folder).st_mode & stat.S_IXOTH:
                return False
            parent = os.path.dirname(folder)
            if parent == folder:
                return True
            folder = parent
    except OSError:
        return False


def _matches(name, patterns):
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in patterns)


def _kind(name):
    """"secret", "personal", "archive" or None for a file name."""
    if _matches(name, SECRET_PATTERNS):
        return "secret"
    if _matches(name, PERSONAL_PATTERNS):
        return "personal"
    if _matches(name, ARCHIVE_PATTERNS):
        return "archive"
    return None


def public_html():
    """The folder Apache serves the site from, when known: MSS_PUBLIC_HTML,
    else the folder holding DJANGO_STATIC_ROOT when that is ".../static"."""
    explicit = os.environ.get("MSS_PUBLIC_HTML", "").strip()
    if explicit:
        return explicit
    if not getattr(settings, "STATIC_VERSION_FROM_ROOT", False):
        return ""
    root = str(settings.STATIC_ROOT)
    return os.path.dirname(root) if os.path.basename(root) == "static" else ""


def _htaccess_has(path, pattern):
    """True/False when the file says (outside comments) what ``pattern``
    matches; None when the file can't be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(65536)
    except OSError:
        return None
    lines = [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]
    return any(re.search(pattern, line, re.IGNORECASE) for line in lines)


@probe("files")
def file_permissions(ctx, deadline):
    """Private files other SRCF users could read, and files in the public
    web folder that anyone on the internet could download."""
    if not ctx.posix:
        return {"skipped": True}
    base = str(settings.BASE_DIR)
    out = {"base": base}
    env = os.path.join(base, ".env")
    out["env_exists"] = os.path.exists(env)
    out["env_open"] = os.path.exists(env) and open_to_others(env)
    database = settings.DATABASES["default"]
    live_database = ""
    if database["ENGINE"].endswith("sqlite3"):
        name = str(database["NAME"])
        live_database = os.path.abspath(name)
        out["sqlite_open"] = os.path.exists(name) and open_to_others(name)
    loose = {"secret": [], "personal": [], "archive": []}
    try:
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if name in (".env", "manage.py") or os.path.abspath(path) == live_database or not os.path.isfile(path):
                continue
            kind = _kind(name)
            if kind and open_to_others(path):
                loose[kind].append(name)
    except OSError:
        pass
    out["loose_secret"], out["loose_personal"], out["loose"] = (
        loose["secret"][:20], loose["personal"][:20], loose["archive"][:20])
    media = str(settings.MEDIA_ROOT)
    open_files = checked = 0
    if os.path.isdir(media):
        for folder, dirs, files in os.walk(media):
            if folder == media:
                dirs[:] = [d for d in dirs if d != "public"]  # served to everyone on purpose
            for name in files:
                checked += 1
                if open_to_others(os.path.join(folder, name)):
                    open_files += 1
                if checked >= MAX_ENTRIES or _left(deadline) < 1:
                    break
            if checked >= MAX_ENTRIES or _left(deadline) < 1:
                out["media_partial"] = True
                break
    out["media_open"], out["media_checked"] = open_files, checked

    root = public_html()
    out["public_root"] = root
    out["public_found"] = bool(root) and os.path.isdir(root)
    if out["public_found"]:
        risky, personal, data, seen = [], [], [], 0
        root_depth = root.rstrip(os.sep).count(os.sep)
        for folder, dirs, files in os.walk(root):
            depth = folder.rstrip(os.sep).count(os.sep) - root_depth
            if folder == root:
                dirs[:] = [d for d in dirs if d != "static"]
            for name in list(dirs):
                if name == ".git":
                    risky.append(os.path.relpath(os.path.join(folder, name), root))
                    dirs.remove(name)
            if depth >= 4:
                dirs[:] = []
            for name in files:
                seen += 1
                rel = os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/")
                kind = _kind(name)
                if kind == "secret":
                    risky.append(rel)
                elif kind == "personal":
                    personal.append(rel)
                elif kind == "archive":
                    data.append(rel)
            if seen >= MAX_ENTRIES or _left(deadline) < 1:
                out["public_partial"] = True
                break
        out["public_risky"], out["public_personal"], out["public_data"] = risky[:20], personal[:20], data[:20]
        out["htaccess_unsets"] = _htaccess_has(
            os.path.join(root, ".htaccess"), r"^RequestHeader\s+unset\s+X-MSS-Remote-User\b")
        out["raven_gated"] = _htaccess_has(
            os.path.join(root, "accounts", "raven", ".htaccess"), r"^AuthType\s+Ucam-WebAuth\b")
    return out


def _folder_size(path, deadline, limit=MAX_ENTRIES * 3):
    total = count = 0
    partial = False
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
            count += 1
        if count >= limit or _left(deadline) < 1:
            partial = True
            break
    return total, count, partial


@probe("storage")
def storage(ctx, deadline):
    """How much the site keeps on disk, how much room is left, and whether
    uploads can be saved."""
    out = {}
    media = str(settings.MEDIA_ROOT)
    out["media_bytes"], out["media_files"], partial = (
        _folder_size(media, deadline) if os.path.isdir(media) else (0, 0, False))
    static_root = str(settings.STATIC_ROOT)
    out["static_bytes"] = _folder_size(static_root, deadline)[0] if os.path.isdir(static_root) else 0
    out["partial"] = partial
    database = settings.DATABASES["default"]
    if database["ENGINE"].endswith("sqlite3") and os.path.exists(str(database["NAME"])):
        out["sqlite_bytes"] = os.path.getsize(str(database["NAME"]))
    try:
        usage = shutil.disk_usage(str(settings.BASE_DIR))
        out["disk_total"], out["disk_free"] = usage.total, usage.free
    except OSError:
        pass
    target = media if os.path.isdir(media) else str(settings.BASE_DIR)
    out["writable_folder"] = target
    if not _saves_uploads(target):
        out["writable"] = None  # another account (the cron's) can't speak for the web server's
        return out
    try:
        handle, path = tempfile.mkstemp(prefix=".health-", dir=target)
        os.close(handle)
        os.remove(path)
        out["writable"] = True
    except OSError as exc:
        out["writable"] = False
        out["write_error"] = f"{type(exc).__name__}: {exc}"[:160]
    return out


def _saves_uploads(folder):
    """Is this process the one that saves uploads (a web worker), or at
    least running as the folder's owner?"""
    if os.environ.get("MSS_WEB_PROCESS") == "1" or not hasattr(os, "geteuid"):
        return True
    try:
        return os.stat(folder).st_uid == os.geteuid()
    except OSError:
        return False
