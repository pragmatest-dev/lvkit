"""The shared type catalog's tables: each ``TypeFact`` body is stored once
(``types`` / ``type_fields`` / ``type_items``), referenced by every VI and control
that uses it, and collected when nothing does. Functions take an open connection
and run inside the caller's transaction."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from .model import TypeFact

TYPES_SCHEMA = """
CREATE TABLE IF NOT EXISTS types (
    type_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    descriptor TEXT NOT NULL DEFAULT '',
    name TEXT,
    dimensions INTEGER,
    element_type_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_types_name ON types(name);
CREATE INDEX IF NOT EXISTS idx_types_kind ON types(kind);

CREATE TABLE IF NOT EXISTS type_fields (
    type_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    field_type_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_type_fields_type ON type_fields(type_id);
CREATE INDEX IF NOT EXISTS idx_type_fields_child ON type_fields(field_type_id);
CREATE INDEX IF NOT EXISTS idx_type_fields_name ON type_fields(name);

CREATE TABLE IF NOT EXISTS type_items (
    type_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    value INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_type_items_type ON type_items(type_id);
CREATE INDEX IF NOT EXISTS idx_type_items_name ON type_items(name);
"""


def record_types(conn: sqlite3.Connection, types: Iterable[TypeFact]) -> None:
    """Store each ``TypeFact`` body once -- a type is shared by every owner and
    never changes, so an existing row is left alone."""
    for t in types:
        cur = conn.execute(
            "INSERT OR IGNORE INTO types(type_id, kind, descriptor, name, "
            "dimensions, element_type_id) VALUES (?,?,?,?,?,?)",
            (
                t.type_id,
                t.kind.value,
                t.descriptor,
                t.name,
                t.dimensions,
                t.element_type_id,
            ),
        )
        if cur.rowcount:
            conn.executemany(
                "INSERT INTO type_fields(type_id, seq, name, field_type_id) "
                "VALUES (?,?,?,?)",
                [(t.type_id, i, fl.name, fl.type_id) for i, fl in enumerate(t.fields)],
            )
            conn.executemany(
                "INSERT INTO type_items(type_id, seq, name, value) VALUES (?,?,?,?)",
                [(t.type_id, i, it.name, it.value) for i, it in enumerate(t.items)],
            )


def require_types(
    conn: sqlite3.Connection, owner: str, type_ids: Iterable[str]
) -> None:
    """Raise when an owner names a type the catalog does not hold -- a dangling
    reference would make every "who uses this type" answer silently wrong."""
    missing = sorted(
        t
        for t in set(type_ids)
        if conn.execute("SELECT 1 FROM types WHERE type_id = ?", (t,)).fetchone()
        is None
    )
    if missing:
        raise ValueError(f"{owner} references types not in the catalog: {missing}")


def drop_unused_types(conn: sqlite3.Connection) -> None:
    """Delete every type no owner uses any more (types are content-addressed and
    shared, so they are collected here, not per owner)."""
    conn.execute(
        "DELETE FROM types WHERE "
        "NOT EXISTS (SELECT 1 FROM vi_types v WHERE v.type_id = types.type_id) AND "
        "NOT EXISTS (SELECT 1 FROM typedef_types d WHERE d.type_id = types.type_id)"
    )
    for child in ("type_fields", "type_items"):
        conn.execute(
            f"DELETE FROM {child} WHERE NOT EXISTS "
            f"(SELECT 1 FROM types t WHERE t.type_id = {child}.type_id)"
        )
