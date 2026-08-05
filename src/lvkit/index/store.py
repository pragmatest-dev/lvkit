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
from .model import ClassFact, ConstantFact, TerminalFact, VIFacts

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


def save(project_root: Path, vis: Iterable[VIFacts]) -> None:
    """Upsert every given ``VIFacts`` row into the project's index DB."""
    conn = _connect(project_root)
    try:
        with conn:
            for f in vis:
                _delete_vi(conn, f.path)
                conn.execute(
                    "INSERT INTO vis(path, name, qualified_name, library, "
                    "is_stub, content_sha, impact_score) VALUES (?,?,?,?,?,?,?)",
                    (
                        f.path, f.name, f.qualified_name, f.library,
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
                if f.class_fact is not None:
                    cf = f.class_fact
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
