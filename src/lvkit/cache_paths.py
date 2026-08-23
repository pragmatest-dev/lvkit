"""Cache location + freshness helpers — the lightweight, stdlib-only core of
lvkit's per-user cache.

Split out of :mod:`lvkit.extractor` (which imports pylabview) so the CLI's
render/diff *cache-hit* path can locate and validate a cached artifact WITHOUT
importing the graph/parser/pylabview stack (~250 ms). Nothing here imports
anything heavier than the stdlib and :mod:`lvkit.project_store`.

Cache layout (PROJECT-FIRST — each source's identity is named ONCE and every
artifact KIND hangs off it, so one project's whole cache is a single subtree you
can browse / move / drop as a unit; each path mirrors the source location so it's
readable)::

    <cache>/<ns>/<slug>/extract/<rel>/<stem>_BDHb.xml   + <stem>.meta.json
    <cache>/<ns>/<slug>/render/ <rel>/<stem>.<ext>      + <stem>.<ext>.meta.json
    <cache>/<ns>/<slug>/diff/   <rel>/<stem>.<b8>.html  + …
    <cache>/projects/<slug>/index/index.db
    <cache>/adhoc/<kind>/…                              (see below)

``<ns>`` answers "who OWNS this VI?": ``projects/<slug>`` (owned by a repo /
.lvkit project — ``<slug>`` = the project root), ``shared/vilib/<slug>`` or
``shared/userlib/<slug>`` (owned by a library install — ``<slug>`` = the vi.lib /
user.lib root), or ``adhoc`` (owned by NOBODY — a standalone file or throwaway
temp). :func:`classify` returns which. The owned namespaces name the ``<slug>``
identity ONCE and hang every ``<kind>`` off it, so a project/library reads as a
single browsable subtree. ``adhoc`` has no identity to name, so it drops the slug
level and is a flat per-kind pool (``adhoc/<kind>/…``) — path-addressed for
``extract``, content-addressed for ``render``/``diff`` (see :mod:`output_cache`),
TTL-swept. ``index`` is projects-only — shared/adhoc VIs are never indexed
standalone. The ``<slug>`` sits BELOW the namespace so the three namespaces stay
legibly separated at the top.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
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
    """Nearest ancestor that is a project/source root: one holding a ``.lvkit/``
    store, a git repo root (``.git``), or a LabVIEW project file (``*.lvproj``).
    Accepts a FILE or a DIRECTORY (a dir is checked itself, then its ancestors).
    Returns the root dir, or ``None`` for a path outside any project.

    A ``.lvclass``/``.lvlib`` dir is NOT a root — it's a component; the walk-up
    passes through it to the enclosing project, so the cache/scope covers the
    whole project rather than one class.
    """
    from lvkit.project_store import global_home

    start = vi_path if vi_path.is_dir() else vi_path.parent
    ancestors = (start, *start.parents)
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
    # LabVIEW-native project with no .lvkit/.git — the .lvproj dir is the source
    # root (a plain LabVIEW project folder still gets a coherent cache bucket).
    for anc in ancestors:
        if any(anc.glob("*.lvproj")):
            return anc
    return None


def classify(vi_path: Path, kind: str) -> tuple[Path, str, str]:
    """Map ``vi_path`` to ``(cache_dir, source_label, namespace)`` for one
    artifact ``kind`` (``"extract"`` | ``"render"`` | ``"diff"``).

    ``cache_dir`` is ``<cache>/<ns>/<slug>/<kind>/<rel-parent>`` — project-first:
    the VI's identity (namespace + slug) is named ONCE and the ``kind`` hangs off
    it, so one project's whole cache is a single subtree and sibling VIs of the
    same kind share a dir. ``namespace`` is ``"projects"``, ``"shared"``, or
    ``"adhoc"`` — callers path-address the first two and content-address
    ``adhoc`` (its paths never repeat).
    """
    resolved = vi_path.resolve()
    cache = global_cache_root()

    vilib = _vilib_root
    if vilib is not None:
        rel = _rel_under(resolved, vilib)
        if rel is not None:
            d = cache / "shared" / "vilib" / _slug(vilib) / kind / rel.parent
            return d, str(rel), "shared"

    userlib = _userlib_root
    if userlib is not None:
        rel = _rel_under(resolved, userlib)
        if rel is not None:
            d = cache / "shared" / "userlib" / _slug(userlib) / kind / rel.parent
            return d, str(rel), "shared"

    project = _project_root_for(resolved)
    if project is not None:
        proj_abs = project.resolve()
        rel = resolved.relative_to(proj_abs)
        d = cache / "projects" / _slug(proj_abs) / kind / rel.parent
        return d, str(rel), "projects"

    # adhoc has NO owner (not a project, not a library) -> no identity slug to
    # name; kind sits right under the namespace and the parent-dir slug is just
    # the path-addressing key (render/diff override this to a flat content pool).
    return cache / "adhoc" / kind / _slug(resolved.parent), resolved.name, "adhoc"


def cache_target(vi_path: Path, kind: str) -> Path:
    """The per-VI cache directory for ``vi_path`` and ``kind`` (created)."""
    target, _, _ = classify(vi_path, kind)
    target.mkdir(parents=True, exist_ok=True)
    return target


# ── Content hashing + freshness ─────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# Top-level package subdirs that DEMONSTRABLY feed NO cached artifact — verified
# absent from the import closure of the fact/render producers and pinned by
# ``test_facts_fingerprint_skips_only_non_facts_dirs``. Skipping them means
# editing codegen / docs / the formula transpiler / the MCP transport layer
# doesn't force a needless rebuild. (Verified: ``render/`` imports none of these
# — Formula NODES are drawn from graph models, not the ``formula/`` transpiler.)
#
# This is an EXCLUDE list on purpose: the default for any file — including a
# brand-new module or ``data/`` table — is to be hashed, so a new dependency can
# never silently escape the fingerprint. The only failure mode (a dep ADDED under
# one of these dirs) trips the guard test, which fails loudly rather than letting
# staleness back in.
_FINGERPRINT_SKIP_DIRS = frozenset({"codegen", "docs", "formula", "mcp"})

# A FROZEN distribution (PyInstaller) ships compiled code, not the ``.py``
# sources the fingerprints hash, so neither fingerprint is computable at runtime
# there (``source_fingerprint`` would see only the bundled data files — blind to
# code — and ``extraction_fingerprint``'s fixed include list would not exist at
# all). The build instead computes BOTH from the live sources and writes them to
# this file, bundled next to the package (editors/vscode/build/build-binary.sh +
# embed_fingerprints.py); at runtime each fingerprint returns the embedded value
# when present, so a built binary's caches stay correct AND code-aware — the same
# keys a source/wheel install computes — instead of degrading or crashing. Absent
# in a normal source/wheel install, where the live computation is authoritative.
_BUILD_FINGERPRINTS_FILE = "_build_fingerprints.json"


@functools.lru_cache(maxsize=1)
def _embedded_fingerprints() -> dict[str, str]:
    """The build-time fingerprints bundled into a frozen distribution, or an
    empty dict for a live source/wheel install (no such file)."""
    # This module lives inside the package, so its own directory IS the package
    # root — no need to import the top-level package to find it.
    f = Path(__file__).resolve().parent / _BUILD_FINGERPRINTS_FILE
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return {str(k): str(v) for k, v in raw.items()}


@functools.lru_cache(maxsize=1)
def source_fingerprint() -> str:
    """A hash of lvkit's own SOURCE + bundled data, so EVERY derived cache (the
    SQLite index AND the render/diff output cache) self-invalidates the moment
    that code changes — with no manual version to bump. This is the SINGLE shared
    invalidation used by all caches; do not reintroduce a per-cache version.

    A cache keyed only on input VI bytes cannot see a change to lvkit's own logic
    (the VI bytes are identical) — so an edit to the parser/graph/render/index
    code, or to a ``data/`` primitive/vilib table, would otherwise keep serving
    output built by the OLD code. That is silent staleness. A hand-bumped version
    can't defend against it because in active development the bump is forgotten
    far more often than not.

    Hashes every file in the package EXCEPT the ``_FINGERPRINT_SKIP_DIRS`` known
    to feed no cache. Skipping is done by directory (a coarse, verifiable unit),
    never by hand-picking modules — an *include* list is a guess that can miss a
    transitive dep and reintroduce staleness, whereas defaulting to "hash it"
    over-invalidates at worst (one extra cold rebuild). Memoized: the source
    cannot change within a live process (Python does not hot-reload), and a dev
    editing lvkit restarts it before the next run anyway.

    In a frozen build the sources aren't on disk, so use the build-time value
    embedded next to the package (``_BUILD_FINGERPRINTS_FILE``) instead of
    hashing the code-blind data-file remnant.
    """
    embedded = _embedded_fingerprints().get("source")
    if embedded:
        return embedded

    pkg = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for f in sorted(pkg.rglob("*")):
        if not f.is_file() or f.suffix == ".pyc" or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(pkg)
        if rel.parts[0] in _FINGERPRINT_SKIP_DIRS:
            continue
        h.update(rel.as_posix().encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# The ONLY modules that determine pylabview's emitted XML: the in-process
# monkeypatches (which alter what pylabview writes), the extraction driver (read
# options + the normalize call), and the text-encoding helpers (textcp choice +
# ``normalize_extracted_xml``). Deliberately a tiny INCLUDE list, not the
# package-wide ``source_fingerprint`` exclude list: extraction output does NOT
# depend on parser/graph/render, so re-running pylabview over the whole corpus
# on every unrelated edit would be pathologically slow. Extraction changes
# rarely (basically only when a monkeypatch changes), so this invalidates rarely.
_EXTRACTION_CODE_FILES = (
    "_pylabview_patches.py",
    "extractor.py",
    "text_encoding.py",
)


@functools.lru_cache(maxsize=1)
def extraction_fingerprint() -> str:
    """A hash of ONLY the extraction code (``_EXTRACTION_CODE_FILES``), so the
    extraction cache rebuilds when HOW we extract changes — a new/edited
    pylabview monkeypatch, a read option, the normalize pass — but NOT when
    post-extraction code (parser/graph/render, keyed on ``source_fingerprint``)
    changes. Kept separate from ``source_fingerprint`` on purpose: extraction is
    the expensive, rarely-changing stage.

    Reads the extraction ``.py`` sources when they are on disk (source checkout,
    installed wheel, Cloud Run) — the precise, narrow signal. In a FROZEN build
    (PyInstaller ships compiled code, not ``.py`` sources) those files are absent,
    so it uses the build-time value embedded next to the package instead. The
    sentinel return is a last-resort defense for the unexpected case of a frozen
    build with neither the sources nor an embedded fingerprint — it never crashes
    trying to read its own source at runtime.
    """
    embedded = _embedded_fingerprints().get("extraction")
    if embedded:
        return embedded

    pkg = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for rel in _EXTRACTION_CODE_FILES:
        try:
            data = (pkg / rel).read_bytes()
        except OSError:
            # Defensive only: a frozen build missing BOTH its .py sources and the
            # embedded fingerprint (a broken build — build-binary.sh always embeds
            # it, which short-circuits above). Return a stable sentinel rather
            # than crash; a correctly built binary never reaches here.
            return "extraction-unavailable"
        h.update(rel.encode())
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


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


def cleanup_legacy_cache() -> None:
    """Delete the abandoned KIND-FIRST cache trees, once, on first use.

    lvkit's cache was briefly kind-first — ``<cache>/{extract,render,diff,index}/
    <ns>/<slug>/…`` (shipped v0.5.7–v0.5.8). It is now PROJECT-FIRST
    (``<cache>/<ns>/<slug>/<kind>/…``), so those four top-level trees are dead
    weight: never read, and with no cache GC they would linger forever. Drop them
    — re-extraction/-render repopulates the new project-first locations lazily.

    We DELETE rather than rename because a rebuild is lvkit's model on ANY version
    change (the index self-invalidates on a code change; render/diff carry the
    lvkit version in their freshness key), so preserving the old artifacts buys
    nothing. Safe: ``extract``/``render``/``diff``/``index`` are never top-level
    dirs under project-first (slugs live below ``projects``/``shared``/``adhoc``),
    so this can't touch live data. Best-effort — a failure just leaves a tree for
    a later run to clean.
    """
    root = global_cache_root()
    for kind in ("extract", "render", "diff", "index"):
        legacy = root / kind
        if legacy.is_dir():
            shutil.rmtree(legacy, ignore_errors=True)
