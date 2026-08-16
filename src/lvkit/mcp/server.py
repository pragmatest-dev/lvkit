"""MCP server for VI analysis — project-scoped, index-backed.

Built on the decorator-style server the mcp SDK ships: ``FastMCP`` in mcp 1.x
(``mcp.server.fastmcp``), renamed ``MCPServer`` in mcp 2.0
(``mcp.server.mcpserver``) with the SAME ``@tool``/``run``/``list_tools`` API.
We import whichever exists (see below) so lvkit runs on both majors. This
supersedes the module-global ``mcp.server.Server`` + ``@app.list_tools()`` build
this module used to have (that decorator was removed in mcp 2.0, silently
disabling the server — see ``docs/_internal/design/lvkit-mcp-improvements.md``).

Two tool groups (understanding only — artifact generation lives in the CLI):

1. **Index-backed, project-scoped** (``index``, ``query``, ``query_schema``,
   ``visualize_project``) — answer *project-wide* questions in one call from the
   persisted, path-keyed facts index (``lvkit.index``). The ``query`` tool is
   read-only SQL over a curated view layer — it returns the *answer* (a
   ``GROUP BY`` histogram), not the source rows, and REPLACES the old
   per-question read tools (``find_terminals``/``find_constants``/…, retired
   2026-08-08) AND the former graph-op tools: the call graph is now the ``node``
   view's ``kind='vi'`` slice (``callee_path``), so callers/callees are one-hop
   selects and blast radius a recursive CTE (``vi.callers_count`` /
   ``vi.impact_score`` give the counts). No per-VI round trips. A per-project
   cache,
   NOT a single global graph any ``clear`` could nuke — safe for an agent
   working across several repos in one session.

2. **Deep single-VI** (``describe`` for prose, ``read_vi`` for the structured
   netlist) — full dataflow detail for ONE VI, loaded live on demand (XML
   already cached). The Serena split: bulk/navigation off the index, depth on
   demand. An AI CONVERTS a VI by understanding it here and writing idiomatic
   Python itself — lvkit's deterministic AST generator is a CLI/oracle tool, not
   an MCP crutch.

Artifact generation (Python packages, HTML docs, pyvis graphs, diffs, renders)
is CLI-only (``lvkit generate``/``docs``/``visualize``/``diff``/``render``): it
writes files, belongs in scripts/CI, and keeps the MCP a pure, in-process
understanding surface with no subprocess or non-packaged-``scripts/`` dependency.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

# Whichever the installed SDK ships: mcp 2.0's MCPServer or 1.x's FastMCP. Only
# one module exists at a time, so the OTHER branch is unresolvable to the type
# checker — suppress the missing-import there (both are annotated so it holds
# under whichever major is installed).
try:  # mcp >= 2.0 renamed FastMCP -> MCPServer (identical decorator API)
    from mcp.server.mcpserver import Context  # type: ignore
    from mcp.server.mcpserver import MCPServer as _MCPServer  # type: ignore
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context  # type: ignore
    from mcp.server.fastmcp import FastMCP as _MCPServer  # type: ignore

from .. import primitive_resolver, vilib_resolver
from ..graph import InMemoryVIGraph
from ..graph.describe import (
    describe_vi as describe_vi_text,
)
from ..graph.netlist import build_netlist, netlist_to_dict
from ..index import sql as isql
from ..index.build import (
    build_index,
    build_lvproj_membership,
    refresh_index,
    warm_all_loaded,
)
from ..index.model import VIFacts
from ..index.project import resolve_project
from ..index.store import db_path as store_db_path
from ..index.store import delete as store_delete
from ..index.store import load as store_load
from ..index.store import save as store_save
from ..index.store import save_lvproj_members as store_save_lvproj_members
from ..load_mode import LoadMode
from ..project_store import find_project_store

_INSTRUCTIONS = """\
lvkit reads LabVIEW code. A LabVIEW project (`.vi`, `.lvclass`, `.lvlib`,
`.lvproj`) is a BINARY format — `grep`, `cat`, `find`, and ad-hoc `python`
scripts CANNOT parse it and return nothing usable. In a LabVIEW repo these
tools are your ONLY way to see the code, so reach for them FIRST; do not grep
a `.vi`.

