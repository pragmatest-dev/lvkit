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
``meta(vi_path, content_sha, schema_version)`` freshness row per VI. Upsert by
path: ``save()`` deletes then reinserts every row belonging to each given
``VIFacts.path`` (safe for both a full rebuild and a future partial refresh).
"""

from __future__ import annotations

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

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vis (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    qualified_name TEXT,
    library TEXT,
    is_stub INTEGER NOT NULL DEFAULT 0,
    content_sha TEXT NOT NULL DEFAULT '',
    impact_score INTEGER NOT NULL DEFAULT 0
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
    fp_dco_uid TEXT
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
    accessor_field TEXT
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
    content_sha TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);
"""

_CHILD_TABLES = ("terminals", "constants", "calls", "type_uses")


def db_path(project_root: Path) -> Path:
    """The SQLite file for ``project_root``'s index (parent dir created)."""
    slug = cache_paths._slug(project_root.resolve())
    d = cache_paths.global_cache_root() / "index" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d / "index.db"


def _connect(project_root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(project_root))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


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
        "SELECT owning_class, parent, scope, is_accessor, accessor_field "
        "FROM class_facts WHERE vi_path = ?",
        (path,),
    ).fetchone()
    prior_class_fact = (
        ClassFact(
            owning_class=cf_row[0],
            parent=cf_row[1],
            scope=cf_row[2],
            is_accessor=bool(cf_row[3]),
            accessor_field=cf_row[4],
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
                        )

                _delete_vi(conn, f.path)
                conn.execute(
                    "INSERT INTO vis(path, name, qualified_name, library, "
                    "is_stub, content_sha, impact_score) VALUES (?,?,?,?,?,?,?)",
                    (
                        f.path, f.name, f.qualified_name, library,
                        int(f.is_stub), f.content_sha, f.impact_score,
                    ),
                )
                conn.executemany(
                    "INSERT INTO terminals(vi_path, ord, name, direction, "
                    "is_indicator, is_public, control_type, py_type, "
                    "is_error_cluster, field_names, fp_dco_uid) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            f.path, i, t.name, t.direction, int(t.is_indicator),
                            int(t.is_public), t.control_type, t.py_type,
                            int(t.is_error_cluster), json.dumps(t.field_names),
                            t.fp_dco_uid,
                        )
                        for i, t in enumerate(f.terminals)
                    ],
                )
                conn.executemany(
                    "INSERT INTO constants(vi_path, ord, value, label, "
                    "py_type, wired_to) VALUES (?,?,?,?,?,?)",
                    [
                        (f.path, i, c.value, c.label, c.py_type, c.wired_to)
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
                        "scope, is_accessor, accessor_field) VALUES (?,?,?,?,?,?)",
                        (
                            f.path, cf.owning_class, cf.parent, cf.scope,
                            int(cf.is_accessor), cf.accessor_field,
                        ),
                    )
                conn.execute(
                    "INSERT INTO meta(vi_path, content_sha, schema_version) "
                    "VALUES (?,?,?)",
                    (f.path, f.content_sha, SCHEMA_VERSION),
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
            "content_sha, impact_score FROM vis"
        ).fetchall()

        terminals_by_vi: dict[str, list[TerminalFact]] = {}
        for row in conn.execute(
            "SELECT vi_path, name, direction, is_indicator, is_public, "
            "control_type, py_type, is_error_cluster, field_names, "
            "fp_dco_uid FROM terminals ORDER BY vi_path, ord"
        ):
            (
                vi_path, name, direction, is_indicator, is_public,
                control_type, py_type, is_error_cluster, field_names_json,
                fp_dco_uid,
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
                )
            )

        constants_by_vi: dict[str, list[ConstantFact]] = {}
        for vi_path, value, label, py_type, wired_to in conn.execute(
            "SELECT vi_path, value, label, py_type, wired_to FROM constants "
            "ORDER BY vi_path, ord"
        ):
            constants_by_vi.setdefault(vi_path, []).append(
                ConstantFact(
                    value=value, label=label, py_type=py_type, wired_to=wired_to,
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
            "accessor_field FROM class_facts"
        ):
            vi_path, owning_class, parent, scope, is_accessor, accessor_field = row
            class_fact_by_vi[vi_path] = ClassFact(
                owning_class=owning_class,
                parent=parent,
                scope=scope,
                is_accessor=bool(is_accessor),
                accessor_field=accessor_field,
            )

        results: list[VIFacts] = []
        for row in vi_rows:
            path, name, qualified_name, library, is_stub, content_sha, impact_score = (
                row
            )
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
                )
            )
        return results
    finally:
        conn.close()
