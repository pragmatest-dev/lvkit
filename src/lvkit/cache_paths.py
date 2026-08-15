"""Cache location + freshness helpers — the lightweight, stdlib-only core of
lvkit's per-user cache.

Split out of :mod:`lvkit.extractor` (which imports pylabview) so the CLI's
render/diff *cache-hit* path can locate and validate a cached artifact WITHOUT
importing the graph/parser/pylabview stack (~250 ms). Nothing here imports
anything heavier than the stdlib and :mod:`lvkit.project_store`.

Cache layout (three artifact KINDS, each its own tree, each mirroring the
source location so a printed path is readable)::

    <cache>/extract/<ns>/<rel>/<stem>_BDHb.xml   + <stem>.meta.json
    <cache>/render/ <ns>/<rel>/<stem>.<ext>      + <stem>.<ext>.meta.json
    <cache>/diff/   <ns>/<rel>/<stem>.<b8>.html  + …

``<ns>`` is one of ``projects/<slug>`` (a VI inside a repo / .lvkit project),
``shared/vilib/<slug>`` or ``shared/userlib/<slug>`` (a VI under a resolved
library root), or ``adhoc`` (a VI outside any of those — a standalone file or a
throwaway temp). :func:`classify` returns which, so callers can path-address the
stable namespaces and content-address ``adhoc`` (whose paths never repeat).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# ── Cache root ──────────────────────────────────────────────────────────────


def global_cache_root() -> Path:
    """Root of lvkit's per-user cache — never in the user's repo.

    ``$LVKIT_CACHE_DIR`` if set (test hook + power-user/CI override), else
    ``<global-home>/cache`` — i.e. ``~/.lvkit/cache``. The home location comes
    from :func:`lvkit.project_store.global_home` (the single source of truth).
    """
    env = os.environ.get("LVKIT_CACHE_DIR")
    if env:
        return Path(env)
    from lvkit.project_store import global_home

    return global_home() / "cache"


# ── Run-scoped extraction roots ─────────────────────────────────────────────
# The resolved <vilib>/<userlib> roots for the current run, set once at
# `InMemoryVIGraph.set_library_roots` so `classify` can prefix-match a VI to its
# tier without re-guessing from the path. One run at a time (same single-process
# concurrency caveat the extractor has always had).
_vilib_root: Path | None = None
_userlib_root: Path | None = None


def set_extraction_roots(*, vilib_root: Path | None, userlib_root: Path | None) -> None:
    """Record the run's resolved library roots for cache classification.

    Roots are resolved to absolute paths so prefix-matching in :func:`classify`
    is stable regardless of how a VI path was spelled. Passing ``None`` clears
    that root.
    """
    global _vilib_root, _userlib_root
    _vilib_root = vilib_root.resolve() if vilib_root is not None else None
    _userlib_root = userlib_root.resolve() if userlib_root is not None else None


def clear_extraction_roots() -> None:
    """Forget the run's library roots (fall back to project/adhoc)."""
    global _vilib_root, _userlib_root
    _vilib_root = None
    _userlib_root = None


# ── Namespace classification ────────────────────────────────────────────────


def _slug(path: Path) -> str:
    """Readable per-project cache namespace: the absolute path with its
    separators turned into ``-`` (``/home/u/repo`` -> ``-home-u-repo``), the way
    ``~/.claude/projects`` names per-repo dirs. No hash — you can read the cache
    and see which project a file came from. Windows drive colons and backslashes
    collapse the same way (``C:\\proj`` -> ``C--proj``).
    """
    return str(path).replace(":", "-").replace("\\", "-").replace("/", "-")


def _rel_under(child: Path, parent: Path | None) -> Path | None:
    """``child`` relative to ``parent`` if it is under it, else ``None``.
    ``parent`` is already resolved; ``child`` is resolved here so the prefix
    test is spelling-independent.
    """
    if parent is None:
        return None
    try:
        return child.resolve().relative_to(parent)
    except ValueError:
        return None


def _project_root_for(vi_path: Path) -> Path | None:
    """Nearest ancestor that is a project root: one holding a ``.lvkit/`` store
    or a git repo root (``.git``). Returns the root dir, or ``None`` for a VI
    outside any project.
    """
    from lvkit.project_store import global_home

    ancestors = (vi_path.parent, *vi_path.parent.parents)
    # The user's GLOBAL store (~/.lvkit) is not a project marker — skip it, or
    # every VI under $HOME collapses into one hash($HOME) bucket.
    home = global_home().resolve()
    # An EXISTING .lvkit/ store wins even when a NEARER ancestor is its own git
    # repo: vendored corpora (each carrying a .git) share the outer project's
    # cache instead of sprouting a .lvkit/ inside every clone.
    for anc in ancestors:
        store = anc / ".lvkit"
        if store.is_dir() and store.resolve() != home:
            return anc
    for anc in ancestors:
        if (anc / ".git").exists():
            return anc
    return None


