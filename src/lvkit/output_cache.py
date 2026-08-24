"""Content-addressed **output** cache for the render/diff pipeline.

The extracted-XML cache (``extractor``) already skips re-*extraction*; this skips
the whole *build* (parse -> graph -> scene -> render) when the same inputs have
already produced output. Stdlib-only (imports only :mod:`lvkit.cache_paths`) so
the CLI can check for a hit BEFORE importing the graph/parser/pylabview stack
(~250 ms) — a hit path that never touches the heavy machinery.

Addressing mirrors extraction (see :mod:`lvkit.cache_paths`):

* A VI with a stable project/library path is **path-addressed** — one slot per
  VI, mirrored under ``<cache>/<ns>/<slug>/render/<rel>/<stem>.<ext>`` (project-
  first), overwritten in place when the VI changes. Bounded by the number of VIs;
  it plateaus.
* An ``adhoc`` input (a throwaway ``mkdtemp`` git-blob, or a standalone VI
  outside any repo) has no owner and no reusable path, so it is **content-
  addressed** in a flat ``<cache>/adhoc/render/<sha>.<ext>`` and swept by TTL.

FRESHNESS (in ``meta.json``, checked by ``cache_paths.meta_fresh``) = the
VI-content signal (``sha256``/``mtime``/``size``), the ``text_encoding`` (system
code page), an ``options`` tag, and the lvkit ``version`` (kept for readable
meta) — so a VI edit, a code-page change, or a different ``--format``/``--theme``
is a miss.

COMPATIBILITY — which lvkit BUILD produced the artifact — is deliberately NOT a
freshness key. It is the ``<fp>`` level of the cache PATH (``cache_paths``
``kind_fingerprint`` → ``source_fingerprint``), so an incompatible build simply
finds nothing at its own path and rebuilds into a SEPARATE slot beside the other
build, instead of clobbering it in place. A renderer/graph/parser/data edit thus
still busts this cache (different path), with no version bump — the same
invalidation the SQLite index shares — but two builds coexist rather than
ping-ponging over one slot.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from lvkit import cache_paths
from lvkit.text_encoding import labview_text_encoding

# Retirement is decoupled from correctness (a removed file is indistinguishable
# from a never-built one — the next lookup just does the work), so this is pure
# policy, freely swappable, and does ONE thing: retire a whole per-build <fp> dir
# once it is idle past the TTL. There is deliberately NO within-fingerprint file
# aging — inside a live build, correctness is already handled by VI-content
# freshness + the <fp> path level, so a per-file TTL would be redundant churn.
# Files are mtime-bumped on every hit, so the build in ACTIVE use never ages out;
# only ones you upgraded away from are reclaimed. <fp> dirs live at
# <ns>/<slug>/<kind>/<fp> and adhoc/<kind>/<fp>. extract gets a longer horizon (it
# is the costliest to rebuild and the slowest-growing). Override the clocks with
# LVKIT_CACHE_TTL_DAYS / LVKIT_EXTRACT_TTL_DAYS.
_DEFAULT_TTL_DAYS = 7.0
# Extract is STICKIER than render/diff/index: it is the costliest slice to rebuild
# (re-runs pylabview) and the slowest-growing (a new <fp> only on a rare
# extraction-code change), so a much longer idle horizon saves the priciest work
# for near-zero disk. Independently tunable via LVKIT_EXTRACT_TTL_DAYS.
_EXTRACT_TTL_DAYS = 60.0


def _ttl_seconds(
    default_days: float = _DEFAULT_TTL_DAYS, env_var: str = "LVKIT_CACHE_TTL_DAYS"
) -> float:
    try:
        return float(os.environ.get(env_var, default_days)) * 86400
    except ValueError:
        return default_days * 86400


# The whole-cache walk is rate-limited to at most once per this interval across ALL
# processes (a persisted <cache>/_last_sweep stamp), so it is NEVER a per-command
# cost — a command inside the window stats one file and returns. A 7-day TTL means
# a daily sweep is plenty timely. Tests set it to 0 to force a walk.
def _sweep_interval_seconds() -> float:
    try:
        return float(os.environ.get("LVKIT_SWEEP_INTERVAL_HOURS", 24.0)) * 3600
    except ValueError:
        return 24.0 * 3600


def _ext_for(fmt: str) -> str:
    return "svg" if fmt == "svg" else "html"


# ── path resolution ─────────────────────────────────────────────────────────


def _render_paths(input_path: Path, fmt: str) -> tuple[Path, Path, bool]:
    """``(body_path, meta_path, is_adhoc)`` for a render of ``input_path``.

    Path-addressed for project/shared VIs; flat content-addressed for adhoc.
    """
    ext = _ext_for(fmt)
    d, _, ns = cache_paths.classify(input_path, "render")
    if ns == "adhoc":
        # Flat content-addressed pool, but still per-build: the <fp> keeps two
        # lvkit builds from colliding on the same <sha>.<ext> (classify's dir is
        # discarded here, so its <fp> level has to be re-added by hand).
        d = (
            cache_paths.global_cache_root()
            / "adhoc"
            / "render"
            / cache_paths.kind_fingerprint("render")
        )
        base = f"{cache_paths.sha256_file(input_path)}.{ext}"
    else:
        base = f"{input_path.stem}.{ext}"
    return d / base, d / f"{base}.meta.json", ns == "adhoc"


def _diff_ext(fmt: str) -> str:
    return {"html": "html", "json": "json"}.get(fmt, "txt")


def _diff_paths(
    before_path: Path, after_path: Path, fmt: str
) -> tuple[Path, Path, str, bool]:
    """``(body_path, meta_path, before_sha, is_adhoc)`` for a diff.

    Path-addressed by the AFTER (working-tree) VI — the side lvkit is handed as a
    real path — with the before-content hash and the format in the filename
    (lvkit is git-agnostic; the before side arrives only as bytes). Flat
    content-addressed when even the after side is adhoc.
    """
    ext = _diff_ext(fmt)
    before_sha = cache_paths.sha256_file(before_path)
    d, _, ns = cache_paths.classify(after_path, "diff")
    if ns == "adhoc":
        after_sha = cache_paths.sha256_file(after_path)
        d = (
            cache_paths.global_cache_root()
            / "adhoc"
            / "diff"
            / cache_paths.kind_fingerprint("diff")
        )
        base = f"{before_sha[:16]}_{after_sha[:16]}.{ext}"
    else:
        base = f"{after_path.stem}.{before_sha[:16]}.{ext}"
    return d / base, d / f"{base}.meta.json", before_sha, ns == "adhoc"


# ── read / write ────────────────────────────────────────────────────────────


def _touch(*paths: Path) -> None:
    """Bump mtime so an actively-used entry survives the access-time TTL."""
    now = time.time()
    for p in paths:
        try:
            os.utime(p, (now, now))
        except OSError:
            pass


def _read_if_fresh(
    vi_path: Path, body_path: Path, meta_path: Path, extra: dict
) -> str | None:
    if not body_path.exists():
        return None
    if not cache_paths.meta_fresh(vi_path, meta_path, extra=extra):
        return None
    try:
        text = body_path.read_text(encoding="utf-8")
    except OSError:
        return None
    _touch(body_path, meta_path)
    return text


def _write(
    body_path: Path, meta_path: Path, vi_path: Path, body: str, extra: dict
) -> None:
    body_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic body write (temp + replace) so a concurrent reader/warm never sees a
    # half-written file. The meta is written AFTER the body, so a hit always has
    # a complete body behind it.
    tmp = body_path.with_suffix(body_path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, body_path)
    cache_paths.write_meta(vi_path, meta_path, **extra)
    _sweep_once()


def render_slot(input_path: Path, fmt: str) -> Path:
    """The cache slot path a render of ``input_path`` occupies (for reporting)."""
    return _render_paths(input_path, fmt)[0]


def diff_slot(before_path: Path, after_path: Path, fmt: str) -> Path:
    """The cache slot path a diff of ``(before, after)`` occupies."""
    return _diff_paths(before_path, after_path, fmt)[0]


def lookup_render(input_path: Path, fmt: str, options: str, version: str) -> str | None:
    """Return cached render output for ``input_path`` if fresh, else ``None``."""
    body_path, meta_path, _ = _render_paths(input_path, fmt)
    return _read_if_fresh(
        input_path,
        body_path,
        meta_path,
        {
            "lvkit_version": version,
            "options": options,
            "text_encoding": labview_text_encoding(),
        },
    )


def store_render(
    input_path: Path, fmt: str, options: str, version: str, body: str
) -> Path:
    """Cache ``body`` as the render of ``input_path``; return the slot path."""
    body_path, meta_path, _ = _render_paths(input_path, fmt)
    _write(
        body_path,
        meta_path,
        input_path,
        body,
        {
            "lvkit_version": version,
            "options": options,
            "kind": "render",
            "text_encoding": labview_text_encoding(),
        },
    )
    return body_path


def lookup_diff(
    before_path: Path, after_path: Path, fmt: str, options: str, version: str
) -> str | None:
    """Return cached diff output for the ``(before, after)`` pair if fresh."""
    body_path, meta_path, before_sha, _ = _diff_paths(before_path, after_path, fmt)
    return _read_if_fresh(
        after_path,
        body_path,
        meta_path,
        {
            "lvkit_version": version,
            "options": options,
            "before_sha": before_sha,
            "text_encoding": labview_text_encoding(),
        },
    )


def store_diff(
    before_path: Path,
    after_path: Path,
    fmt: str,
    options: str,
    version: str,
    body: str,
) -> Path:
    """Cache ``body`` as the diff of ``(before, after)``; return the slot path."""
    body_path, meta_path, before_sha, _ = _diff_paths(before_path, after_path, fmt)
    _write(
        body_path,
        meta_path,
        after_path,
        body,
        {
            "lvkit_version": version,
            "options": options,
            "before_sha": before_sha,
            "kind": "diff",
            "text_encoding": labview_text_encoding(),
        },
    )
    return body_path


# ── TTL sweep (opportunistic, once per process) ─────────────────────────────

_swept = False


def _sweep_once() -> None:
    """Access-TTL cleanup, best-effort. Runs at most once per process AND at most
    once per ``LVKIT_SWEEP_INTERVAL_HOURS`` across ALL processes (a persisted
    ``<cache>/_last_sweep`` stamp), so the whole-cache walk is never a per-command
    cost — a command inside the window stats one file and returns. When it does
    run: ONE rule — retire a whole per-build ``<kind>/<fp>/`` dir once every file
    under it has gone untouched for the TTL (extract on its own longer horizon). No
    within-fingerprint file aging. Safe against a concurrent warm: a vanished file
    is skipped, and an in-use build is never idle (files are bumped on every hit).
    """
    global _swept
    if _swept:
        return
    _swept = True
    try:
        now = time.time()
        root = cache_paths.global_cache_root()
        stamp = root / "_last_sweep"
        interval = _sweep_interval_seconds()
        try:
            if stamp.exists() and (now - stamp.stat().st_mtime) < interval:
                return  # swept recently (by any process) -> skip the walk entirely
        except OSError:
            pass
        # Claim the sweep up front (refresh the stamp) so a burst of sibling
        # processes doesn't all walk the cache at once.
        try:
            root.mkdir(parents=True, exist_ok=True)
            stamp.write_text("", encoding="utf-8")
        except OSError:
            pass
        default_cutoff = now - _ttl_seconds()
        extract_cutoff = now - _ttl_seconds(_EXTRACT_TTL_DAYS, "LVKIT_EXTRACT_TTL_DAYS")
        # Every per-build <fp> dir: owned at <ns>/<slug>/<kind>/<fp>, adhoc at
        # adhoc/<kind>/<fp>. The <kind> is always the dir's PARENT, so pick the
        # horizon (extract is stickier) from d.parent.name.
        fp_dirs = (
            list((root / "projects").glob("*/*/*"))
            + list((root / "shared").glob("*/*/*/*"))
            + list((root / "adhoc").glob("*/*"))
        )
        for d in fp_dirs:
            if not d.is_dir():
                continue
            cutoff = extract_cutoff if d.parent.name == "extract" else default_cutoff
            _retire_if_idle(d, cutoff)
    except Exception:
        pass  # cache hygiene must never break a render


def _retire_if_idle(fp_dir: Path, cutoff: float) -> None:
    """Remove a whole per-build ``<kind>/<fp>/`` slice if its newest file (its
    last access) predates ``cutoff``. A dir with no files is left alone."""
    if not fp_dir.is_dir():
        return
    try:
        newest = 0.0
        for dirpath, _, files in os.walk(fp_dir):
            for name in files:
                try:
                    m = (Path(dirpath) / name).stat().st_mtime
                    if m > newest:
                        newest = m
                except OSError:
                    pass
        if newest and newest < cutoff:
            shutil.rmtree(fp_dir, ignore_errors=True)
    except OSError:
        pass
