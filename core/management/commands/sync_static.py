"""Copy the site's static files into STATIC_ROOT, comparing contents.

``collectstatic`` skips a file when the collected copy is newer than the
source, judged by timestamp, and a ZIP or WinSCP deploy keeps old
timestamps, so changed stylesheets can be skipped and the site keeps serving
stale ones. This copies a file whenever its bytes differ, writes each copy
atomically and never deletes anything, so the folder Apache serves is never
empty or half-written. run_on_srcf.sh runs it on every start.
"""

import filecmp
import os
import stat
import tempfile

from django.conf import settings
from django.core.management.base import BaseCommand

from core.storage import static_sources


class Command(BaseCommand):
    help = "Copy changed static files into STATIC_ROOT, by content; nothing is deleted."

    def handle(self, *args, **options):
        root = str(settings.STATIC_ROOT)
        copied = unchanged = 0
        for name, source in static_sources().items():
            target = os.path.join(root, *name.split("/"))
            if os.path.isfile(target) and filecmp.cmp(source, target, shallow=False):
                unchanged += 1
                continue
            self._copy(source, target)
            copied += 1
        self.stdout.write(f"sync_static: {copied} copied, {unchanged} unchanged, into {root}")

    @staticmethod
    def _ensure_folder(folder):
        """Create ``folder`` and any missing parents, each readable by the
        web server whatever the process umask."""
        missing = []
        while folder and not os.path.isdir(folder):
            missing.append(folder)
            folder = os.path.dirname(folder)
        for path in reversed(missing):
            os.mkdir(path)
            # keep the setgid bit a group-sticky parent passed on, so files
            # inside stay the society's
            os.chmod(path, 0o775 | (os.stat(path).st_mode & stat.S_ISGID))

    def _copy(self, source, target):
        folder = os.path.dirname(target)
        self._ensure_folder(folder)
        handle, temp = tempfile.mkstemp(dir=folder, prefix=".sync-")
        try:
            with os.fdopen(handle, "wb") as out, open(source, "rb") as src:
                for chunk in iter(lambda: src.read(65536), b""):
                    out.write(chunk)
            os.chmod(temp, 0o644)  # Apache must read it, whatever the process umask
            os.replace(temp, target)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise
