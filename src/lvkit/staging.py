"""Per-process staging area for cache writes — stdlib-only.

Every cache write that must be published atomically (extraction output, render
bodies, the owner index) is built first in a PRIVATE spot on the same filesystem as
the cache and then moved into place with one ``os.replace`` / directory rename. All
of those private spots live under ONE directory per lvkit process:

    <cache>/.staging/r-<pid>-<token>/          the run's staging directory
    <cache>/.staging/r-<pid>-<token>.lock      held open by the owning process

The run's directory is created lazily on the first write, reused for every write
in the process, and removed at normal exit. A process that dies mid-run (killed,
crashed, power loss) leaves its directory behind; the next lvkit run removes it.

Ownership is decided by the lock file, not by the pid in the name: the owner keeps
an exclusive OS lock on ``.lock`` for its whole life and the operating system drops
that lock when the process ends, however it ends. A cleaner that can take the lock
therefore knows the owner is gone — with no pid-reuse hazard and no platform-specific
process query. The pid in the name is only for a human reading the folder.
"""

from __future__ import annotations

import atexit
import itertools
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

STAGING_DIRNAME = ".staging"

# A directory with no lock file beside it — such as the ``.x-<random>`` ones that
# lvkit 0.8.7 and earlier leave behind — has no owner to ask. Those were created
# for ONE VI extraction and removed straight after it, so one that is more than an
# hour old can only be an orphan from a killed run, however slow the machine.
# Younger ones are left for a later run.
_UNLOCKED_ORPHAN_MIN_AGE_S = 3600.0

_RUN_PREFIX = "r-"
_LOCK_SUFFIX = ".lock"


def _try_lock(fh: IO[bytes]) -> bool:
    """Take a non-blocking exclusive lock on ``fh``; False when another handle
    already holds it."""
    if sys.platform == "win32":
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


@dataclass
class _Run:
    pid: int
    directory: Path
    lock_path: Path
    lock_fh: IO[bytes] | None
    counter: itertools.count[int] = field(default_factory=itertools.count)


_runs: dict[Path, _Run] = {}
_runs_lock = threading.Lock()


def staging_root(cache_root: Path) -> Path:
    """``<cache>/.staging`` — the shallow parent of every run's directory, kept
    out of the deep ``projects/<slug>/…`` tree so a staged path never grows with
    project depth."""
    return cache_root / STAGING_DIRNAME


def _claim_run(cache_root: Path) -> _Run:
    root = staging_root(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    name = f"{_RUN_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}"
    lock_path = root / f"{name}{_LOCK_SUFFIX}"
    # Lock file first, directory second: a directory therefore never exists
    # without its lock beside it, so a cleaner can't mistake a run that is still
    # starting up for an orphan.
    fh: IO[bytes] | None = open(lock_path, "a+b")
    if not _try_lock(fh):
        fh.close()
        fh = None
    if fh is None:
        # The filesystem can't lock (some network shares). Without a held lock
        # the file would mark a LIVE run as dead, so don't leave one; the
        # directory then ages out by the unlocked-orphan rule instead.
        try:
            lock_path.unlink()
        except OSError:
            pass
    run = _Run(os.getpid(), root / name, lock_path, fh)
    run.directory.mkdir()
    _remove_stale(root, keep=name)
    atexit.register(_release, cache_root)
    return run


def _run_for(cache_root: Path) -> _Run:
    with _runs_lock:
        run = _runs.get(cache_root)
        # A forked child inherits the parent's entry but not ownership of it.
        if run is None or run.pid != os.getpid():
            run = _claim_run(cache_root)
            _runs[cache_root] = run
        return run


def work_dir(cache_root: Path) -> Path:
    """A fresh, empty directory inside this process's staging directory, for one
    unit of work that produces several files (one VI's extraction). The caller
    either renames it into the cache or removes it."""
    run = _run_for(cache_root)
    path = run.directory / str(next(run.counter))
    path.mkdir(parents=True)
    return path


def temp_path(cache_root: Path) -> Path:
    """A unique, not-yet-created file path inside this process's staging
    directory, for one atomically-published file."""
    run = _run_for(cache_root)
    run.directory.mkdir(parents=True, exist_ok=True)
    return run.directory / str(next(run.counter))


def _release(cache_root: Path) -> None:
    """Remove this process's staging directory and give up its lock. Registered
    at exit and callable directly."""
    with _runs_lock:
        run = _runs.pop(cache_root, None)
    if run is None or run.pid != os.getpid():
        return
    shutil.rmtree(run.directory, ignore_errors=True)
    if run.lock_fh is not None:
        run.lock_fh.close()
        try:
            run.lock_path.unlink()
        except OSError:
            pass


def release_all() -> None:
    """Release every staging directory this process holds (normal exit does this
    automatically; exposed for callers that switch cache roots mid-process)."""
    for cache_root in list(_runs):
        _release(cache_root)


def _remove_stale(root: Path, keep: str) -> None:
    """Remove staging directories whose owner is gone. Best-effort: anything it
    can't remove stays for a later run."""
    now = time.time()
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    locked_names = {p.name[: -len(_LOCK_SUFFIX)] for p in entries if _is_lock(p)}
    for entry in entries:
        if _is_lock(entry):
            name = entry.name[: -len(_LOCK_SUFFIX)]
            if name == keep:
                continue
            if _owner_gone(entry):
                shutil.rmtree(root / name, ignore_errors=True)
                try:
                    entry.unlink()
                except OSError:
                    pass
        elif entry.name not in locked_names and entry.is_dir():
            try:
                age = now - entry.stat().st_mtime
            except OSError:
                continue
            if age > _UNLOCKED_ORPHAN_MIN_AGE_S:
                shutil.rmtree(entry, ignore_errors=True)


def _is_lock(path: Path) -> bool:
    return path.name.startswith(_RUN_PREFIX) and path.name.endswith(_LOCK_SUFFIX)


def _owner_gone(lock_path: Path) -> bool:
    """True when nobody holds the lock — the owning process has ended."""
    try:
        with open(lock_path, "r+b") as fh:
            return _try_lock(fh)
    except OSError:
        return False
