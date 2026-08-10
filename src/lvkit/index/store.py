"""SQLite persistence for the facts index.

``~/.lvkit/cache/index/projects/<slug>/index.db`` — one DB per project root,
``<slug>`` from ``cache_paths._slug(project_root)`` (the same readable,
path-derived slug the extraction cache uses for per-project namespaces).

Built directly from the resolved ``project_root`` (not
``cache_paths.classify()``): ``classify()`` intentionally makes a *vendored*
corpus with its own ``.git`` (e.g. a sample repo cloned under
``.lvkit/cache/samples/``) share its *outer* project's cache namespace, so
many VIs extracted from many corpora don't sprawl into many extraction cache
dirs. The index is different — a caller who runs ``lvkit index
<path-to-that-corpus>`` is deliberately scoping the index to that corpus, and
expects its own DB, not to be silently folded into the outer project's.

WAL mode. Tables: ``vis``, ``terminals``, ``constants``,
``calls(caller_path, callee_key)``, ``type_uses``, ``class_facts``, and a
``meta(vi_path, content_sha)`` freshness row per VI. Upsert by path: ``save()``
deletes then reinserts every row belonging to each given ``VIFacts.path`` (safe
for both a full rebuild and a partial refresh).

**The whole DB is a CACHE of derived facts.** Per-VI freshness is keyed on VI
*bytes* (``meta.content_sha``); the DB as a whole is keyed on a fingerprint of
lvkit's own facts-producing source (``index_meta`` — see ``_facts_fingerprint``)
so it self-invalidates the instant that code or bundled data changes, with no
version bump to forget. See ``_ensure_facts_version``.
"""

