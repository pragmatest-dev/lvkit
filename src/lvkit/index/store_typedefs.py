"""The ``.ctl`` controls' tables: one ``typedefs`` row per control with its
flattened fields, the files it uses / is owned by, and the closure of types it
holds. Functions take an open connection and run inside the caller's
transaction; type bodies live in the shared catalog (``store_types``)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from .model import TypedefFacts, TypedefRel
from .store_types import record_types, require_types

TYPEDEFS_SCHEMA = """
CREATE TABLE IF NOT EXISTS typedefs (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    library TEXT,
    is_stub INTEGER NOT NULL DEFAULT 0,
    stub_reason TEXT,
    content_sha TEXT NOT NULL DEFAULT '',
    type_id TEXT,
    kind TEXT,
    default_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_typedefs_name ON typedefs(name);
CREATE INDEX IF NOT EXISTS idx_typedefs_type ON typedefs(type_id);

CREATE TABLE IF NOT EXISTS typedef_fields (
    typedef_path TEXT NOT NULL,
    seq INTEGER NOT NULL,
    parent_seq INTEGER,
    depth INTEGER NOT NULL,
    name TEXT NOT NULL,
    type_id TEXT,
    default_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_typedef_fields_typedef ON typedef_fields(typedef_path);
CREATE INDEX IF NOT EXISTS idx_typedef_fields_name ON typedef_fields(name);

CREATE TABLE IF NOT EXISTS typedef_refs (
    typedef_path TEXT NOT NULL,
    ref_path TEXT,
    ref_name TEXT NOT NULL,
    ref_kind TEXT NOT NULL,
    rel TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_typedef_refs_typedef ON typedef_refs(typedef_path);
CREATE INDEX IF NOT EXISTS idx_typedef_refs_ref ON typedef_refs(ref_path);

-- The closure of types each .ctl control holds (its own type and everything
-- nested in it).
CREATE TABLE IF NOT EXISTS typedef_types (
    typedef_path TEXT NOT NULL,
    type_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_typedef_types_typedef ON typedef_types(typedef_path);
CREATE INDEX IF NOT EXISTS idx_typedef_types_type ON typedef_types(type_id);
"""

_CHILD_TABLES = ("typedef_fields", "typedef_refs", "typedef_types")


def delete_typedef(conn: sqlite3.Connection, path: str) -> None:
    """Every row of one control."""
    conn.execute("DELETE FROM typedefs WHERE path = ?", (path,))
    for table in _CHILD_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE typedef_path = ?", (path,))


def write_typedefs(conn: sqlite3.Connection, typedefs: Iterable[TypedefFacts]) -> None:
    """Replace each control's rows (delete, then reinsert), sharing type bodies
    with the VIs."""
    for t in typedefs:
        delete_typedef(conn, t.path)
        conn.execute(
            "INSERT INTO typedefs(path, name, library, is_stub, stub_reason, "
            "content_sha, type_id, kind, default_text) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                t.path,
                t.name,
                t.library,
                int(t.is_stub),
                t.stub_reason,
                t.content_sha,
                t.type_id,
                t.kind.value if t.kind is not None else None,
                t.default_text,
            ),
        )
        conn.executemany(
            "INSERT INTO typedef_fields(typedef_path, seq, parent_seq, depth, "
            "name, type_id, default_text) VALUES (?,?,?,?,?,?,?)",
            [
                (
                    t.path,
                    f.seq,
                    f.parent_seq,
                    f.depth,
                    f.name,
                    f.type_id,
                    f.default_text,
                )
                for f in t.fields
            ],
        )
        conn.executemany(
            "INSERT INTO typedef_refs(typedef_path, ref_path, ref_name, "
            "ref_kind, rel) VALUES (?,?,?,?,?)",
            [
                (t.path, r.ref_path, r.ref_name, r.ref_kind, r.rel.value)
                for r in t.refs
            ],
        )
        record_types(conn, t.types)
        type_ids = sorted(ty.type_id for ty in t.types)
        require_types(conn, t.path, type_ids)
        conn.executemany(
            "INSERT INTO typedef_types(typedef_path, type_id) VALUES (?,?)",
            [(t.path, type_id) for type_id in type_ids],
        )


def read_typedef_shas(conn: sqlite3.Connection) -> dict[str, str]:
    """``{control path: content_sha}`` of every stored control -- all an
    incremental sync needs to know."""
    return dict(conn.execute("SELECT path, content_sha FROM typedefs"))


def read_typedef_dependents(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """``{control path: paths of the controls that USE it}`` -- the reverse of
    ``typedef_refs``' ``uses`` edges. A control whose own file is unchanged can
    still need rebuilding when one it uses (directly or, by walking this map,
    transitively) does: its resolved fields/type_id are read through that
    dependency, not from its own bytes."""
    dependents: dict[str, set[str]] = {}
    for ref_path, typedef_path in conn.execute(
        "SELECT ref_path, typedef_path FROM typedef_refs "
        "WHERE rel = ? AND ref_path IS NOT NULL",
        (TypedefRel.USES.value,),
    ):
        dependents.setdefault(ref_path, set()).add(typedef_path)
    return dependents