lvkit indexes the whole REPOSITORY — every `.vi` on disk. A repo may hold many
LabVIEW PROJECTS (`.lvproj` files); a VI can belong to several of them or none.
"The project" is therefore ambiguous — don't assume one `.lvproj` scopes the
repo. To filter by an actual LabVIEW project, use the `lvproj` view (membership
is many-to-many): "classes in VIUnit.lvproj" is `SELECT member_name FROM lvproj
WHERE lvproj_name='VIUnit' AND member_type='LVClass'`.

For any question about the project, start here:

- Structure, classes & inheritance, terminals, constants, type usage,
  `.lvproj` membership — `query` runs read-only SQL over the project's facts
  index (views: `vi`, `class_fact`, `terminal`, `constant`, `node`,
  `type_use`, `lvproj`; call `query_schema` for columns). "What classes exist
  and how do they inherit?" is `SELECT owning_class, parent FROM class_fact`.
  It returns the answer (e.g. a GROUP BY histogram), not a row dump.
- Find a block-diagram PATTERN across every VI at once — the `node` view is
  grep for VI code: one row per node (a primitive, SubVI call, structure,
  constant, ...) with its `kind`, robust identity (`prim_id` for primitives,
  `qualified_name` for SubVI calls), and STRUCTURAL containment (`parent_uid`,
  `frame`) — but NO wiring. This is grep-not-read: `query` the `node` view to
  find WHICH VIs match a pattern, then read the actual dataflow of a hit with
  `read_vi`. Robust filters are `prim_id`/`qualified_name`, not `name`.
  Worked slices:
    - Callers of a VI: `SELECT DISTINCT vi_path FROM node WHERE
      callee_path='<abs path of MyVI.vi>'` (or the vi.callers_count column).
    - A structure containing another (e.g. an event-handler loop): self-join
      `node c JOIN node p ON c.parent_uid=p.uid AND c.vi_path=p.vi_path
      WHERE c.kind='event' AND p.kind='while'`; walk full nesting with a
      `WITH RECURSIVE` over `parent_uid`.
    - Producer/consumer (queues, user events): filter by the queue/event
      `prim_id` (enumerate with `SELECT DISTINCT prim_id, name FROM node
      WHERE name LIKE '%Enqueue%'`) or by `qualified_name` for vi.lib VIs,
      then `read_vi` each hit to trace the named message/refnum.
- Who calls what / change impact — the call graph is the `node` view's
  `kind='vi'` slice via `callee_path`. Direct callers of X:
  `SELECT DISTINCT vi_path FROM node WHERE callee_path='<abs path of X>'`;
  direct callees: `SELECT callee_path FROM node WHERE vi_path='<X>' AND
  kind='vi' AND callee_path IS NOT NULL`. Transitive blast radius: a
  `WITH RECURSIVE deps(p) AS (SELECT vi_path FROM node WHERE callee_path=:x
  UNION SELECT n.vi_path FROM node n JOIN deps ON n.callee_path=deps.p) …`.
  For the COUNTS, `vi.callers_count` (0 == no static caller) and
  `vi.impact_score` are precomputed columns — no CTE needed.
- One VI in depth (pass a path, no load step) — `describe` (prose) or
  `read_vi` (structured netlist: operations, wiring, structures, constants).
- Convert a VI to Python — UNDERSTAND it with `read_vi`/`query`, then write
  idiomatic Python yourself. (lvkit's deterministic AST generator lives in the
  `lvkit generate` CLI — use it as a reference/oracle, not the primary path.)
