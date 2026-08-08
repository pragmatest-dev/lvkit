"""MCP server for VI analysis — project-scoped, index-backed.

Built on ``mcp.server.fastmcp.FastMCP`` (the 2.x-forward decorator API), which
supersedes the module-global ``mcp.server.Server`` + ``@app.list_tools()`` build
this module used to have (that decorator was removed in mcp 2.0, silently
disabling the server — see ``docs/_internal/design/lvkit-mcp-improvements.md``).

Three tool groups:

1. **Index-backed, project-scoped** (``index``, ``find_terminals``,
   ``find_constants``, ``find_type_usages``, ``find_symbols``, ``get_callers``,
   ``get_callees``, ``blast_radius``, ``get_signatures``, ``visualize_project``)
   — answer *project-wide* questions in one call from the persisted, path-keyed
   facts index (``lvkit.index``). No per-VI round trips, no name-collision bug.
   State is a per-project-root cache, NOT a single global graph any ``clear``
   could nuke — safe for an agent working across several repos in one session.

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
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context, FastMCP

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
from ..index.build import build_index, refresh_index
from ..index.model import VIFacts
from ..index.project import resolve_project
from ..index.store import delete as store_delete
from ..index.store import load as store_load
from ..index.store import save as store_save
from ..load_mode import LoadMode
from ..project_store import find_project_store
from .tools import generate_documents as _gen_documents
from .tools import generate_python as _gen_python

mcp = FastMCP("lvkit-mcp")

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

def _uri_to_path(uri: str) -> Path:
    """Convert a ``file://`` URI (as sent in MCP roots) to a local path.

    Handles POSIX (``file:///home/x``) and Windows (``file:///C:/Users/x``,
    which arrives as ``/C:/Users/x``) forms.
    """
    path = unquote(urlparse(uri).path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]  # Windows drive path: '/C:/...' -> 'C:/...'
    return Path(path)


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
    """Resolve a project path: explicit ``project`` > first client root > cwd."""
    if project:
        return project
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
    """
    root, vi_paths = resolve_project(Path(project))
    key = str(root)
    if rebuild or key not in _indexes:
        _configure_resolvers_for_vi(root)
        facts = [] if rebuild else store_load(root)
        if not facts:
            result = build_index(root, vi_paths)
            store_save(root, result.facts)
            facts = result.facts
        _indexes[key] = facts
    return root, _indexes[key]


def _vi_summary(f: VIFacts) -> dict[str, Any]:
    """A compact VI record for symbol/navigation results (no terminal dump)."""
    return {
        "path": f.path,
        "name": f.name,
        "qualified_name": f.qualified_name,
        "library": f.library,
        "owning_class": f.class_fact.owning_class if f.class_fact else None,
        "is_stub": f.is_stub,
        "impact_score": f.impact_score,
    }


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
        _indexes[str(root)] = result.facts
        return {
            "project_root": str(root),
            "vis": len(result.facts),
            "collisions": result.collisions,
            "ms": round((time.monotonic() - start) * 1000),
        }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def find_terminals(
    project: str | None = None,
    direction: str | None = None,
    is_error_cluster: bool | None = None,
    py_type: str | None = None,
    name: str | None = None,
    ctx: Context | None = None,
) -> list[dict[str, Any]]:
    """Find connector-pane terminals (controls/indicators) across every VI.

    On a connector pane an **indicator is an OUTPUT** terminal, so
    ``direction="output"`` selects indicators and ``direction="input"`` selects
    controls. Combine ``direction="output"`` with ``is_error_cluster=True`` for
    the canonical "what names does this project use for error indicators?"
    question — then tally the returned ``name`` values. Each result carries its
    VI (``vi_path``/``vi_name``) plus the terminal's fields. ``project`` defaults
    to the client's workspace root.
    """
    project = await _resolve_project(project, ctx)

    def _work() -> list[dict[str, Any]]:
        _, facts = _get_index(project)
        matches = iq.find_terminals(
            facts, direction=direction, is_error_cluster=is_error_cluster,
            py_type=py_type, name=name,
        )
        return [asdict(m) for m in matches]

    return await asyncio.to_thread(_work)


@mcp.tool()
async def find_constants(
    project: str | None = None, wired_to: str | None = None,
    ctx: Context | None = None,
) -> list[dict[str, Any]]:
    """Find block-diagram constants across every VI, by what their wire feeds.

    ``wired_to`` is one of ``indicator`` / ``control`` / ``other`` / ``unwired``
    (precomputed at index time). ``wired_to="indicator"`` answers "every constant
    wired directly to an indicator" without a per-VI wire trace. ``project``
    defaults to the client's workspace root.
    """
    project = await _resolve_project(project, ctx)

    def _work() -> list[dict[str, Any]]:
        _, facts = _get_index(project)
        return [asdict(m) for m in iq.find_constants(facts, wired_to=wired_to)]

    return await asyncio.to_thread(_work)


@mcp.tool()
async def find_type_usages(
    type_key: str, project: str | None = None, ctx: Context | None = None,
) -> list[str]:
    """Paths of every VI whose terminals reference ``type_key`` (a classname or
    typedef name) — the reverse type-usage index ("who uses this typedef?").
    ``project`` defaults to the client's workspace root."""
    project = await _resolve_project(project, ctx)

    def _work() -> list[str]:
        _, facts = _get_index(project)
        return iq.find_type_usages(facts, type_key)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def find_symbols(
    project: str | None = None, name: str | None = None,
    owning_class: str | None = None, ctx: Context | None = None,
) -> list[dict[str, Any]]:
    """Workspace symbol search: VIs whose bare name contains ``name``
    (case-insensitive) and/or that belong to ``owning_class``. Each result is a
    compact record (path, qualified name, library, owning class, impact score).
    ``project`` defaults to the client's workspace root.
    """
    project = await _resolve_project(project, ctx)

    def _work() -> list[dict[str, Any]]:
        _, facts = _get_index(project)
        return [
            _vi_summary(f)
            for f in iq.find_symbols(facts, name=name, owning_class=owning_class)
        ]

    return await asyncio.to_thread(_work)


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
async def get_signatures(
    project: str | None = None, vi_names: list[str] | None = None,
    ctx: Context | None = None,
) -> list[dict[str, Any]]:
    """Connector panes of every indexed VI (or just ``vi_names``) in one call —
    each terminal summarized (name, direction, type, cluster field names). The
    bulk read for classifying terminals project-wide without a round trip per VI.
    ``project`` defaults to the client's workspace root.
    """
    project = await _resolve_project(project, ctx)
    wanted = set(vi_names or [])

    def _work() -> list[dict[str, Any]]:
        _, facts = _get_index(project)
        out: list[dict[str, Any]] = []
        for f in facts:
            if wanted and f.name not in wanted and f.path not in wanted:
                continue
            out.append({
                "vi_path": f.path,
                "vi_name": f.name,
                "qualified_name": f.qualified_name,
                "terminals": [
                    {
                        "name": t.name,
                        "direction": t.direction,
                        "py_type": t.py_type,
                        "is_error_cluster": t.is_error_cluster,
                        "field_names": t.field_names,
                    }
                    for t in f.terminals
                ],
            })
        return out

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
    for vi_name in graph.list_vis():
        src = graph.get_vi_source_path(vi_name)
        if src is not None and src.resolve() == p:
            return graph, vi_name
    # Fall back to the leaf-name resolver (single-VI graphs usually have one)
    return graph, graph.resolve_vi_name(p.name)


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
