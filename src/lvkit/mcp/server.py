"""MCP server for VI analysis — project-scoped, index-backed.

Built on the decorator-style server the mcp SDK ships: ``FastMCP`` in mcp 1.x
(``mcp.server.fastmcp``), renamed ``MCPServer`` in mcp 2.0
(``mcp.server.mcpserver``) with the SAME ``@tool``/``run``/``list_tools`` API.
We import whichever exists (see below) so lvkit runs on both majors. This
supersedes the module-global ``mcp.server.Server`` + ``@app.list_tools()`` build
this module used to have (that decorator was removed in mcp 2.0, silently
disabling the server — see ``docs/_internal/design/lvkit-mcp-improvements.md``).

Three tool groups:

1. **Index-backed, project-scoped** (``index``, ``query``, ``query_schema``,
   ``get_callers``, ``get_callees``, ``blast_radius``, ``visualize_project``) —
   answer *project-wide* questions in one call from the persisted, path-keyed
   facts index (``lvkit.index``). The ``query`` tool is read-only SQL over a
   curated view layer — it returns the *answer* (a ``GROUP BY`` histogram), not
   the source rows, and REPLACES the old per-question read tools
   (``find_terminals``/``find_constants``/``find_symbols``/``find_type_usages``/
   ``get_signatures``, retired 2026-08-08). Reachability stays typed:
   ``get_callers``/``get_callees``/``blast_radius`` are graph ops, not SQL. No
   per-VI round trips, no name-collision bug. State is a per-project-root cache,
   NOT a single global graph any ``clear`` could nuke — safe for an agent
   working across several repos in one session.

2. **Deep single-VI** (``describe``, ``get_operations``, ``get_dataflow``,
   ``get_structure``, ``get_constants``, ``get_context``, ``generate_ast_code``)
   — token-heavy dataflow detail for ONE VI, loaded live on demand (XML already
   cached). The Serena split: bulk/navigation off the index, depth on demand.

3. **Stateless generators** (``generate_documents``, ``generate_python``) —
   unchanged, subprocess-free wrappers over the same pipeline as the CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:  # mcp >= 2.0 renamed FastMCP -> MCPServer (identical decorator API)
    from mcp.server.mcpserver import Context, MCPServer as _MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP as _MCPServer

from .. import primitive_resolver, vilib_resolver
from ..codegen import build_module
from ..graph import InMemoryVIGraph
from ..graph.describe import (
    describe_constants as describe_constants_text,
)
from ..graph.describe import (
    describe_dataflow as describe_dataflow_text,
)
from ..graph.describe import (
    describe_operations as describe_operations_text,
)
from ..graph.describe import (
    describe_structure as describe_structure_text,
)
from ..graph.describe import (
    describe_vi as describe_vi_text,
)
from ..index import query as iq
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
from .tools import generate_documents as _gen_documents
from .tools import generate_python as _gen_python

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
  index (views: `vi`, `class_fact`, `terminal`, `constant`, `call`,
  `type_use`, `lvproj`; call `query_schema` for columns). "What classes exist
  and how do they inherit?" is `SELECT owning_class, parent FROM class_fact`.
  It returns the answer (e.g. a GROUP BY histogram), not a row dump.
- Who calls what / change impact — `get_callers`, `get_callees`,
  `blast_radius` (transitive; not expressible in SQL).
- A whole-project call graph / class tree diagram — `visualize_project`.
- One VI in depth (pass a path, no load step) — `describe`,
  `get_operations`, `get_dataflow`, `get_structure`, `get_constants`,
  `get_context`.
- Convert a VI to Python — `generate_python` / `generate_ast_code`; docs
  site — `generate_documents`.

`query`, `get_callers` and friends operate on the whole project at once and
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


async def _resolve_project(project: str | None, ctx: Context | None) -> str:
    """Resolve a project path: explicit ``project`` > first client root > cwd.

    An explicit ``project`` may be a Windows path when a Windows editor drives a
    WSL-hosted server — map it onto the WSL mount so it resolves."""
    if project:
        return _win_to_wsl_path(project)
    roots = await _client_roots(ctx)
    if roots:
        return str(roots[0])
    return str(Path.cwd())


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
async def index(
    project: str | None = None, refresh: bool = False, ctx: Context | None = None,
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
    import time

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
    sql: str, project: str | None = None, ctx: Context | None = None,
) -> dict[str, Any]:
    """Read a LabVIEW project's structure — classes & inheritance, terminals,
    constants, calls, type usage — by running one read-only SQL
    ``SELECT``/``WITH`` over its facts index. The primary way to answer
    project-wide questions about a repo you can't grep (``.vi`` is binary);
    returns **just the answer** (a server-computed ``GROUP BY`` histogram, not a
    row dump). "What classes exist and how do they inherit?" is
    ``SELECT owning_class, parent FROM class_fact``.

    Query these curated views (call ``query_schema`` for their columns):
    ``vi``, ``terminal``, ``constant``, ``call``, ``type_use``, ``class_fact``,
    ``lvproj`` (which VIs/classes belong to which ``.lvproj``).
    Example — the names this project uses for error indicators, as a histogram
    rather than 379 raw rows::

        SELECT name, COUNT(*) AS n FROM terminal
        WHERE is_error_cluster = 1 AND direction = 'output'
        GROUP BY name ORDER BY n DESC

    Returns ``{columns, rows, row_count, truncated}`` (columnar). Only a single
    SELECT/CTE is allowed — writes, ``PRAGMA``, ``ATTACH`` and stacked statements
    are refused. The index is built/refreshed automatically on first use.
    ``project`` defaults to the client's workspace root. Transitive questions
    (callers, blast radius) are the ``get_callers``/``blast_radius`` tools, not
    SQL.
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