- Artifacts (Python packages, HTML docs, graphs, diffs, renders) are the
  `lvkit` CLI's job (`generate`/`docs`/`visualize`/`diff`/`render`) — they write
  files; point the user at the command.

`query` operates on the whole project at once and
build/refresh the index automatically. Prefer them over per-VI round-trips.
"""

mcp = _MCPServer("lvkit-mcp", instructions=_INSTRUCTIONS)

# Per-project-root facts cache: {resolved project_root str -> [VIFacts]}. This
# replaces the old module-global _graph — different repos get different entries,
# so parallel agents on different projects never collide, and there is no
# session-wide `clear` that wipes another caller's state.
_indexes: dict[str, list[VIFacts]] = {}


# ===== Workspace-root defaulting =====
#
# MCP clients advertise the folder(s) the user opened (VS Code / Claude Code
# workspace) as `roots`. We read them so a caller who has already opened their
# VI repo never has to repeat its path: `project` defaults to the first root,
# and a relative VI path resolves under it. Passing an explicit path still wins
# (multi-repo sessions, headless agents), and a client that doesn't support
# roots simply falls back to that explicit argument.

# A Windows VS Code / Claude client speaks Windows paths (`C:\repo`, and roots
# arrive as `file:///C:/repo`). When the server itself runs INSIDE WSL — the
# common "run the WSL checkout, drive it from a Windows editor" setup — those
# paths must be re-expressed as the WSL mount (`/mnt/c/repo`) or nothing
# resolves. Applied at every point a path enters the server (roots + explicit
# args), so `project` defaulting and relative targets work across the boundary.
_WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_WSL_UNC = re.compile(r"^\\\\wsl(?:\.localhost|\$)\\[^\\]+\\(.*)$")


def _win_to_wsl_path(p: str) -> str:
    """Map a Windows path to its WSL-visible form; a no-op on native Windows or
    for an already-POSIX path.

    - ``C:\\repo`` / ``C:/repo`` -> ``/mnt/c/repo`` (WSL's default automount;
      override the mount root in ``/etc/wsl.conf`` if you've changed it).
    - ``\\\\wsl.localhost\\Ubuntu\\home\\x`` (a WSL folder opened FROM Windows)
      -> ``/home/x``.
    """
    if os.name == "nt":
        return p  # native Windows process — its own paths are already correct
    m = _WIN_DRIVE.match(p)
    if m:
        return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}"
    m = _WSL_UNC.match(p)
    if m:
        return "/" + m.group(1).replace("\\", "/")
    return p


def _uri_to_path(uri: str) -> Path:
    """Convert a ``file://`` URI (as sent in MCP roots) to a local path.

    Handles POSIX (``file:///home/x``), Windows (``file:///C:/Users/x``, which
    arrives as ``/C:/Users/x``), and — when the server runs under WSL — maps a
    Windows drive path onto its ``/mnt`` mount (see :func:`_win_to_wsl_path`).
    """
    path = unquote(urlparse(uri).path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]  # Windows drive path: '/C:/...' -> 'C:/...'
    return Path(_win_to_wsl_path(path))


async def _client_roots(ctx: Context | None) -> list[Path]:
    """Workspace folders the client advertised (empty if unsupported/declined).

    Never raises: a client without the roots capability just yields ``[]``, and
    the explicit path argument is the documented fallback.
    """
    if ctx is None:
        return []
    try:
        result = await ctx.session.list_roots()
    except Exception:
        return []
    return [_uri_to_path(str(r.uri)) for r in result.roots]


# Default project roots for a client that sends NO roots (notably Claude
# Desktop). Set from the positional dirs of ``lvkit mcp <dir>...`` — the ``.mcpb``
# expands the user's picked VI folders here (``multiple`` supported). Empty until
# ``main`` populates it.
_DEFAULT_ROOTS: list[str] = []


def _default_roots() -> list[str]:
    """Fallback project roots for a client that sends NO roots — notably Claude
    Desktop, which has no workspace concept. In order: the folders passed to
    ``lvkit mcp <dir>...`` (the ``.mcpb`` expands the user's picked VI folders,
    ``multiple`` supported), else the legacy single ``LVKIT_PROJECT_ROOT`` env,
    else the cwd. A client that sends workspace roots takes precedence over these
    (see ``_resolve_project``/``_resolve_target``)."""
    if _DEFAULT_ROOTS:
        return list(_DEFAULT_ROOTS)
    env = os.environ.get("LVKIT_PROJECT_ROOT")
    if env:
        return [env]
    return [str(Path.cwd())]


async def _resolve_project(project: str | None, ctx: Context | None) -> str:
    """Resolve a project path: explicit ``project`` > the client's workspace
    roots > the configured default roots (``lvkit mcp <dir>...`` / env / cwd).

    A client's workspace uses its FIRST root (its primary workspace). Among the
    CONFIGURED default roots (Claude Desktop), a single one is used automatically,
    but when SEVERAL are configured and no explicit ``project`` was given this
    raises with the list rather than silently picking one — pass ``project=<path>``
    (or call ``list_projects``) to choose. An explicit ``project`` may be a Windows
    path when a Windows editor drives a WSL-hosted server — mapped onto the WSL
    mount."""
    if project:
        return _win_to_wsl_path(project)
    roots = await _client_roots(ctx)
    if roots:
        return str(roots[0])
    defaults = _default_roots()
    if len(defaults) == 1:
        return _win_to_wsl_path(defaults[0])
    raise ValueError(
        "Several project roots are configured; pass project=<path> to pick one "
        "(or call list_projects). Roots: " + ", ".join(defaults)
    )


async def _resolve_target(target: str, ctx: Context | None) -> str:
    """Resolve a VI/library path, allowing it to be relative to a client root.

    An absolute path is used verbatim. A relative one is tried under each client
    root and the first existing match wins — so ``describe("Classes/Foo/run.vi")``
    works when the client is opened in that repo. If nothing matches it is
    returned unchanged, so the tool raises its own ``FileNotFoundError``.
    """
    # Map first: a Windows ``C:\...`` arg isn't ``is_absolute()`` on a Linux
    # (WSL) host, so it must be translated before the absolute-vs-relative test.
    target = _win_to_wsl_path(target)
    if Path(target).is_absolute():
        return target
    for root in await _client_roots(ctx):
        if (root / target).exists():
            return str(root / target)
    # No client root matched — try each configured default root (Claude Desktop
    # path: the .mcpb-picked VI folders / env / cwd).
    for d in _default_roots():
        default_root = Path(_win_to_wsl_path(d))
        if (default_root / target).exists():
            return str(default_root / target)
    return target


def _configure_resolvers_for_vi(vi_path: str | Path) -> None:
    """Discover ``.lvkit/`` from a path and reset the primitive/vilib resolvers.

    One MCP session may serve several projects, so the project store is
    re-resolved on every call that knows a target path. The path may be a file
    (a ``.vi``), a directory, or not exist yet — we start from the path itself
    when it is a directory, its parent otherwise, then walk up for ``.lvkit/``.
    """
    p = Path(vi_path).resolve()
    start = p if p.is_dir() else p.parent
    store = find_project_store(start=start)
    primitive_resolver.reset_resolver(project_data_dir=store)
    vilib_resolver.reset_resolver(project_data_dir=store)


# ===== Index (project-scoped) =====


def _get_index(project: str, *, rebuild: bool = False) -> tuple[Path, list[VIFacts]]:
    """Resolve ``project`` to its root and return ``(root, facts)``.

    Loads the persisted index when present; builds + saves it on first use (or
    when ``rebuild``). Caches the facts per resolved root for the session.

    The in-memory ``_indexes`` cache must never mask an ABSENT on-disk store:
    the ``query`` tool reads the SQLite file (not these facts), so if the cache
    dir was cleared/moved out from under a long-lived server, a cache hit would
    otherwise return facts while ``run_query`` sees no DB. So a hit whose DB file
    is gone falls through to a rebuild+save.
    """
    root, vi_paths = resolve_project(Path(project))
    key = str(root)
    if rebuild or key not in _indexes or not store_db_path(root).exists():
        _configure_resolvers_for_vi(root)
        stored = [] if rebuild else store_load(root)
        if not stored:
            # Cold store: one fast whole-repo build.
            result = build_index(root, vi_paths)
            store_save(root, result.facts)
            store_save_lvproj_members(root, result.lvproj_members)
            facts = result.facts
        else:
            # Warm/partial store — progressively populated by single-VI loads.
            # Gap-fill: reuse fresh rows, (re)build only missing/changed VIs, and
            # recompute impact across the merged set (warmed rows carry
            # impact_score=0 until a pass like this fills the global inverse).
            rr, facts = refresh_index(root, vi_paths, stored)
            store_delete(root, rr.deleted)
            store_save(root, facts)
            # Membership is a cheap .lvproj-only reparse — refresh it wholesale
            # so the `lvproj` view reflects added/removed projects.
            store_save_lvproj_members(root, build_lvproj_membership(root))
        _indexes[key] = facts
    return root, _indexes[key]


@mcp.tool()
async def list_projects(ctx: Context | None = None) -> dict[str, Any]:
    """List the project roots this server can answer for.

    ``client_roots`` are what the client provided (an IDE / Claude Code
    workspace), if any; ``configured_roots`` are the folders set at install
    (``lvkit mcp <dir>...`` / the ``.mcpb`` VI folders / env / cwd). Pass one as a
    tool's ``project=`` to scope a call — when exactly one root is active it is
    used automatically, so you only need this when several are configured."""
    client = [str(r) for r in await _client_roots(ctx)]
    return {
        "client_roots": client,
        "configured_roots": _default_roots(),
    }


@mcp.tool()
async def index(
    project: str | None = None,
    refresh: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Build or refresh the code-understanding index for a whole VI repo.

    ``project`` is any path inside the repo (a directory, ``.lvproj``,
    ``.lvclass``, ``.lvlib``, or ``.vi``); the enclosing project root is indexed.
    Omit it to index the folder the client is opened in (the first workspace
    root).
    Every ``.vi`` is projected into a persisted, path-keyed facts row — so
    same-named VIs (``setUp.vi`` ×17) never collide the way a name-keyed graph
    does. Run this once; the other project-scoped tools then answer in sub-ms.

    ``refresh=True`` does an incremental update of an existing index — rebuild
    only VIs whose content changed (or were added), drop deleted ones — instead
    of a full rebuild. Returns the VI count (full build) or the rebuilt/deleted
    counts (refresh), plus the resolved project root.
    """
    project = await _resolve_project(project, ctx)

    def _work() -> dict[str, Any]:
        start = time.monotonic()
        root, vi_paths = resolve_project(Path(project))
        _configure_resolvers_for_vi(root)
        stored = store_load(root) if refresh else []
        if stored:
            rr, merged = refresh_index(root, vi_paths, stored)
            store_delete(root, rr.deleted)
            store_save(root, merged)
            store_save_lvproj_members(root, build_lvproj_membership(root))
            _indexes[str(root)] = merged
            return {
                "project_root": str(root),
                "rebuilt": len(rr.rebuilt),
                "deleted": len(rr.deleted),
                "total": rr.total,
                "ms": round((time.monotonic() - start) * 1000),
            }
        result = build_index(root, vi_paths)
        store_save(root, result.facts)
        store_save_lvproj_members(root, result.lvproj_members)
        _indexes[str(root)] = result.facts
        return {
            "project_root": str(root),
            "vis": len(result.facts),
            "collisions": result.collisions,
            "ms": round((time.monotonic() - start) * 1000),
        }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def query(
    sql: str,
    project: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Read a LabVIEW project's structure — classes & inheritance, terminals,
    constants, calls, type usage — by running one read-only SQL
    ``SELECT``/``WITH`` over its facts index. The primary way to answer
    project-wide questions about a repo you can't grep (``.vi`` is binary);
    returns **just the answer** (a server-computed ``GROUP BY`` histogram, not a
    row dump). "What classes exist and how do they inherit?" is
    ``SELECT owning_class, parent FROM class_fact``.

    Query these curated views (call ``query_schema`` for their columns):
    ``vi``, ``terminal``, ``constant``, ``node`` (block-diagram nodes — grep for
    VI code: primitives/SubVI-calls/structures with kind + identity +
    containment + resolved ``callee_path``, no wiring), ``type_use``,
    ``class_fact``, ``lvproj`` (which VIs/classes belong to which ``.lvproj``).
    Example — the names this project uses for error indicators, as a histogram
    rather than 406 raw rows::

        SELECT name, COUNT(*) AS n FROM terminal
        WHERE type_descriptor = 'Error' AND direction = 'output'
        GROUP BY name ORDER BY n DESC

    Returns ``{columns, rows, row_count, truncated}`` (columnar). Only a single
    SELECT/CTE is allowed — writes, ``PRAGMA``, ``ATTACH`` and stacked statements
    are refused. The index is built/refreshed automatically on first use.
    ``project`` defaults to the client's workspace root. The call graph is the
    ``node`` view: direct callers = ``WHERE callee_path=…``, transitive blast
    radius = a ``WITH RECURSIVE`` over ``callee_path`` (``vi.callers_count`` /
    ``vi.impact_score`` are the precomputed counts).
    """
    project = await _resolve_project(project, ctx)

    def _work() -> dict[str, Any]:
        root, _ = _get_index(project)
        return asdict(isql.run_query(root, sql))

    return await asyncio.to_thread(_work)


@mcp.tool()
async def query_schema() -> list[dict[str, Any]]:
    """List the views and columns available to the ``query`` tool — call this
    first so your SQL uses real column names instead of guessing. Each entry is
    ``{name, columns:[{name, description}]}``."""
    return [asdict(v) for v in isql.describe_schema()]


# ===== Deep single-VI (load on demand) =====


def _load_one(vi_path: str) -> tuple[InMemoryVIGraph, str]:
    """Load ONE VI (MINIMAL) into a fresh graph and return ``(graph, vi_name)``.

    A MINIMAL load also leaf-loads direct SubVIs, so ``list_vis()`` may hold
    several names; we pick the one whose source path IS ``vi_path``.
    """
    p = Path(vi_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"VI not found: {vi_path}")
    _configure_resolvers_for_vi(p)
    graph = InMemoryVIGraph()
    graph.load_vi(p, LoadMode.MINIMAL, search_paths=[p.parent])
    vi_name: str | None = None
    for name in graph.list_vis():
        src = graph.get_vi_source_path(name)
        if src is not None and src.resolve() == p:
            vi_name = name
            break
    if vi_name is None:
        # Fall back to the leaf-name resolver (single-VI graphs usually have one)
        vi_name = graph.resolve_vi_name(p.name)
    # Progressive index: every parse warms the store — a MINIMAL load parses
    # this VI AND its SubVIs, so warm all of them (accumulates as the repo is
    # used).
    warm_all_loaded(graph)
    return graph, vi_name


@mcp.tool()
async def describe(vi_path: str, ctx: Context | None = None) -> str:
    """Human-readable purpose, signature, SubVI calls, and control flow for one
    VI (loaded on demand). The prose read; for the STRUCTURED form (a program
    parsing the result), use ``read_vi``. ``vi_path`` may be relative to the
    client's workspace root.
    """
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        return describe_vi_text(graph, vi_name)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def read_vi(vi_path: str, ctx: Context | None = None) -> dict[str, Any]:
    """READ one VI in full — its structure as the canonical **netlist IR**
    ``{vi, inputs, outputs, components, body}`` — for a program (not a person)
    to parse. This is the "read" to ``query``'s "grep": grep the ``node`` view
    to find WHICH VIs match a pattern, then ``read_vi`` a hit to see its actual
    wiring/dataflow. Loaded on demand; the structured counterpart to
    ``describe``'s prose.

    Boundary ``inputs``/``outputs`` carry the FAITHFUL LabVIEW type descriptor
    (``Error``, ``TestSuite.lvclass``, ``method--Enum{setUp, tearDown}``); each
    ``output`` also carries a ``source`` net (which producer drives that indicator, or
    ``null`` if unwired). The ``body`` is a ``kind``-tagged ``instance``/``scope``
    tree (scopes nest their frames' bodies, wiring as ``port -> source.net``
    bindings), and ``components`` are the distinct subVI/primitive typed
    interfaces. A structure's OUTPUT is a named Gated-SSA **merge net** a
    consumer references by name: a scope's ``outputs`` carry
    ``case{id}.out{k}`` = ``gamma`` (selector-dependent), ``loop{id}.shift{k}``
    = ``mu`` (shift register), ``loop{id}.out{k}`` = ``eta`` (loop output,
    array/last), and a feedback node is ``fb{k}`` = ``mu``. ``vi_path`` may be
    relative to the client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> dict[str, Any]:
        graph, vi_name = _load_one(vi_path)
        return netlist_to_dict(build_netlist(graph, vi_name))

    return await asyncio.to_thread(_work)


@mcp.tool()
async def unresolved(
    target: str,
    search_paths: list[str] | None = None,
    ctx: Context | None = None,
) -> list[dict[str, Any]]:
    """Every unknown primitive / unmapped vi.lib VI under ``target`` (a VI,
    library, class, or directory), collected in ONE pass instead of the
    one-at-a-time ``PrimitiveResolutionNeeded``/``VILibResolutionNeeded`` the
    conversion loop raises. Use before converting a large library to triage the
    gaps up front. Returns a list of ``{kind, identifier, name, count,
    vi_names}`` (kind ∈ ``unknown_primitive``/``unmapped_vilib``/
    ``terminal_mapping``). Empty list means no gaps. ``target`` may be relative
    to the client's workspace root."""
    target = await _resolve_target(target, ctx)
    _configure_resolvers_for_vi(target)

    def _work() -> list[dict[str, Any]]:
        from ..unresolved import collect_unresolved

        items = collect_unresolved(
            target,
            search_paths=[Path(p) for p in (search_paths or [])],
        )
        return [
            {
                "kind": it.kind,
                "identifier": it.identifier,
                "name": it.name,
                "count": it.count,
                "vi_names": it.vi_names,
            }
            for it in items
        ]

    return await asyncio.to_thread(_work)


# ===== Entry points =====


def selftest() -> int:
    """Initialize the server, list its tools, and return the count.

    The same registration path a client's ``initialize`` -> ``tools/list``
    handshake hits — so a broken build (e.g. an ``mcp`` API break) is one command
    away from a non-zero exit instead of appearing as a silently absent server.
    Used by ``lvkit mcp --selftest`` and CI.
    """
    tools = asyncio.run(mcp.list_tools())
    return len(tools)


def main(roots: list[str] | None = None) -> None:
    """Run the MCP server over stdio (entry point).

    ``roots`` are default project roots — the positional ``lvkit mcp <dir>...``
    args, which the ``.mcpb`` expands from the user's picked VI folders — used for
    clients that send no workspace roots (Claude Desktop). Empty/omitted falls
    back to the ``LVKIT_PROJECT_ROOT`` env, then cwd (see ``_default_roots``)."""
    global _DEFAULT_ROOTS
    if roots:
        _DEFAULT_ROOTS = [_win_to_wsl_path(r) for r in roots]
    mcp.run("stdio")


if __name__ == "__main__":
    main()