from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .. import cache_paths
from .model import (
    ClassFact,
    ConstantFact,
    LVProjMemberFact,
    TerminalFact,
    VIFacts,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vis (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    qualified_name TEXT,
    library TEXT,
    is_stub INTEGER NOT NULL DEFAULT 0,
    content_sha TEXT NOT NULL DEFAULT '',
    impact_score INTEGER NOT NULL DEFAULT 0,
    callers_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_vis_name ON vis(name);
CREATE INDEX IF NOT EXISTS idx_vis_qualified_name ON vis(qualified_name);

CREATE TABLE IF NOT EXISTS terminals (
    vi_path TEXT NOT NULL,
    ord INTEGER NOT NULL,
    name TEXT,
    direction TEXT NOT NULL,
    is_indicator INTEGER NOT NULL,
    is_public INTEGER NOT NULL,
    control_type TEXT,
    py_type TEXT NOT NULL,
    is_error_cluster INTEGER NOT NULL,
    field_names TEXT NOT NULL DEFAULT '[]',
    fp_dco_uid TEXT,
    lv_type TEXT NOT NULL DEFAULT 'Any',
    enum_values TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_terminals_vi ON terminals(vi_path);
CREATE INDEX IF NOT EXISTS idx_terminals_name ON terminals(name);
CREATE INDEX IF NOT EXISTS idx_terminals_direction ON terminals(direction);

CREATE TABLE IF NOT EXISTS constants (
    vi_path TEXT NOT NULL,
    ord INTEGER NOT NULL,
    value TEXT NOT NULL,
    label TEXT,
    py_type TEXT NOT NULL,
    lv_type TEXT NOT NULL DEFAULT '?',
    wired_to TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_constants_vi ON constants(vi_path);
CREATE INDEX IF NOT EXISTS idx_constants_wired ON constants(wired_to);

CREATE TABLE IF NOT EXISTS calls (
    caller_path TEXT NOT NULL,
    callee_key TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_path);
CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_key);

CREATE TABLE IF NOT EXISTS type_uses (
    vi_path TEXT NOT NULL,
    type_key TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_type_uses_vi ON type_uses(vi_path);
CREATE INDEX IF NOT EXISTS idx_type_uses_key ON type_uses(type_key);

CREATE TABLE IF NOT EXISTS class_facts (
    vi_path TEXT PRIMARY KEY,
    owning_class TEXT NOT NULL,
    parent TEXT,
    scope TEXT,
    is_accessor INTEGER NOT NULL DEFAULT 0,
    accessor_field TEXT,
    private_data TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS lvproj_members (
    lvproj_path TEXT NOT NULL,
    lvproj_name TEXT NOT NULL,
    member_name TEXT NOT NULL,
    member_url TEXT NOT NULL,
    resolved_path TEXT,
    member_type TEXT NOT NULL,
    is_in_repo INTEGER NOT NULL,
    target TEXT NOT NULL,
    is_dependency INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lvproj_members_proj
    ON lvproj_members(lvproj_path);
CREATE INDEX IF NOT EXISTS idx_lvproj_members_resolved
    ON lvproj_members(resolved_path);

CREATE TABLE IF NOT EXISTS meta (
    vi_path TEXT PRIMARY KEY,
    content_sha TEXT NOT NULL
);
"""

_CHILD_TABLES = ("terminals", "constants", "calls", "type_uses")

# Every table that holds DERIVED facts — a pure cache of what lvkit's parser
# produces from the VIs. Dropped wholesale when the facts fingerprint changes
# (``_ensure_facts_version``). ``index_meta`` (the fingerprint itself) is NOT
# here — it survives the wipe so the new fingerprint can be stamped onto it.
_ALL_TABLES = (
    "vis", "terminals", "constants", "calls", "type_uses", "class_facts",
    "lvproj_members", "meta",
)


# Top-level package subdirs that DEMONSTRABLY don't feed the index — verified
# absent from the import closure of ``index.build``/``index.store`` (the code
# that actually produces facts), and pinned by
# ``test_facts_fingerprint_skips_only_non_facts_dirs``. Skipping them means
# editing codegen / docs / the formula transpiler / the MCP transport layer
# doesn't force a needless full-corpus rebuild.
#
# This is an EXCLUDE list on purpose: the default for any file — including a
# brand-new module or ``data/`` table — is to be hashed, so a new facts
# dependency can never silently escape the fingerprint. The only failure mode
# (a facts dep ADDED under one of these dirs) trips the guard test, which fails
# loudly rather than letting staleness back in.
_FINGERPRINT_SKIP_DIRS = frozenset({"codegen", "docs", "formula", "mcp"})


@functools.lru_cache(maxsize=1)
def _facts_fingerprint() -> str:
    """A hash of lvkit's own facts-producing SOURCE + bundled data, so the index
    self-invalidates the moment that changes — with no manual version to bump.

    The index is a CACHE of what lvkit derives from each VI. Per-VI freshness is
    keyed on VI bytes (``meta.content_sha``), but that cannot see a change to
    lvkit's *extraction logic* (the VI bytes are identical) — so an edit to the
    parser/graph/index code, or to a ``data/`` primitive/vilib table, would
    otherwise keep serving facts built by the OLD code. That is silent staleness,
    and it corrupts everything reading the index: queries, the MCP server, and
    evals. A hand-bumped ``SCHEMA_VERSION`` can't defend against it because in
    active development the bump is forgotten far more often than not.

    Hashes every file in the package EXCEPT the ``_FINGERPRINT_SKIP_DIRS`` known
    not to feed facts. Skipping is done by directory (a coarse, verifiable unit),
    never by hand-picking the facts modules — an *include* list is a guess that
    can miss a transitive dep and reintroduce staleness, whereas defaulting to
    "hash it" over-invalidates at worst (one extra cold rebuild). Memoized: the
    source cannot change within a live process (Python does not hot-reload), and
    a dev editing lvkit restarts it before the next run anyway.
    """
    import lvkit
    pkg = Path(lvkit.__file__).resolve().parent
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


def db_path(project_root: Path) -> Path:
    """The SQLite file for ``project_root``'s index (parent dir created)."""
    slug = cache_paths._slug(project_root.resolve())
    d = cache_paths.global_cache_root() / "index" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d / "index.db"


def _connect(project_root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(project_root))
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_facts_version(conn)
    conn.executescript(_SCHEMA)
    return conn


def _ensure_facts_version(conn: sqlite3.Connection) -> None:
    """Invalidate the whole index when lvkit's facts-producing code changed.

    Runs at the single point every caller funnels through (``_connect``), before
    the schema is (re)created. Compares the fingerprint stored in this DB against
    the running code's :func:`_facts_fingerprint`. On a match, nothing to do. On
    a mismatch — or a DB that predates the fingerprint (an older lvkit, or the
    former hand-bumped ``schema_version`` scheme) — every derived-facts table is
    dropped so the next build is a full, cold rebuild from the VIs. This is what
    makes a parser change (identical VI bytes) actually re-derive facts instead
    of silently reusing the stale ones; NO migrate-in-place, because a derived
    cache with defaulted columns that never recompute IS the staleness bug.

    Deliberately additive-safe: intentionally drops and lets the caller rebuild
    rather than trying to preserve rows — the rows are all rederivable from VIs.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS index_meta ("
        "id INTEGER PRIMARY KEY CHECK (id = 0), fingerprint TEXT NOT NULL)"
    )
    current = _facts_fingerprint()
    row = conn.execute(
        "SELECT fingerprint FROM index_meta WHERE id = 0"
    ).fetchone()
    if row is not None and row[0] == current:
        return
    for table in _ALL_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(
        "INSERT INTO index_meta(id, fingerprint) VALUES (0, ?) "
        "ON CONFLICT(id) DO UPDATE SET fingerprint = excluded.fingerprint",
        (current,),
    )
    conn.commit()


def _prior_container_facts(
    conn: sqlite3.Connection, path: str,
) -> tuple[str | None, str | None, ClassFact | None]:
    """Read the prior ``content_sha``, ``library``, and ``class_fact`` for
    ``path`` (all None if the VI has never been saved).

    ``meta.content_sha`` is the freshness authority (written per-save, see
    ``save()`` below); the ``vis``/``class_facts`` rows for the same path are
    the container fields a progressive/partial re-save must not clobber with
    NULLs when the VI's bytes haven't changed.
    """
    row = conn.execute(
        "SELECT content_sha FROM meta WHERE vi_path = ?", (path,)
    ).fetchone()
    if row is None:
        return None, None, None
    prior_sha = row[0]

    lib_row = conn.execute(
        "SELECT library FROM vis WHERE path = ?", (path,)
    ).fetchone()
    prior_library = lib_row[0] if lib_row is not None else None

    cf_row = conn.execute(
        "SELECT owning_class, parent, scope, is_accessor, accessor_field, "
        "private_data FROM class_facts WHERE vi_path = ?",
        (path,),
    ).fetchone()
    prior_class_fact = (
        ClassFact(
            owning_class=cf_row[0],
            parent=cf_row[1],
            scope=cf_row[2],
            is_accessor=bool(cf_row[3]),
            accessor_field=cf_row[4],
            private_data=json.loads(cf_row[5]),
        )
        if cf_row is not None
        else None
    )
    return prior_sha, prior_library, prior_class_fact


def save(project_root: Path, vis: Iterable[VIFacts]) -> None:
    """Upsert every given ``VIFacts`` row into the project's index DB.

    Coalesce-on-save: when a prior row exists for the same path AND the same
    ``content_sha`` (same VI bytes — an incoming NULL container field is
    ignorance from a partial/progressive build, not truth), the nullable
    container fields (``library`` and the whole ``class_fact`` row) fall back
    to the prior value instead of being clobbered. A changed sha (or no prior
    row) means today's behavior: trust the incoming facts fully. The
    terminals/constants/calls/type_uses child tables are intrinsic to a
    single-VI load and are never coalesced — always overwritten.
    """
    conn = _connect(project_root)
    try:
        with conn:
            for f in vis:
                prior_sha, prior_library, prior_class_fact = (
                    _prior_container_facts(conn, f.path)
                )
                same_vi = prior_sha is not None and prior_sha == f.content_sha
                if not same_vi:
                    library = f.library
                    class_fact = f.class_fact
                else:
                    library = f.library if f.library is not None else prior_library
                    if f.class_fact is None:
                        # No class facts at all from this (partial) load —
                        # preserve the entire prior row wholesale.
                        class_fact = prior_class_fact
                    elif prior_class_fact is None:
                        class_fact = f.class_fact
                    else:
                        cfc = f.class_fact
                        class_fact = ClassFact(
                            owning_class=(
                                cfc.owning_class
                                if cfc.owning_class is not None
                                else prior_class_fact.owning_class
                            ),
                            parent=(
                                cfc.parent
                                if cfc.parent is not None
                                else prior_class_fact.parent
                            ),
                            scope=(
                                cfc.scope
                                if cfc.scope is not None
                                else prior_class_fact.scope
                            ),
                            is_accessor=cfc.is_accessor,
                            accessor_field=(
                                cfc.accessor_field
                                if cfc.accessor_field is not None
                                else prior_class_fact.accessor_field
                            ),
                            private_data=(
                                cfc.private_data
                                if cfc.private_data
                                else prior_class_fact.private_data
                            ),
                        )

                _delete_vi(conn, f.path)
                conn.execute(
                    "INSERT INTO vis(path, name, qualified_name, library, "
                    "is_stub, content_sha, impact_score, callers_count) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        f.path, f.name, f.qualified_name, library,
                        int(f.is_stub), f.content_sha, f.impact_score,
                        f.callers_count,
                    ),
                )
                conn.executemany(
                    "INSERT INTO terminals(vi_path, ord, name, direction, "
                    "is_indicator, is_public, control_type, py_type, "
                    "is_error_cluster, field_names, fp_dco_uid, lv_type, "
                    "enum_values) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            f.path, i, t.name, t.direction, int(t.is_indicator),
                            int(t.is_public), t.control_type, t.py_type,
                            int(t.is_error_cluster), json.dumps(t.field_names),
                            t.fp_dco_uid, t.lv_type, json.dumps(t.enum_values),
                        )
                        for i, t in enumerate(f.terminals)
                    ],
                )
                conn.executemany(
                    "INSERT INTO constants(vi_path, ord, value, label, "
                    "py_type, lv_type, wired_to) VALUES (?,?,?,?,?,?,?)",
                    [
                        (
                            f.path, i, c.value, c.label, c.py_type, c.lv_type,
                            c.wired_to,
                        )
                        for i, c in enumerate(f.constants)
                    ],
                )
                conn.executemany(
                    "INSERT INTO calls(caller_path, callee_key) VALUES (?,?)",
                    [(f.path, callee) for callee in f.calls],
                )
                conn.executemany(
                    "INSERT INTO type_uses(vi_path, type_key) VALUES (?,?)",
                    [(f.path, type_key) for type_key in f.type_uses],
                )
                if class_fact is not None:
                    cf = class_fact
                    conn.execute(
                        "INSERT INTO class_facts(vi_path, owning_class, parent, "
                        "scope, is_accessor, accessor_field, private_data) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            f.path, cf.owning_class, cf.parent, cf.scope,
                            int(cf.is_accessor), cf.accessor_field,
                            json.dumps(cf.private_data),
                        ),
                    )
                conn.execute(
                    "INSERT INTO meta(vi_path, content_sha) VALUES (?,?)",
                    (f.path, f.content_sha),
                )
    finally:
        conn.close()


def delete(project_root: Path, paths: Iterable[str]) -> None:
    """Delete every row belonging to each given VI path from the index DB.

    Used by an incremental refresh to drop VIs whose ``.vi`` file is gone.
    """
    conn = _connect(project_root)
    try:
        with conn:
            for path in paths:
                _delete_vi(conn, path)
    finally:
        conn.close()


def save_lvproj_members(
    project_root: Path, members: Iterable[LVProjMemberFact],
) -> None:
    """Replace the project's ``.lvproj`` membership rows wholesale.

    Membership is a **project-level** fact derived purely from the repo's
    ``.lvproj`` XML (no VI content, no ``content_sha``) — cheap to recompute in
    full — so unlike the per-VI ``save()`` this just clears the table and
    reinserts, keeping it consistent with the current set of ``.lvproj`` files
    (added/removed projects, moved members) on every build/refresh.
    """
    conn = _connect(project_root)
    try:
        with conn:
            conn.execute("DELETE FROM lvproj_members")
            conn.executemany(
                "INSERT INTO lvproj_members(lvproj_path, lvproj_name, "
                "member_name, member_url, resolved_path, member_type, "
                "is_in_repo, target, is_dependency) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        m.lvproj_path, m.lvproj_name, m.member_name, m.member_url,
                        m.resolved_path, m.member_type, int(m.is_in_repo),
                        m.target, int(m.is_dependency),
                    )
                    for m in members
                ],
            )
    finally:
        conn.close()


def load_lvproj_members(project_root: Path) -> list[LVProjMemberFact]:
    """Load every ``.lvproj`` membership row for ``project_root`` (``[]`` if
    unindexed)."""
    conn = _connect(project_root)
    try:
        rows = conn.execute(
            "SELECT lvproj_path, lvproj_name, member_name, member_url, "
            "resolved_path, member_type, is_in_repo, target, is_dependency "
            "FROM lvproj_members"
        ).fetchall()
        return [
            LVProjMemberFact(
                lvproj_path=r[0],
                lvproj_name=r[1],
                member_name=r[2],
                member_url=r[3],
                resolved_path=r[4],
                member_type=r[5],
                is_in_repo=bool(r[6]),
                target=r[7],
                is_dependency=bool(r[8]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def _delete_vi(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM vis WHERE path = ?", (path,))
    for table in _CHILD_TABLES:
        col = "caller_path" if table == "calls" else "vi_path"
        conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (path,))
    conn.execute("DELETE FROM class_facts WHERE vi_path = ?", (path,))
    conn.execute("DELETE FROM meta WHERE vi_path = ?", (path,))


def load(project_root: Path) -> list[VIFacts]:
    """Load every ``VIFacts`` row for ``project_root`` from its index DB.

    Returns ``[]`` if the project has never been indexed (no DB / empty
    ``vis`` table) rather than raising.
    """
    conn = _connect(project_root)
    try:
        vi_rows = conn.execute(
            "SELECT path, name, qualified_name, library, is_stub, "
            "content_sha, impact_score, callers_count FROM vis"
        ).fetchall()

        terminals_by_vi: dict[str, list[TerminalFact]] = {}
        for row in conn.execute(
            "SELECT vi_path, name, direction, is_indicator, is_public, "
            "control_type, py_type, is_error_cluster, field_names, "
            "fp_dco_uid, lv_type, enum_values FROM terminals "
            "ORDER BY vi_path, ord"
        ):
            (
                vi_path, name, direction, is_indicator, is_public,
                control_type, py_type, is_error_cluster, field_names_json,
                fp_dco_uid, lv_type, enum_values_json,
            ) = row
            terminals_by_vi.setdefault(vi_path, []).append(
                TerminalFact(
                    name=name,
                    direction=direction,
                    is_indicator=bool(is_indicator),
                    is_public=bool(is_public),
                    control_type=control_type,
                    py_type=py_type,
                    is_error_cluster=bool(is_error_cluster),
                    field_names=json.loads(field_names_json),
                    fp_dco_uid=fp_dco_uid,
                    lv_type=lv_type,
                    enum_values=json.loads(enum_values_json),
                )
            )

        constants_by_vi: dict[str, list[ConstantFact]] = {}
        for vi_path, value, label, py_type, lv_type, wired_to in conn.execute(
            "SELECT vi_path, value, label, py_type, lv_type, wired_to "
            "FROM constants ORDER BY vi_path, ord"
        ):
            constants_by_vi.setdefault(vi_path, []).append(
                ConstantFact(
                    value=value, label=label, py_type=py_type,
                    lv_type=lv_type, wired_to=wired_to,
                )
            )

        calls_by_vi: dict[str, list[str]] = {}
        for caller_path, callee_key in conn.execute(
            "SELECT caller_path, callee_key FROM calls ORDER BY caller_path"
        ):
            calls_by_vi.setdefault(caller_path, []).append(callee_key)

        type_uses_by_vi: dict[str, list[str]] = {}
        for vi_path, type_key in conn.execute(
            "SELECT vi_path, type_key FROM type_uses ORDER BY vi_path"
        ):
            type_uses_by_vi.setdefault(vi_path, []).append(type_key)

        class_fact_by_vi: dict[str, ClassFact] = {}
        for row in conn.execute(
            "SELECT vi_path, owning_class, parent, scope, is_accessor, "
            "accessor_field, private_data FROM class_facts"
        ):
            (
                vi_path, owning_class, parent, scope, is_accessor,
                accessor_field, private_data,
            ) = row
            class_fact_by_vi[vi_path] = ClassFact(
                owning_class=owning_class,
                parent=parent,
                scope=scope,
                is_accessor=bool(is_accessor),
                accessor_field=accessor_field,
                private_data=json.loads(private_data),
            )

        results: list[VIFacts] = []
        for row in vi_rows:
            (
                path, name, qualified_name, library, is_stub, content_sha,
                impact_score, callers_count,
            ) = row
            results.append(
                VIFacts(
                    path=path,
                    name=name,
                    qualified_name=qualified_name,
                    library=library,
                    is_stub=bool(is_stub),
                    content_sha=content_sha,
                    terminals=terminals_by_vi.get(path, []),
                    constants=constants_by_vi.get(path, []),
                    calls=calls_by_vi.get(path, []),
                    type_uses=type_uses_by_vi.get(path, []),
                    class_fact=class_fact_by_vi.get(path),
                    impact_score=impact_score,
                    callers_count=callers_count,
                )
            )
        return results
    finally:
        conn.close()