@mcp.tool()
async def get_callers(
    vi: str, project: str | None = None, ctx: Context | None = None,
) -> list[str]:
    """Paths of VIs that call ``vi`` — pure call edges (a method's owning class
    is never counted as a caller). ``vi`` may be a path, a qualified name, or an
    unambiguous bare name. ``project`` defaults to the client's workspace root."""
    project = await _resolve_project(project, ctx)

    def _work() -> list[str]:
        _, facts = _get_index(project)
        return iq.get_callers(facts, vi)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def get_callees(
    vi: str, project: str | None = None, ctx: Context | None = None,
) -> list[str]:
    """Paths of VIs that ``vi`` calls — pure call edges. ``vi`` may be a path, a
    qualified name, or an unambiguous bare name. ``project`` defaults to the
    client's workspace root."""
    project = await _resolve_project(project, ctx)

    def _work() -> list[str]:
        _, facts = _get_index(project)
        return iq.get_callees(facts, vi)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def blast_radius(
    vi: str, project: str | None = None, depth: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """"What breaks if I change ``vi``?" — its transitive dependents over the
    pure call graph, optionally bounded to ``depth`` hops. Returns the resolved
    key, the dependent VI paths, and ``impact_score`` (their count). ``project``
    defaults to the client's workspace root."""
    project = await _resolve_project(project, ctx)

    def _work() -> dict[str, Any]:
        _, facts = _get_index(project)
        return asdict(iq.blast_radius(facts, vi, depth=depth))

    return await asyncio.to_thread(_work)


@mcp.tool()
async def visualize_project(
    project: str | None = None, scope: str = "calls",
    highlight: str | None = None, ctx: Context | None = None,
) -> str:
    """A self-contained **Mermaid** map of the project (paste into any Mermaid
    renderer). ``scope="calls"`` draws the pure call graph; ``scope="classes"``
    draws the class-inheritance tree. ``highlight`` (a VI path/qualified/bare
    name) marks that VI and its blast-radius dependents — the visual twin of
    ``blast_radius``. Clean-room: emits only Mermaid text, no external hosts.
    ``project`` defaults to the client's workspace root."""
    project = await _resolve_project(project, ctx)

    def _work() -> str:
        _, facts = _get_index(project)
        return _mermaid(facts, scope=scope, highlight=highlight)

    return await asyncio.to_thread(_work)


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
    VI (loaded on demand). Start here before ``get_operations``/``get_dataflow``.
    ``vi_path`` may be relative to the client's workspace root.
    """
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        return describe_vi_text(graph, vi_name)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def get_operations(vi_path: str, ctx: Context | None = None) -> str:
    """Execution-ordered operations of one VI, with nested structures (case
    frames, loop bodies), loaded on demand. ``vi_path`` may be relative to the
    client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        return describe_operations_text(graph, vi_name)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def get_dataflow(
    vi_path: str, operation_id: str | None = None, ctx: Context | None = None,
) -> str:
    """Wire connections between one VI's operations, optionally filtered to a
    single operation. Loaded on demand. ``vi_path`` may be relative to the
    client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        return describe_dataflow_text(graph, vi_name, operation_id)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def get_structure(
    vi_path: str, operation_id: str, ctx: Context | None = None,
) -> str:
    """Detail on one case/loop/sequence structure — selector and values,
    tunnels, frame contents. Loaded on demand. ``vi_path`` may be relative to
    the client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        return describe_structure_text(graph, vi_name, operation_id)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def get_constants(vi_path: str, ctx: Context | None = None) -> str:
    """Every constant's name, type, and value in one VI (loaded on demand).
    ``vi_path`` may be relative to the client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        return describe_constants_text(graph, vi_name)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def get_context(vi_path: str, ctx: Context | None = None) -> str:
    """Full structured context for one VI — inputs, outputs, operations, wires,
    constants — as JSON. Loaded on demand. Heavier than ``describe``; use when
    you need the raw structure, not prose. ``vi_path`` may be relative to the
    client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        context = graph.get_vi_context(vi_name)
        return json.dumps(context.model_dump(), indent=2, default=str)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def generate_ast_code(vi_path: str, ctx: Context | None = None) -> str:
    """Generate Python for one VI via the deterministic AST pipeline (loaded on
    demand). Always valid syntax; may contain PRIMITIVE_xxx stubs for unknown
    primitives. ``vi_path`` may be relative to the client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)

    def _work() -> str:
        graph, vi_name = _load_one(vi_path)
        context = graph.get_vi_context(vi_name)
        return build_module(context, vi_name)

    return await asyncio.to_thread(_work)


# ===== Stateless generators =====

@mcp.tool()
async def generate_documents(
    library_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    load_mode: str = "full",
    vilib_root: str | None = None,
    userlib_root: str | None = None,
    auto_vilib: bool = True,
    ctx: Context | None = None,
) -> str:
    """Generate a static HTML documentation site for a VI, library, class, or
    directory (same output as ``lvkit docs``). Writes files and returns a
    summary; tell the user the path to ``index.html``. ``library_path`` may be
    relative to the client's workspace root."""
    library_path = await _resolve_target(library_path, ctx)
    _configure_resolvers_for_vi(library_path)
    return await asyncio.to_thread(
        _gen_documents, library_path, output_dir, search_paths or [], load_mode,
        vilib_root=vilib_root, userlib_root=userlib_root, auto_vilib=auto_vilib,
    )


@mcp.tool()
async def generate_python(
    vi_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    soft_unresolved: bool = False,
    vilib_root: str | None = None,
    userlib_root: str | None = None,
    auto_vilib: bool = True,
    ctx: Context | None = None,
) -> str:
    """Generate a Python package from a VI (same conversion as ``lvkit
    generate``), with a ``needs_review``/``errors`` workflow for the calling
    agent to read and correct the output. Returns JSON. ``vi_path`` may be
    relative to the client's workspace root."""
    vi_path = await _resolve_target(vi_path, ctx)
    _configure_resolvers_for_vi(vi_path)
    result = await asyncio.to_thread(
        _gen_python, vi_path, output_dir, search_paths or [],
        include_code=False, soft_unresolved=soft_unresolved,
        vilib_root=vilib_root, userlib_root=userlib_root, auto_vilib=auto_vilib,
    )
    return result.model_dump_json(indent=2)


# ===== Mermaid rendering (project visualization) =====

def _mermaid_id(path: str, ids: dict[str, str]) -> str:
    """Stable, Mermaid-safe node id for a path (n0, n1, …)."""
    if path not in ids:
        ids[path] = f"n{len(ids)}"
    return ids[path]


def _mermaid(vis: list[VIFacts], *, scope: str, highlight: str | None) -> str:
    """Render the project as a Mermaid ``graph``/``classDiagram`` string."""
    by_path = {f.path: f for f in vis}
    label = {f.path: f.name for f in vis}

    if scope == "classes":
        lines = ["classDiagram"]
        seen: set[str] = set()
        for f in vis:
            cf = f.class_fact
            if cf is None:
                continue
            cls = cf.owning_class
            if cls not in seen:
                lines.append(f"    class `{cls}`")
                seen.add(cls)
            if cf.parent and cf.parent not in seen:
                lines.append(f"    class `{cf.parent}`")
                seen.add(cf.parent)
            if cf.parent:
                lines.append(f"    `{cf.parent}` <|-- `{cls}`")
        if len(lines) == 1:
            lines.append("    class `(no classes indexed)`")
        return "\n".join(lines)

    # scope == "calls" (default): the pure call graph.
    graph = iq.build_call_graph(vis)
    hot: set[str] = set()
    if highlight is not None:
        br = iq.blast_radius(vis, highlight)
        hot = {br.vi_key, *br.dependents}

    ids: dict[str, str] = {}
    lines = ["graph LR"]
    for path in graph.nodes():
        nid = _mermaid_id(path, ids)
        name = label.get(path, Path(path).name).replace('"', "'")
        lines.append(f'    {nid}["{name}"]')
    for a, b in graph.edges():
        lines.append(f"    {_mermaid_id(a, ids)} --> {_mermaid_id(b, ids)}")
    for path in hot:
        if path in by_path:
            lines.append(f"    style {_mermaid_id(path, ids)} fill:#f9a,stroke:#c33")
    return "\n".join(lines)


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


def main() -> None:
    """Run the MCP server over stdio (entry point)."""
    mcp.run("stdio")


if __name__ == "__main__":
    main()
