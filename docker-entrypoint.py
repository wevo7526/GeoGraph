#!/usr/bin/env python3
"""Fix the mounted volume's ownership, then drop privileges and exec the app.

WHY THIS EXISTS (learned the hard way on MarketGraph). The Dockerfile creates
`/data` and chowns it at BUILD time; a Railway volume attaches at RUN time and
mounts OVER that path, owned by root — so the build-time chown applies to a
directory the running container can no longer see, and a `USER`-pinned app
dies at startup with "Permission denied" the moment a volume is attached.

Standard fix: start as root, correct the ownership of the mount that actually
exists, drop to the unprivileged user, exec. `os.execvp` keeps the app as
PID 1 so it receives signals directly — a shell wrapper would swallow SIGTERM
and turn every redeploy into a hard kill.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import sys
from pathlib import Path

APP_USER = "geograph"


def _writable_paths() -> list[Path]:
    """Every directory the app writes at runtime — the graph directory's
    parent covers the graph and any cache placed beside it."""
    db = Path(os.getenv("KUZU_DB_PATH", "/data/geograph.kuzu"))
    return [db.parent]


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            # A file we cannot touch is not worth refusing to boot over.
            with contextlib.suppress(OSError):
                os.chown(os.path.join(root, name), uid, gid)


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("docker-entrypoint.py: nothing to exec")

    if os.geteuid() == 0:
        user = pwd.getpwnam(APP_USER)
        for path in _writable_paths():
            _chown_tree(path, user.pw_uid, user.pw_gid)
        os.setgid(user.pw_gid)
        os.initgroups(user.pw_name, user.pw_gid)
        os.setuid(user.pw_uid)
        os.environ["HOME"] = user.pw_dir

    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
