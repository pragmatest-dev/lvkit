"""Content-addressed **output** cache for the render/diff pipeline.

The extracted-XML cache (``extractor``) already skips re-*extraction*; this skips
the whole *build* (parse -> graph -> scene -> render) when the same inputs have
already produced output. Stdlib-only (imports only :mod:`lvkit.cache_paths`) so
the CLI can check for a hit BEFORE importing the graph/parser/pylabview stack
(~250 ms) — a hit path that never touches the heavy machinery.

Addressing mirrors extraction (see :mod:`lvkit.cache_paths`):

* A VI with a stable project/library path is **path-addressed** — one slot per
  VI, mirrored under ``<cache>/render/<ns>/<rel>/<stem>.<ext>``, overwritten in
  place when the VI changes. Bounded by the number of VIs; it plateaus.
* An ``adhoc`` input (a throwaway ``mkdtemp`` git-blob, or a standalone VI
  outside any repo) has no reusable path, so it is **content-addressed** in a
  flat ``<cache>/render/adhoc/<sha>.<ext>`` and swept by TTL.

Freshness = the VI-content signal ``cache_paths.meta_fresh`` already uses PLUS
the lvkit ``version``, text encoding, and an ``options`` tag — so a VI edit, an
lvkit upgrade, a system-code-page change, or different ``--format``/``--theme``
is a miss.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from lvkit import cache_paths
from lvkit.text_encoding import labview_text_encoding

# The genuinely-growing surfaces get an access-time TTL (a diff/adhoc entry is
# worthless once its inputs move on — regenerate on demand). Path-addressed
# render/extract project slots are NOT swept: they overwrite in place and
# plateau at ~one-per-VI. Override the clock with LVKIT_CACHE_TTL_DAYS.
_DEFAULT_TTL_DAYS = 7.0


def _ttl_seconds() -> float:
    try:
        return float(os.environ.get("LVKIT_CACHE_TTL_DAYS", _DEFAULT_TTL_DAYS)) * 86400
    except ValueError:
        return _DEFAULT_TTL_DAYS * 86400


def _ext_for(fmt: str) -> str:
    return "svg" if fmt == "svg" else "html"


# ── path resolution ─────────────────────────────────────────────────────────


def _render_paths(input_path: Path, fmt: str) -> tuple[Path, Path, bool]:
    """``(body_path, meta_path, is_adhoc)`` for a render of ``input_path``.

    Path-addressed for project/shared VIs; flat content-addressed for adhoc.
    """
    ext = _ext_for(fmt)
    d, _label, ns = cache_paths.classify(input_path, "render")
    if ns == "adhoc":
        d = cache_paths.global_cache_root() / "render" / "adhoc"
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
    d, _label, ns = cache_paths.classify(after_path, "diff")
    if ns == "adhoc":
        after_sha = cache_paths.sha256_file(after_path)
        d = cache_paths.global_cache_root() / "diff" / "adhoc"
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
    body_path, meta_path, _adhoc = _render_paths(input_path, fmt)
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
    body_path, meta_path, _adhoc = _render_paths(input_path, fmt)
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
    body_path, meta_path, before_sha, _adhoc = _diff_paths(before_path, after_path, fmt)
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
    body_path, meta_path, before_sha, _adhoc = _diff_paths(before_path, after_path, fmt)
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
    """Delete access-stale entries in the growing trees. Runs at most once per
    process (on the first cache write), a single cheap ``stat``+``unlink`` pass —
    no daemon, no size accounting. Best-effort: races with a concurrent warm just
    skip vanished files.
    """
    global _swept
    if _swept:
        return
    _swept = True
    try:
        cutoff = time.time() - _ttl_seconds()
        root = cache_paths.global_cache_root()
        # Everything under diff/ (before-versions accumulate as history moves) +
        # the adhoc subtree of every kind (ephemeral temp/blob inputs).
        targets = [
            root / "diff",
            root / "render" / "adhoc",
            root / "extract" / "adhoc",
        ]
        for base in targets:
            if not base.is_dir():
                continue
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    fp = Path(dirpath) / name
                    try:
                        if fp.stat().st_mtime < cutoff:
                            fp.unlink()
                    except OSError:
                        pass
    except Exception:
        pass  # cache hygiene must never break a render
