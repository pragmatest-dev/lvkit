"""Read-only SQL query surface over a project's facts index.

This is the relational half of the query architecture (design doc
``docs/_internal/design/lvkit-query-surface.md`` §3): the ONE ``query`` entry
point that replaces the read-half of the hand-shaped MCP tools. It answers the
driving question — *"count the names this project uses for error indicators"* —
as a single ``GROUP BY`` that returns the 13-row histogram, not the 379 terminal
rows the old tool dumped.

Callers get **read-only SQL over a small, curated VIEW layer** (``vi``,
``terminal``, ``constant``, ``call``, ``type_use``, ``class_fact``). The physical
tables (``store.py``) can churn underneath; the views are the public contract.

Security is enforced **structurally**, not by string-matching the SQL (design
§3a — Anthropic's own Postgres MCP shipped a read-only-bypass injection because
it grepped):

1. the DB is opened ``mode=ro`` — writes are impossible at the file layer;
2. an ``authorizer`` denies every action except SELECT/READ/FUNCTION/RECURSIVE
   — so ``PRAGMA``/``ATTACH``/``DELETE`` are refused even inside a crafted
   statement, and even if (1) were bypassed;
3. ``execute`` runs exactly one statement (a second, stacked statement raises);
4. a wall-clock progress handler interrupts a runaway query;
5. results are row-capped.

Transitive reachability (blast-radius/callers) deliberately stays OUT of this
surface — it lives in :mod:`lvkit.index.query` as typed graph ops (design §4).
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import store

# --- SQLite authorizer action codes (stable ABI values) ---------------------
# The ``sqlite3`` module only exposes these as named constants on Python 3.11+;
# lvkit targets 3.10, so we pin the integer values (they are part of SQLite's
# stable C ABI and never change).
_SQLITE_READ = 20
_SQLITE_SELECT = 21
_SQLITE_FUNCTION = 31
_SQLITE_RECURSIVE = 33

_ALLOWED_ACTIONS = frozenset(
    {_SQLITE_READ, _SQLITE_SELECT, _SQLITE_FUNCTION, _SQLITE_RECURSIVE}
)

DEFAULT_ROW_CAP = 1000
DEFAULT_TIMEOUT_S = 2.0


class QueryError(Exception):
    """A query was rejected (not a SELECT, unindexed project) or timed out.

    Raised LOUD and specific so the caller/agent can correct itself, per the
    "fail loud, never silent-wrong" discipline in the design doc (§3b).
    """


# --- The curated view layer -------------------------------------------------
# One source of truth: each view's SELECT body AND a per-column description
# (used verbatim by :func:`describe_schema` so an agent grounds itself in real
# column names instead of guessing). Keep the surface SMALL and well-named —
# that is the regime where LLM-authored SQL is reliable (design §3b).


@dataclass(frozen=True)
class _View:
    body: str
    columns: dict[str, str]

    @property
    def select_list(self) -> str:
        return ", ".join(self.columns)


VIEWS: dict[str, _View] = {
    "vi": _View(
        body="FROM vis",
        columns={
            "path": "absolute path to the .vi file (the VI's identity key)",
            "name": "bare VI filename, e.g. 'run.vi'",
            "qualified_name": "class/lib-qualified name (e.g. 'Foo.lvclass:run.vi')",
            "library": "owning .lvclass/.lvlib, or NULL",
            "is_stub": "1 if the VI could not be fully loaded (placeholder facts)",
            "impact_score": "count of transitive dependents (0 until a full refresh)",
            "callers_count": "number of in-repo VIs that directly call this VI; "
            "0 == dead code / uncalled. Use this for uncalled-VI detection — it "
            "is computed on VI path identity, so it is reliable even when "
            "qualified_name is NULL (a name-matching anti-join over callee_key "
            "silently misfires).",
            "lv_version": "LabVIEW version the VI was saved with, "
            "'Major.Minor.Bugfix' (e.g. '21.0.0'), or NULL if absent",
            "vi_type": "VI kind from the Instrument record (e.g. 'Control'), "
            "or NULL",
            "lock_state": "VI Properties -> Protection: 'unlocked', 'locked', "
            "or 'password_protected'",
            # -- exec_* : VI Properties -> Execution -------------------------
            "exec_reentrant": "1 if VI Properties -> Execution 'Reentrant "
            "execution' is enabled",
            "exec_reentrancy_pooled": "1 if reentrant execution uses a shared "
            "clone pool (vs. a preallocated clone per caller)",
            "exec_priority": "Execution priority code (LVSR Priority "
            "attribute), or NULL if unset",
            "exec_preferred_system": "Execution preferred execution system "
            "code (LVSR PrefExecSyst), or NULL if unset",
            "exec_is_subroutine": "1 if VI Properties -> Execution priority "
            "is 'subroutine'",
            "exec_run_when_opened": "1 if 'Run when opened' is enabled",
            "exec_show_fp_when_loaded": "1 if the front panel shows when the "
            "VI is loaded into memory",
            "exec_show_fp_when_called": "1 if the front panel shows when the "
            "VI is called as a subVI",
            "exec_close_fp_after_call": "1 if the front panel closes "
            "afterwards when called as a subVI",
            "exec_auto_preallocate_arrays": "1 if 'Auto handle array/string "
            "resizing at subVI boundary' is enabled",
            "exec_inline": "1 if the VI is set to inline into its callers",
            "exec_inlinable": "1 if the block diagram is eligible for inlining",
            "exec_auto_error_handling": "1 if automatic error handling is "
            "enabled for this VI",
            "exec_allow_debugging": "1 if 'Allow debugging' is enabled",
            "exec_always_calls_parent": "1 if a dynamic-dispatch override "
            "always calls its parent implementation first",
            "exec_print_after_exec": "1 if 'Print panel after execution' is "
            "enabled",
            # -- window_* : VI Properties -> Window Appearance ---------------
            "window_show_title_bar": "1 if the front panel window shows a "
            "title bar",
            "window_show_menu_bar": "1 if the front panel window shows a "
            "menu bar",
            "window_show_toolbar": "1 if the front panel window shows a "
            "toolbar",
            "window_show_scrollbar": "raw ShowScrollBar bitmask, verbatim, "
            "or NULL if unset",
            "window_auto_center": "1 if the front panel window auto-centers "
            "on screen",
            "window_size_to_screen": "1 if the front panel window auto-sizes "
            "to the screen",
            "window_no_runtime_popup_menu": "1 if the runtime shortcut menu "
            "is disabled",
            "window_scale_with_window": "1 if front-panel objects scale with "
            "the window",
            "window_mark_return_button": "1 if the default Enter/Return "
            "button is highlighted",
            "window_auto_handle_menus": "1 if the VI auto-handles its own "
            "menu selections",
            "window_can_close": "1 if the window's close button is enabled",
            "window_can_resize": "1 if the window is user-resizable",
            "window_can_minimize": "1 if the window's minimize button is "
            "enabled",
            "window_transparent": "1 if the window is drawn transparent",
            # -- toolbar_* : hidden toolbar buttons ---------------------------
            "toolbar_hide_run_button": "1 if the Run button is hidden",
            "toolbar_hide_abort_button": "1 if the Abort button is hidden",
            "toolbar_hide_free_run_button": "1 if the Run Continuously "
            "button is hidden",
            # -- instance_* : instance / poly-VI flags ------------------------
            "instance_is_system_vi": "1 if flagged as a LabVIEW system VI",
            "instance_show_poly_selector": "1 if a polymorphic VI shows its "
            "instance selector",
            "instance_hide_instance_caption": "1 if a poly-VI instance hides "
            "its caption",
            "instance_draw_instance_icon": "1 if a poly-VI instance draws its "
            "own icon",
            "instance_remote_panel": "1 if front-panel remote access is "
            "enabled",
            # -- struct_* : VI kind + compile health (VIStructure -- a facet
            # SIBLING to VI Properties above, never nested under it) --------
            "struct_is_typedef": "1 if this is a type-definition control/VI",
            "struct_is_strict_typedef": "1 if this is a STRICT "
            "type-definition control/VI",
            "struct_dynamic_dispatch": "1 if this VI participates in "
            "dynamic dispatch (a class method override point)",
            "struct_source_only": "1 if the VI is source-only/separate "
            "compiled code (no cached object code saved)",
            "struct_has_no_block_diagram": "1 if the block diagram was "
            "stripped (a run-only distribution build)",
            "struct_is_instance_vi": "1 if this is a generated poly-VI "
            "instance, not the poly wrapper itself",
            "struct_bad_node": "1 if the VI has a broken node",
            "struct_bad_subvi": "1 if the VI calls a broken subVI",
            "struct_bad_subvi_link": "1 if a subVI call link is broken "
            "(unresolved)",
            "struct_bad_compile": "1 if the VI failed to compile",
            "struct_broken_poly": "1 if a polymorphic VI has a broken variant",
            "struct_is_broken": "1 if ANY of struct_bad_node/struct_bad_subvi/"
            "struct_bad_subvi_link/struct_bad_compile/struct_broken_poly is "
            "set (precomputed OR of the five)",
        },
    ),
    "terminal": _View(
        body="FROM terminals",
        columns={
            "vi_path": "path of the VI this connector-pane terminal belongs to",
            "name": "terminal label, e.g. 'error out'",
            "direction": "'input' or 'output' ('output' == an indicator)",
            "is_indicator": "1 if an indicator (output side of the connector pane)",
            "is_public": "1 if on the public connector pane",
            "control_type": "front-panel control class, or NULL",
            "py_type": "generated Python type for this terminal (LOSSY codegen "
            "target — an enum collapses to 'int', a cluster to "
            "'dict[str, Any]'); prefer lv_type for anything that reads the "
            "type back",
            "is_error_cluster": "1 if this terminal carries a LabVIEW error cluster",
            "field_names": "JSON array of cluster field names (for cluster terminals)",
            "lv_type": "FAITHFUL LabVIEW type label, e.g. 'DBL', "
            "'MethodEnum{setUp, testMethod, tearDown}', 'error cluster', "
            "'TestCase.lvclass' — never a Python annotation",
            "enum_values": "JSON array of enum/ring member names in ordinal order "
            "(empty for non-enum terminals) — query for terminals whose enum "
            "carries a given member via e.g. "
            "\"WHERE enum_values LIKE '%\"setUp\"%'\"",
        },
    ),
    "constant": _View(
        body="FROM constants",
        columns={
            "vi_path": "path of the VI the constant lives in",
            "value": "the constant's literal value, as text",
            "label": "the constant's label, or NULL",
            "py_type": "generated Python type for the constant (LOSSY codegen "
            "target — prefer lv_type to read the type back)",
            "lv_type": "FAITHFUL LabVIEW type label, e.g. 'DBL', "
            "'error cluster', 'MethodEnum{setUp, tearDown}' — never a Python "
            "annotation",
            "wired_to": "what the constant wires into, e.g. 'indicator'",
        },
    ),
    "call": _View(
        body="FROM calls",
        columns={
            "caller_path": "path of the calling VI",
            "callee_key": "qualified name of the callee (join vi.qualified_name)",
        },
    ),
    "type_use": _View(
        body="FROM type_uses",
        columns={
            "vi_path": "path of the VI referencing the type",
            "type_key": "a class or typedef name the VI's terminals reference",
        },
    ),
    "class_fact": _View(
        body="FROM class_facts",
        columns={
            "vi_path": "path of the class-member VI",
            "owning_class": "the .lvclass this VI is a method of",
            "parent": "the parent class, or NULL",
            "scope": "member scope (public/private/protected/community), or NULL",
            "is_accessor": "1 if a generated property/accessor VI",
            "accessor_field": "the class field this accessor reads/writes, or NULL",
            "private_data": "JSON array of the owning class's private-data fields "
            "(incl. inherited), each FAITHFULLY rendered 'name: <lv_type>', e.g. "
            "'[\"testName: String\", \"result: TestResult.lvclass\"]'",
            "is_static": "1 if this method is a static (non-dynamic-dispatch) "
            "class method",
            "must_override": "1 if a child class MUST provide its own "
            "implementation of this dynamic-dispatch method",
            "must_call_parent": "1 if an override of this method MUST call its "
            "parent implementation",
            "class_version": "the owning class's NI.Lib.Version (dotted-quad "
            "string, e.g. '1.0.0.7'), or NULL if absent",
            "ancestors": "JSON array of the owning class's FULL ancestor chain, "
            "nearest-first (immediate parent first); may be a PREFIX of the true "
            "chain if an ancestor's .lvclass isn't present in this checkout",
        },
    ),
    "lvproj": _View(
        body="FROM lvproj_members",
        columns={
            "lvproj_path": "abs path to the .lvproj file (a LabVIEW project)",
            "lvproj_name": "the .lvproj stem, e.g. 'VIUnit'",
            "member_name": "member item name as declared in the .lvproj",
            "member_url": "raw member URL from the .lvproj (before path resolution)",
            "resolved_path": "abs on-disk path the URL resolves to, or NULL",
            "member_type": "member kind by extension: VI | Control | LVClass | Library",
            "is_in_repo": "1 if resolved_path is a file UNDER the indexed repo root",
            "target": "build/execution target it sits under, e.g. 'My Computer'",
            "is_dependency": "1 if in the auto-collected Dependencies group "
            "(transitive ref, mostly vi.lib); 0 if the project's own content",
        },
    ),
}


@dataclass
class ViewColumn:
    name: str
    description: str


@dataclass
class ViewInfo:
    name: str
    columns: list[ViewColumn]


@dataclass
class QueryResult:
    """A columnar result set (design §9.1 — compact for token economy)."""

    columns: list[str]
    rows: list[list[object]]
    truncated: bool = False
    row_count: int = field(default=0)

    def __post_init__(self) -> None:
        self.row_count = len(self.rows)


def describe_schema() -> list[ViewInfo]:
    """List the queryable views and their columns (schema introspection).

    An agent should call this first so it writes SQL against real column names
    instead of hallucinating them (design §3b)."""
    return [
        ViewInfo(
            name=name,
            columns=[ViewColumn(col, desc) for col, desc in view.columns.items()],
        )
        for name, view in VIEWS.items()
    ]


def _install_views(conn: sqlite3.Connection) -> None:
    """Define the public views as TEMP views on a read-only connection.

    TEMP views live in the connection's writable temp schema, so this works
    even though the main DB is opened read-only. Done BEFORE the authorizer is
    installed, so the CREATEs aren't denied.
    """
    for name, view in VIEWS.items():
        conn.execute(
            f"CREATE TEMP VIEW {name} AS SELECT {view.select_list} {view.body}"
        )


def _authorizer(
    action: int, _arg1: object, _arg2: object, _db_name: object, _trigger: object
) -> int:
    """Deny every action except reads — the structural read-only boundary.

    Blocks PRAGMA/ATTACH/DETACH and every write even inside a crafted single
    statement. Read-only file mode already stops writes; this is defence in
    depth AND the only thing that stops PRAGMA/ATTACH on a read-only handle.
    """
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


_LEADING_NOISE = re.compile(r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s+)", re.DOTALL)


def _first_keyword(sql: str) -> str:
    """The first SQL keyword, skipping leading whitespace and comments."""
    s = sql
    while True:
        m = _LEADING_NOISE.match(s)
        if not m or m.end() == 0:
            break
        s = s[m.end() :]
    m = re.match(r"[A-Za-z_]+", s)
    return m.group(0).upper() if m else ""


def run_query(
    project_root: Path,
    sql: str,
    *,
    row_cap: int = DEFAULT_ROW_CAP,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> QueryResult:
    """Run one read-only ``SELECT``/``WITH`` against the project's index views.

    Raises :class:`QueryError` if the project has no index yet, the statement
    is not a single SELECT/CTE, or the query exceeds ``timeout_s``.
    """
    kw = _first_keyword(sql)
    if kw not in ("SELECT", "WITH"):
        raise QueryError(
            "only read-only SELECT/WITH queries are allowed "
            f"(statement started with {kw or '<empty>'!r})"
        )

    db_file = store.db_path(project_root)
    if not db_file.exists():
        raise QueryError(
            f"no index for {project_root} — build it first (e.g. `lvkit index`)"
        )

    # mode=ro (NOT immutable): immutable would ignore the -wal file and miss
    # rows a writer left in the WAL that aren't checkpointed yet.
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        _install_views(conn)

        deadline = time.monotonic() + timeout_s

        def _progress() -> int:
            return 1 if time.monotonic() > deadline else 0

        conn.set_progress_handler(_progress, 2000)
        conn.set_authorizer(_authorizer)

        try:
            cur = conn.execute(sql)
            fetched = cur.fetchmany(row_cap + 1)
        except sqlite3.OperationalError as e:
            # progress-handler abort surfaces as "interrupted"
            if "interrupt" in str(e).lower():
                raise QueryError(
                    f"query exceeded the {timeout_s:g}s time limit"
                ) from e
            raise QueryError(str(e)) from e
        except sqlite3.Warning as e:
            # stacked statements ("SELECT 1; DROP TABLE vis") — sqlite3.Warning
            # is NOT a subclass of sqlite3.Error, so it needs its own clause.
            raise QueryError(
                f"one statement per query only ({e})"
            ) from e
        except sqlite3.Error as e:
            # includes "not authorized" from the authorizer (defence in depth)
            raise QueryError(str(e)) from e

        columns = [d[0] for d in cur.description] if cur.description else []
        truncated = len(fetched) > row_cap
        rows = [list(r) for r in fetched[:row_cap]]
        return QueryResult(columns=columns, rows=rows, truncated=truncated)
    finally:
        conn.close()


# --- Canned aggregates (hot paths — design §3b, §7.2) -----------------------

ERROR_INDICATOR_HISTOGRAM_SQL = (
    "SELECT name, COUNT(*) AS n FROM terminal "
    "WHERE is_error_cluster = 1 AND direction = 'output' "
    "GROUP BY name ORDER BY n DESC, name"
)


def error_indicator_histogram(project_root: Path) -> QueryResult:
    """Count the names this project uses for error indicators (the driving
    question) — returns the small histogram, never the raw terminal rows."""
    return run_query(project_root, ERROR_INDICATOR_HISTOGRAM_SQL)


UNCALLED_VIS_SQL = (
    "SELECT path, name, library FROM vi WHERE callers_count = 0 ORDER BY name"
)


def uncalled_vis(project_root: Path) -> QueryResult:
    """The project's dead code: VIs that no in-repo VI calls (entry points and
    orphans). A straight ``callers_count = 0`` filter on the ``vi`` view — NOT
    the fragile ``qualified_name`` / ``callee_key`` name anti-join, which
    misfires because ``qualified_name`` is often NULL and ``callee_key`` holds
    bare filenames, never qualified names."""
    return run_query(project_root, UNCALLED_VIS_SQL)