def classify(vi_path: Path, kind: str) -> tuple[Path, str, str]:
    """Map ``vi_path`` to ``(cache_dir, source_label, namespace)`` for one
    artifact ``kind`` (``"extract"`` | ``"render"`` | ``"diff"``).

    ``cache_dir`` is ``<cache>/<kind>/<ns>/<rel-parent>`` — the VI's parent dir
    mirrored under the kind tree, so sibling VIs share a dir. ``namespace`` is
    ``"projects"``, ``"shared"``, or ``"adhoc"`` — callers path-address the
    first two and content-address ``adhoc`` (its paths never repeat).
    """
    resolved = vi_path.resolve()
    root = global_cache_root() / kind

    vilib = _vilib_root
    if vilib is not None:
        rel = _rel_under(resolved, vilib)
        if rel is not None:
            d = root / "shared" / "vilib" / _slug(vilib) / rel.parent
            return d, str(rel), "shared"

    userlib = _userlib_root
    if userlib is not None:
        rel = _rel_under(resolved, userlib)
        if rel is not None:
            d = root / "shared" / "userlib" / _slug(userlib) / rel.parent
            return d, str(rel), "shared"

    project = _project_root_for(resolved)
    if project is not None:
        proj_abs = project.resolve()
        rel = resolved.relative_to(proj_abs)
        return root / "projects" / _slug(proj_abs) / rel.parent, str(rel), "projects"

    return root / "adhoc" / _slug(resolved.parent), resolved.name, "adhoc"


def cache_target(vi_path: Path, kind: str) -> Path:
    """The per-VI cache directory for ``vi_path`` and ``kind`` (created)."""
    target, _, _ = classify(vi_path, kind)
    target.mkdir(parents=True, exist_ok=True)
    return target


# ── Content hashing + freshness ─────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def meta_fresh(vi_path: Path, meta_path: Path, extra: dict | None = None) -> bool:
    """True if the cached artifact described by ``meta_path`` is still valid for
    ``vi_path``.

    Fast-path on unchanged ``(mtime, size)``; otherwise a content ``sha256``
    compare (robust to clone/checkout mtime resets), refreshing the recorded
    mtime on a content match. When ``extra`` is given (e.g. lvkit version +
    render options), every key must ALSO match the recorded meta — a version
    bump or an options change is a miss even when the VI bytes are unchanged.
    """
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if extra:
        for k, v in extra.items():
            if meta.get(k) != v:
                return False
    try:
        st = vi_path.stat()
    except OSError:
        return False
    if meta.get("size") == st.st_size and meta.get("mtime") == st.st_mtime:
        return True
    if meta.get("sha256") == sha256_file(vi_path):
        meta["mtime"] = st.st_mtime
        meta["size"] = st.st_size
        try:
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            pass
        return True
    return False


def write_meta(vi_path: Path, meta_path: Path, **extra: object) -> None:
    """Write a freshness sidecar for ``vi_path``: its content ``sha256`` +
    ``mtime``/``size`` fast-path fields, plus any ``extra`` fields the caller
    wants matched later (lvkit version, render options, tool name, …).
    """
    st = vi_path.stat()
    meta: dict[str, object] = {
        "sha256": sha256_file(vi_path),
        "mtime": st.st_mtime,
        "size": st.st_size,
    }
    meta.update(extra)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


# ── One-time migration of the pre-kind extraction cache ─────────────────────


def migrate_legacy_extract() -> None:
    """Move a pre-``extract/`` extraction cache into the new kind tree.

    Older lvkit put extractions at ``<cache>/{projects,shared,adhoc}/…``; they
    now live under ``<cache>/extract/…``. This renames the legacy top-level dirs
    under ``extract/`` once — an instant same-filesystem move, no re-extraction.
    Best-effort: any failure just leaves the legacy dir (a later lazy
    re-extraction repopulates the new location). ``llb/`` staging is unaffected.
    """
    root = global_cache_root()
    extract = root / "extract"
    for ns in ("projects", "shared", "adhoc"):
        legacy = root / ns
        if not legacy.is_dir():
            continue
        dest = extract / ns
        if dest.exists():
            # already migrated (or a fresh install made it) — leave legacy be
            continue
        try:
            extract.mkdir(parents=True, exist_ok=True)
            legacy.rename(dest)
        except OSError:
            pass
