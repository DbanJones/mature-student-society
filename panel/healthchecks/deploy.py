"""Is the server running the code that is on it?

A deploy copies new files (WinSCP or a ZIP) and must be followed by a
restart. Each web worker therefore records, when it starts, a fingerprint
of every Python file, template and requirements file: size, modification
time and a hash of the content. The deploy check later re-reads only the
files whose size or time changed and compares hashes, so a ZIP that keeps
old timestamps can't fool it and a changed timestamp alone doesn't count.

Only web workers record (run_on_srcf.sh sets MSS_WEB_PROCESS=1 just before
starting gunicorn); a management command has no restart to wait for.
"""

import hashlib
import os
import time

from django.conf import settings
from django.utils import timezone

SKIP_DIRS = {".venv", "venv", "staticfiles", "media", ".git", "__pycache__", "node_modules", "state", "sockets"}
MAX_FILES = 3000

BOOT = {}  # filled by record_boot() in web workers


def _wanted(name):
    return name.endswith((".py", ".html")) or (name.startswith("requirements") and name.endswith(".txt"))


def _walk(root):
    count = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if _wanted(name):
                path = os.path.join(folder, name)
                yield os.path.relpath(path, root).replace(os.sep, "/"), path
                count += 1
                if count >= MAX_FILES:
                    return


def _hash(path):
    with open(path, "rb") as handle:
        # Line endings don't matter: a copy made on Windows is the same code.
        return hashlib.sha1(handle.read().replace(b"\r\n", b"\n"), usedforsecurity=False).hexdigest()


def fingerprint(root=None):
    """{relative path: (size, mtime_ns, sha1)} for the code under ``root``."""
    root = str(root or settings.BASE_DIR)
    files = {}
    for rel, path in _walk(root):
        try:
            stat = os.stat(path)
            files[rel] = (stat.st_size, stat.st_mtime_ns, _hash(path))
        except OSError:
            continue
    return files


def digest(files):
    """A short name for one version of the code: the same files give the same
    name on any machine."""
    joined = "\n".join(f"{rel} {entry[2]}" for rel, entry in sorted(files.items()))
    return hashlib.sha1(joined.encode(), usedforsecurity=False).hexdigest()[:10]


def record_boot(root=None):
    """Called once per web worker as it starts (PanelConfig.ready)."""
    files = fingerprint(root)
    BOOT.clear()
    BOOT.update({
        "root": str(root or settings.BASE_DIR),
        "files": files,
        "digest": digest(files),
        "started": timezone.now(),
        "pid": os.getpid(),
    })


def changed_since_boot():
    """(changed, added, removed) relative paths since this worker started,
    or None when this process didn't record a fingerprint."""
    if not BOOT:
        return None
    root, before = BOOT["root"], BOOT["files"]
    changed, added = [], []
    seen = set()
    for rel, path in _walk(root):
        seen.add(rel)
        old = before.get(rel)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if old is None:
            added.append(rel)
        elif (stat.st_size, stat.st_mtime_ns) != old[:2]:
            try:
                if _hash(path) != old[2]:
                    changed.append(rel)
            except OSError:
                continue
    removed = sorted(set(before) - seen)
    return changed, added, removed


def migration_state():
    """{"behind": [app.name, ...], "ahead": [...]}: migrations the database
    still needs, and applied migrations this code doesn't have (code rolled
    back after a migrate)."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    behind = [f"{migration.app_label}.{migration.name}" for migration, _ in executor.migration_plan(targets)]
    applied = executor.recorder.applied_migrations()
    ahead = sorted(f"{app}.{name}" for app, name in applied if (app, name) not in executor.loader.disk_migrations
                   and app in {config.label for config in _app_configs()})
    return {"behind": behind, "ahead": ahead}


def _app_configs():
    from django.apps import apps

    return apps.get_app_configs()


def git_ref(root=None):
    """The commit the code came from, when the folder is a git checkout
    (a ZIP or WinSCP copy isn't, and then this is "")."""
    git = os.path.join(str(root or settings.BASE_DIR), ".git")
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as handle:
            head = handle.read().strip()
        if not head.startswith("ref:"):
            return head[:10]
        ref = head[4:].strip()
        path = os.path.join(git, *ref.split("/"))
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()[:10]
        with open(os.path.join(git, "packed-refs"), encoding="utf-8") as handle:
            for line in handle:
                if line.rstrip().endswith(" " + ref):
                    return line.split()[0][:10]
    except OSError:
        pass
    return ""


_LABEL = {}


def version_label():
    """"<commit or code fingerprint>": what the snapshots record as the
    version that ran the checks."""
    if "value" not in _LABEL or time.monotonic() - _LABEL["at"] > 300:
        ref = git_ref()
        code = BOOT.get("digest") or ""
        _LABEL["value"] = " ".join(part for part in (ref and f"commit {ref}", code and f"code {code}") if part)
        _LABEL["at"] = time.monotonic()
    return _LABEL["value"]
