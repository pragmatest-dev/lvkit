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

WAL mode. Tables: ``vis``, ``terminals``, ``constants``, ``nodes`` (the
block-diagram node spine; its ``kind='vi'`` rows carry the call graph via
``callee_path``), ``type_uses``, ``class_facts``, and a
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

import json
import os
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from .. import cache_paths
from ..models import LVTypeKind
from .model import (
    ClassFact,
    ConstantFact,
    LVProjMemberFact,
    NodeFact,
    NodeKind,
    TerminalFact,
    VIFacts,
    WiredTo,
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
    callers_count INTEGER NOT NULL DEFAULT 0,
    lv_version TEXT,
    vi_type TEXT,
    lock_state TEXT NOT NULL DEFAULT 'unlocked',
    exec_priority TEXT NOT NULL DEFAULT 'normal',
    reentrancy TEXT NOT NULL DEFAULT 'non_reentrant',
    exec_system TEXT NOT NULL DEFAULT 'same_as_caller',
    exec_run_when_opened INTEGER NOT NULL DEFAULT 0,
    exec_show_fp_when_loaded INTEGER NOT NULL DEFAULT 0,
    exec_show_fp_when_called INTEGER NOT NULL DEFAULT 0,
    exec_close_fp_after_call INTEGER NOT NULL DEFAULT 0,
    exec_auto_preallocate_arrays INTEGER NOT NULL DEFAULT 0,
    exec_inline INTEGER NOT NULL DEFAULT 0,
    exec_inlinable INTEGER NOT NULL DEFAULT 0,
    exec_auto_error_handling INTEGER NOT NULL DEFAULT 0,
    exec_allow_debugging INTEGER NOT NULL DEFAULT 0,
    exec_always_calls_parent INTEGER NOT NULL DEFAULT 0,
    exec_print_after_exec INTEGER NOT NULL DEFAULT 0,
    window_show_title_bar INTEGER NOT NULL DEFAULT 0,
    window_show_menu_bar INTEGER NOT NULL DEFAULT 0,
    window_show_toolbar INTEGER NOT NULL DEFAULT 0,
    window_show_scrollbar INTEGER,
    window_auto_center INTEGER NOT NULL DEFAULT 0,
    window_size_to_screen INTEGER NOT NULL DEFAULT 0,
    window_no_runtime_popup_menu INTEGER NOT NULL DEFAULT 0,
    window_scale_with_window INTEGER NOT NULL DEFAULT 0,
    window_mark_return_button INTEGER NOT NULL DEFAULT 0,
    window_auto_handle_menus INTEGER NOT NULL DEFAULT 0,
    window_can_close INTEGER NOT NULL DEFAULT 0,
    window_can_resize INTEGER NOT NULL DEFAULT 0,
    window_can_minimize INTEGER NOT NULL DEFAULT 0,
    window_transparent INTEGER NOT NULL DEFAULT 0,
    toolbar_hide_run_button INTEGER NOT NULL DEFAULT 0,
    toolbar_hide_abort_button INTEGER NOT NULL DEFAULT 0,
    toolbar_hide_free_run_button INTEGER NOT NULL DEFAULT 0,
    instance_is_system_vi INTEGER NOT NULL DEFAULT 0,
    instance_show_poly_selector INTEGER NOT NULL DEFAULT 0,
    instance_hide_instance_caption INTEGER NOT NULL DEFAULT 0,
    instance_draw_instance_icon INTEGER NOT NULL DEFAULT 0,
    instance_remote_panel INTEGER NOT NULL DEFAULT 0,
    kind_typedef_status TEXT NOT NULL DEFAULT 'not_a_typedef',
    kind_dynamic_dispatch INTEGER NOT NULL DEFAULT 0,
    kind_source_only INTEGER NOT NULL DEFAULT 0,
    kind_has_no_block_diagram INTEGER NOT NULL DEFAULT 0,
    kind_is_instance_vi INTEGER NOT NULL DEFAULT 0,
    health_bad_node INTEGER NOT NULL DEFAULT 0,
    health_bad_subvi INTEGER NOT NULL DEFAULT 0,
    health_bad_subvi_link INTEGER NOT NULL DEFAULT 0,
    health_bad_compile INTEGER NOT NULL DEFAULT 0,
    health_broken_poly INTEGER NOT NULL DEFAULT 0,
    health_is_broken INTEGER NOT NULL DEFAULT 0
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
    field_names TEXT NOT NULL DEFAULT '[]',
    fp_dco_uid TEXT,
    type_descriptor TEXT NOT NULL DEFAULT '',
    type_kind TEXT,
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
    type_descriptor TEXT NOT NULL DEFAULT '',
    type_kind TEXT,
    wired_to TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_constants_vi ON constants(vi_path);
CREATE INDEX IF NOT EXISTS idx_constants_wired ON constants(wired_to);

CREATE TABLE IF NOT EXISTS nodes (
    vi_path TEXT NOT NULL,
    ord INTEGER NOT NULL,
    uid TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT,
    prim_id INTEGER,
    qualified_name TEXT,
    callee_path TEXT,
    object_name TEXT,
    method_name TEXT,
    parent_uid TEXT,
    frame TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_vi ON nodes(vi_path);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_prim_id ON nodes(prim_id);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_callee_path ON nodes(callee_path);
CREATE INDEX IF NOT EXISTS idx_nodes_parent_uid ON nodes(parent_uid);

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
    private_data TEXT NOT NULL DEFAULT '[]',
    is_static INTEGER NOT NULL DEFAULT 0,
    must_override INTEGER NOT NULL DEFAULT 0,
    must_call_parent INTEGER NOT NULL DEFAULT 0,
    class_version TEXT,
    ancestors TEXT NOT NULL DEFAULT '[]'
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

_CHILD_TABLES = ("terminals", "constants", "nodes", "type_uses")

# Every table that holds DERIVED facts — a pure cache of what lvkit's parser
# produces from the VIs. Dropped wholesale when the facts fingerprint changes
# (``_ensure_facts_version``). ``index_meta`` (the fingerprint itself) is NOT
# here — it survives the wipe so the new fingerprint can be stamped onto it.
_ALL_TABLES = (
    "vis",
    "terminals",
    "constants",
    "nodes",
    "type_uses",
    "class_facts",
    "lvproj_members",
    "meta",
)

# VIProperties + VIHealth flattened columns on ``vis``: (column_name,
# is_bool). ``column_name`` is always identical to the matching ``VIFacts``
# attribute name (see index/model.py) so save()/load() round-trip all 50
# columns from this single ordered list instead of hand-aligning that many
# positional slots across the DDL/INSERT/SELECT. The first 5 of the last 11
# (``kind_*``) are VIProperties.kind's (a sub-struct, like exec_*/window_*/
# …); the final 6 (``health_*``) are VIHealth's -- a facet SIBLING to
# VIProperties, flattened into the same wide ``vis`` row for
# one-query-per-VI convenience.
_FLAT_PROPERTY_COLUMNS: tuple[tuple[str, bool], ...] = (
    ("lv_version", False),
    ("vi_type", False),
    ("lock_state", False),
    ("exec_priority", False),
    ("reentrancy", False),
    ("exec_system", False),
    ("exec_run_when_opened", True),
    ("exec_show_fp_when_loaded", True),
    ("exec_show_fp_when_called", True),
    ("exec_close_fp_after_call", True),
    ("exec_auto_preallocate_arrays", True),
    ("exec_inline", True),
    ("exec_inlinable", True),
    ("exec_auto_error_handling", True),
    ("exec_allow_debugging", True),
    ("exec_always_calls_parent", True),
    ("exec_print_after_exec", True),
    ("window_show_title_bar", True),
    ("window_show_menu_bar", True),
    ("window_show_toolbar", True),
    ("window_show_scrollbar", False),
    ("window_auto_center", True),
    ("window_size_to_screen", True),
    ("window_no_runtime_popup_menu", True),
    ("window_scale_with_window", True),
    ("window_mark_return_button", True),
    ("window_auto_handle_menus", True),
    ("window_can_close", True),
    ("window_can_resize", True),
    ("window_can_minimize", True),
    ("window_transparent", True),
    ("toolbar_hide_run_button", True),
    ("toolbar_hide_abort_button", True),
    ("toolbar_hide_free_run_button", True),
    ("instance_is_system_vi", True),
    ("instance_show_poly_selector", True),
    ("instance_hide_instance_caption", True),
    ("instance_draw_instance_icon", True),
    ("instance_remote_panel", True),
    ("kind_typedef_status", False),
    ("kind_dynamic_dispatch", True),
    ("kind_source_only", True),
    ("kind_has_no_block_diagram", True),
    ("kind_is_instance_vi", True),
    ("health_bad_node", True),
    ("health_bad_subvi", True),
    ("health_bad_subvi_link", True),
    ("health_bad_compile", True),
    ("health_broken_poly", True),
    ("health_is_broken", True),
)


# The source fingerprint that invalidates the index now lives in ``cache_paths``
# (the light, dependency-free module) so EVERY cache — this SQLite index and the
# render/diff output cache — shares the exact SAME invalidation. Re-exported here
# under the historical names the index code + guard test reference. See
# ``cache_paths.source_fingerprint`` / ``_FINGERPRINT_SKIP_DIRS``.
_FINGERPRINT_SKIP_DIRS = cache_paths._FINGERPRINT_SKIP_DIRS
_facts_fingerprint = cache_paths.source_fingerprint


def db_path(project_root: Path) -> Path:
    """The SQLite file for ``project_root``'s index (parent dir created).

    Path-partitioned by the source fingerprint (``kind_fingerprint("index")`` —
    the same ``<fp>`` level every other cache kind uses) so two incompatible lvkit
    builds keep SEPARATE index dbs and never drop/rebuild each other's tables. The
    stored-fingerprint self-invalidation in ``_ensure_facts_version`` is now a
    backstop (a matching path already implies a matching fingerprint), kept
    because its guard test and any pre-<fp> db still rely on it.
    """
    slug = cache_paths._slug(project_root.resolve())
    d = (
        cache_paths.global_cache_root()
        / "projects"
        / slug
        / "index"
        / cache_paths.kind_fingerprint("index")
    )
    d.mkdir(parents=True, exist_ok=True)
    return d / "index.db"


def _connect(project_root: Path) -> sqlite3.Connection:
    path = db_path(project_root)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_facts_version(conn)
    conn.executescript(_SCHEMA)
    # Bump the slice's mtime so the access-TTL sweep keeps an index in active use
    # warm and only retires the ones for builds the user has upgraded away from.
    try:
        now = time.time()
        os.utime(path, (now, now))
    except OSError:
        pass
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
    row = conn.execute("SELECT fingerprint FROM index_meta WHERE id = 0").fetchone()
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
    conn: sqlite3.Connection,
    path: str,
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

    lib_row = conn.execute("SELECT library FROM vis WHERE path = ?", (path,)).fetchone()
    prior_library = lib_row[0] if lib_row is not None else None

    cf_row = conn.execute(
        "SELECT owning_class, parent, scope, is_accessor, accessor_field, "
        "private_data, is_static, must_override, must_call_parent, "
        "class_version, ancestors "
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
            private_data=json.loads(cf_row[5]),
            is_static=bool(cf_row[6]),
            must_override=bool(cf_row[7]),
            must_call_parent=bool(cf_row[8]),
            class_version=cf_row[9],
            ancestors=json.loads(cf_row[10]),
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
    terminals/constants/nodes/type_uses child tables are intrinsic to a
    single-VI load and are never coalesced — always overwritten. (``callee_path``
    on the node rows is the one exception filled at merge, like impact_score.)
    """
    conn = _connect(project_root)
    try:
        with conn:
            for f in vis:
                prior_sha, prior_library, prior_class_fact = _prior_container_facts(
                    conn, f.path
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
                        # Coalesce the accessor PAIR atomically: is_accessor and
                        # accessor_field are one fact from the owns edge, so a
                        # partial load with no accessor info must not leave
                        # is_accessor=False beside a preserved accessor_field (a
                        # self-contradictory row).
                        if cfc.is_accessor or cfc.accessor_field is not None:
                            is_accessor = cfc.is_accessor
                            accessor_field = cfc.accessor_field
                        else:
                            is_accessor = prior_class_fact.is_accessor
                            accessor_field = prior_class_fact.accessor_field
                        # scope/is_static/must_override/must_call_parent all
                        # come from the SAME "owns" edge lookup
                        # (get_method_access) -- gate the whole group on scope
                        # (like the accessor pair above) so a partial load
                        # with no access info at all doesn't clobber a
                        # previously-resolved method's is_static/must_override
                        # with defaulted False.
                        if cfc.scope is not None:
                            scope = cfc.scope
                            is_static = cfc.is_static
                            must_override = cfc.must_override
                            must_call_parent = cfc.must_call_parent
                        else:
                            scope = prior_class_fact.scope
                            is_static = prior_class_fact.is_static
                            must_override = prior_class_fact.must_override
                            must_call_parent = prior_class_fact.must_call_parent
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
                            scope=scope,
                            is_accessor=is_accessor,
                            accessor_field=accessor_field,
                            private_data=(
                                cfc.private_data
                                if cfc.private_data
                                else prior_class_fact.private_data
                            ),
                            is_static=is_static,
                            must_override=must_override,
                            must_call_parent=must_call_parent,
                            class_version=(
                                cfc.class_version
                                if cfc.class_version is not None
                                else prior_class_fact.class_version
                            ),
                            ancestors=(
                                cfc.ancestors
                                if cfc.ancestors
                                else prior_class_fact.ancestors
                            ),
                        )

                _delete_vi(conn, f.path)
                prop_cols = [c for c, _ in _FLAT_PROPERTY_COLUMNS]
                prop_values = tuple(
                    int(getattr(f, c)) if is_bool else getattr(f, c)
                    for c, is_bool in _FLAT_PROPERTY_COLUMNS
                )
                base_cols = (
                    "path",
                    "name",
                    "qualified_name",
                    "library",
                    "is_stub",
                    "content_sha",
                    "impact_score",
                    "callers_count",
                )
                conn.execute(
                    f"INSERT INTO vis({', '.join(base_cols + tuple(prop_cols))}) "
                    f"VALUES ({', '.join('?' * (len(base_cols) + len(prop_cols)))})",
                    (
                        f.path,
                        f.name,
                        f.qualified_name,
                        library,
                        int(f.is_stub),
                        f.content_sha,
                        f.impact_score,
                        f.callers_count,
                        *prop_values,
                    ),
                )
                conn.executemany(
                    "INSERT INTO terminals(vi_path, ord, name, direction, "
                    "is_indicator, is_public, control_type, "
                    "field_names, fp_dco_uid, type_descriptor, type_kind, "
                    "enum_values) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            f.path,
                            i,
                            t.name,
                            t.direction,
                            int(t.is_indicator),
                            int(t.is_public),
                            t.control_type,
                            json.dumps(t.field_names),
                            t.fp_dco_uid,
                            t.type_descriptor,
                            t.type_kind.value if t.type_kind else None,
                            json.dumps(t.enum_values),
                        )
                        for i, t in enumerate(f.terminals)
                    ],
                )
                conn.executemany(
                    "INSERT INTO constants(vi_path, ord, value, label, "
                    "type_descriptor, type_kind, wired_to) VALUES (?,?,?,?,?,?,?)",
                    [
                        (
                            f.path,
                            i,
                            c.value,
                            c.label,
                            c.type_descriptor,
                            c.type_kind.value if c.type_kind else None,
                            c.wired_to.value,
                        )
                        for i, c in enumerate(f.constants)
                    ],
                )
                conn.executemany(
                    "INSERT INTO nodes(vi_path, ord, uid, kind, name, prim_id, "
                    "qualified_name, callee_path, object_name, method_name, "
                    "parent_uid, frame) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            f.path,
                            i,
                            n.uid,
                            n.kind.value,
                            n.name,
                            n.prim_id,
                            n.qualified_name,
                            n.callee_path,
                            n.object_name,
                            n.method_name,
                            n.parent_uid,
                            n.frame,
                        )
                        for i, n in enumerate(f.nodes)
                    ],
                )
                conn.executemany(
                    "INSERT INTO type_uses(vi_path, type_key) VALUES (?,?)",
                    [(f.path, type_key) for type_key in f.type_uses],
                )
                if class_fact is not None:
                    cf = class_fact
                    conn.execute(
                        "INSERT INTO class_facts(vi_path, owning_class, parent, "
                        "scope, is_accessor, accessor_field, private_data, "
                        "is_static, must_override, must_call_parent, "
                        "class_version, ancestors) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            f.path,
                            cf.owning_class,
                            cf.parent,
                            cf.scope,
                            int(cf.is_accessor),
                            cf.accessor_field,
                            json.dumps(cf.private_data),
                            int(cf.is_static),
                            int(cf.must_override),
                            int(cf.must_call_parent),
                            cf.class_version,
                            json.dumps(cf.ancestors),
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
    project_root: Path,
    members: Iterable[LVProjMemberFact],
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
                        m.lvproj_path,
                        m.lvproj_name,
                        m.member_name,
                        m.member_url,
                        m.resolved_path,
                        m.member_type,
                        int(m.is_in_repo),
                        m.target,
                        int(m.is_dependency),
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
        conn.execute(f"DELETE FROM {table} WHERE vi_path = ?", (path,))
    conn.execute("DELETE FROM class_facts WHERE vi_path = ?", (path,))
    conn.execute("DELETE FROM meta WHERE vi_path = ?", (path,))


def load(project_root: Path) -> list[VIFacts]:
    """Load every ``VIFacts`` row for ``project_root`` from its index DB.

    Returns ``[]`` if the project has never been indexed (no DB / empty
    ``vis`` table) rather than raising.
    """
    conn = _connect(project_root)
    try:
        prop_cols = [c for c, _ in _FLAT_PROPERTY_COLUMNS]
        vi_rows = conn.execute(
            "SELECT path, name, qualified_name, library, is_stub, "
            "content_sha, impact_score, callers_count, "
            f"{', '.join(prop_cols)} FROM vis"
        ).fetchall()

        terminals_by_vi: dict[str, list[TerminalFact]] = {}
        for row in conn.execute(
            "SELECT vi_path, name, direction, is_indicator, is_public, "
            "control_type, field_names, "
            "fp_dco_uid, type_descriptor, type_kind, enum_values FROM terminals "
            "ORDER BY vi_path, ord"
        ):
            (
                vi_path,
                name,
                direction,
                is_indicator,
                is_public,
                control_type,
                field_names_json,
                fp_dco_uid,
                type_descriptor,
                type_kind,
                enum_values_json,
            ) = row
            terminals_by_vi.setdefault(vi_path, []).append(
                TerminalFact(
                    name=name,
                    direction=direction,
                    is_indicator=bool(is_indicator),
                    is_public=bool(is_public),
                    control_type=control_type,
                    field_names=json.loads(field_names_json),
                    fp_dco_uid=fp_dco_uid,
                    type_descriptor=type_descriptor,
                    type_kind=LVTypeKind(type_kind) if type_kind else None,
                    enum_values=json.loads(enum_values_json),
                )
            )

        constants_by_vi: dict[str, list[ConstantFact]] = {}
        for vi_path, value, label, type_descriptor, type_kind, wired_to in conn.execute(
            "SELECT vi_path, value, label, type_descriptor, type_kind, wired_to "
            "FROM constants ORDER BY vi_path, ord"
        ):
            constants_by_vi.setdefault(vi_path, []).append(
                ConstantFact(
                    value=value,
                    label=label,
                    type_descriptor=type_descriptor,
                    type_kind=LVTypeKind(type_kind) if type_kind else None,
                    wired_to=WiredTo(wired_to),
                )
            )

        nodes_by_vi: dict[str, list[NodeFact]] = {}
        for row in conn.execute(
            "SELECT vi_path, uid, kind, name, prim_id, qualified_name, "
            "callee_path, object_name, method_name, parent_uid, frame "
            "FROM nodes ORDER BY vi_path, ord"
        ):
            (
                vi_path,
                uid,
                kind,
                name,
                prim_id,
                qualified_name,
                callee_path,
                object_name,
                method_name,
                parent_uid,
                frame,
            ) = row
            nodes_by_vi.setdefault(vi_path, []).append(
                NodeFact(
                    uid=uid,
                    kind=NodeKind(kind),
                    name=name,
                    prim_id=prim_id,
                    qualified_name=qualified_name,
                    callee_path=callee_path,
                    object_name=object_name,
                    method_name=method_name,
                    parent_uid=parent_uid,
                    frame=frame,
                )
            )

        type_uses_by_vi: dict[str, list[str]] = {}
        for vi_path, type_key in conn.execute(
            "SELECT vi_path, type_key FROM type_uses ORDER BY vi_path"
        ):
            type_uses_by_vi.setdefault(vi_path, []).append(type_key)

        class_fact_by_vi: dict[str, ClassFact] = {}
        for row in conn.execute(
            "SELECT vi_path, owning_class, parent, scope, is_accessor, "
            "accessor_field, private_data, is_static, must_override, "
            "must_call_parent, class_version, ancestors FROM class_facts"
        ):
            (
                vi_path,
                owning_class,
                parent,
                scope,
                is_accessor,
                accessor_field,
                private_data,
                is_static,
                must_override,
                must_call_parent,
                class_version,
                ancestors,
            ) = row
            class_fact_by_vi[vi_path] = ClassFact(
                owning_class=owning_class,
                parent=parent,
                scope=scope,
                is_accessor=bool(is_accessor),
                accessor_field=accessor_field,
                private_data=json.loads(private_data),
                is_static=bool(is_static),
                must_override=bool(must_override),
                must_call_parent=bool(must_call_parent),
                class_version=class_version,
                ancestors=json.loads(ancestors),
            )

        results: list[VIFacts] = []
        for row in vi_rows:
            base = row[:8]
            (
                path,
                name,
                qualified_name,
                library,
                is_stub,
                content_sha,
                impact_score,
                callers_count,
            ) = base
            prop_kwargs = cast(
                "dict[str, Any]",
                {
                    col: (bool(value) if is_bool else value)
                    for (col, is_bool), value in zip(
                        _FLAT_PROPERTY_COLUMNS, row[8:], strict=True
                    )
                },
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
                    nodes=nodes_by_vi.get(path, []),
                    type_uses=type_uses_by_vi.get(path, []),
                    class_fact=class_fact_by_vi.get(path),
                    impact_score=impact_score,
                    callers_count=callers_count,
                    **prop_kwargs,
                )
            )
        return results
    finally:
        conn.close()
