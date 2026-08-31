"""MCP server for VI analysis — project-scoped, index-backed.

This is a FACADE package: the original single-file ``lvkit/mcp/server.py``
was split by responsibility into this ``lvkit/mcp/server/`` package
(``app.py`` — the server instance + instructions; ``roots.py`` — stateless
Windows/WSL path + client-roots helpers; ``resolvers.py`` — primitive/vilib
resolver configuration; ``index_tools.py`` — the index-backed project-scoped
tools; ``vi_tools.py`` — the deep single-VI tools; ``_compat.py`` — the mcp
SDK version shim). Every name importable from ``lvkit.mcp.server`` before the
split remains importable from this exact path — this module re-exports all
of them, so ``from lvkit.mcp.server import X`` keeps working with zero edits
at any call site.

One piece of state resisted the split: ``_DEFAULT_ROOTS`` (the configured
default project roots — set by ``main()`` via ``global _DEFAULT_ROOTS`` and
read by ``_default_roots()``) is mutated directly by test monkeypatches on
the ``lvkit.mcp.server`` module object
(``monkeypatch.setattr(mcp_server, "_DEFAULT_ROOTS", ...)`` in
``tests/test_mcp_roots.py``). A module-level global and its reader/``global``
-writer must share one ``__globals__`` dict to see the same mutations, so
``_DEFAULT_ROOTS``, ``_default_roots()``, ``_resolve_project()``,
``_resolve_target()``, and ``main()`` all stay defined directly in this
facade rather than moving to ``roots.py`` — only the pure, stateless helpers
(``_win_to_wsl_path``, ``_uri_to_path``, ``_client_roots``, the two regexes)
moved out. ``index_tools.py``/``vi_tools.py`` reach ``_resolve_project``/
``_resolve_target``/``_default_roots`` back through
``import lvkit.mcp.server as _facade`` (a self-referential absolute import
that resolves immediately via ``sys.modules`` since this package is already
mid-import when those submodules load) with the attribute access deferred to
call time inside the tool bodies, so no fragile definition-before-import
ordering is required.

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

Artifact generation (Python packages, HTML docs, pyvis graphs, diffs) is
CLI-only (``lvkit generate``/``docs``/``visualize``): it writes files
and belongs in scripts/CI. The ONE exception is ``render`` — a VI's
block-diagram SVG, which an AI CANNOT reconstruct from the netlist (only lvkit
has the geometry from the ``.vi`` binary), so it's an MCP tool that writes the
SVG artifact and returns its **path** (the markup is large — written, not
inlined into context). Everything else stays a pure in-process read — no
subprocess or non-packaged-``scripts/`` dependency.
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

from ... import __version__, primitive_resolver, vilib_resolver
from ...cache_paths import _project_root_for
from ...graph import InMemoryVIGraph, load_vi_by_path
from ...graph.netlist import netlist_to_dict
from ...index import sql as isql
from ...index.build import (
    build_index,
    build_lvproj_membership,
    refresh_index,
    warm_all_loaded,
)
from ...index.model import VIFacts
from ...index.project import resolve_project
from ...index.store import db_path as store_db_path
from ...index.store import delete as store_delete
from ...index.store import load as store_load
from ...index.store import save as store_save
from ...index.store import save_lvproj_members as store_save_lvproj_members
from ...load_mode import LoadMode
from ...output_cache import (
    cached_diff,
    cached_render,
    diff_options_tag,
    diff_slot,
    render_options_tag,
    render_slot,
)
from ...project_store import find_project_store
from ._compat import Context, _MCPServer
from .app import _INSTRUCTIONS, mcp
from .roots import _WIN_DRIVE, _WSL_UNC, _client_roots, _uri_to_path, _win_to_wsl_path

# ===== Workspace-root defaulting (stateful) =====
#
# Default search roots for a client that sends NO usable root (notably Claude
# Desktop, whose cwd isn't your files). Set from the positional dirs of
# ``lvkit mcp <dir>...`` — a CLI / automation affordance for pointing at one or
# more source roots. Empty until ``main`` populates it; the ``.mcpb`` ships no
# config, so Desktop leans on cwd + the one-time ask below + Claude's memory.
_DEFAULT_ROOTS: list[str] = []


def _default_roots() -> list[str]:
    """Fallback search roots for a client that sends NO usable root — notably
    Claude Desktop, whose cwd isn't your VIs. In order: the folders passed to
    ``lvkit mcp <dir>...`` (a CLI/automation affordance), else the legacy single
    ``LVKIT_PROJECT_ROOT`` env, else cwd. A client that sends a workspace root
    (Claude Code / VS Code — cwd IS the repo) takes precedence over these (see
    ``_resolve_project``/``_resolve_target``)."""
    if _DEFAULT_ROOTS:
        return list(_DEFAULT_ROOTS)
    env = os.environ.get("LVKIT_PROJECT_ROOT")
    if env:
        return [env]
    # Pure cwd fallback: autodetect the enclosing source root (walk up for
    # .lvkit/.git/.lvproj), else cwd itself — so an IDE client whose cwd is a
    # subdir still scopes to the whole project, and a Desktop cwd with no markers
    # lands on cwd (where _require_vis then asks).
    cwd = Path.cwd()
    return [str(_project_root_for(cwd) or cwd)]


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
    # path: a configured search root (CLI `lvkit mcp <dir>`) / env / cwd).
    for d in _default_roots():
        default_root = Path(_win_to_wsl_path(d))
        if (default_root / target).exists():
            return str(default_root / target)
    return target


# These submodule imports run AFTER `_default_roots`/`_resolve_project`/
# `_resolve_target` are defined above (see the module docstring): they define
# the actual @mcp.tool() functions and reach back into this partially
# initialized facade via `import lvkit.mcp.server as _facade`, with the
# attribute access deferred to call time — so this ordering is a defensive
# nicety, not a strict requirement.
from .index_tools import (  # noqa: E402
    _get_index,
    _indexes,
    _require_vis,
    index,
    list_projects,
    query,
    query_schema,
)
from .resolvers import _configure_resolvers_for_vi  # noqa: E402
from .vi_tools import _load_one, diff, read_vi, render, unresolved  # noqa: E402

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
    args (a CLI / automation affordance) — used for
    clients that send no workspace roots (Claude Desktop). Empty/omitted falls
    back to the ``LVKIT_PROJECT_ROOT`` env, then cwd (see ``_default_roots``)."""
    global _DEFAULT_ROOTS
    if roots:
        _DEFAULT_ROOTS = [_win_to_wsl_path(r) for r in roots]
    mcp.run("stdio")


if __name__ == "__main__":
    main()


__all__ = [
    # stdlib / third-party re-exports (mechanical preservation of the
    # pre-split module's importable surface)
    "asyncio",
    "os",
    "re",
    "time",
    "asdict",
    "Path",
    "Any",
    "unquote",
    "urlparse",
    "Context",
    "_MCPServer",
    # lvkit re-exports
    "__version__",
    "primitive_resolver",
    "vilib_resolver",
    "_project_root_for",
    "InMemoryVIGraph",
    "load_vi_by_path",
    "netlist_to_dict",
    "isql",
    "build_index",
    "build_lvproj_membership",
    "refresh_index",
    "warm_all_loaded",
    "VIFacts",
    "resolve_project",
    "store_db_path",
    "store_delete",
    "store_load",
    "store_save",
    "store_save_lvproj_members",
    "LoadMode",
    "cached_diff",
    "cached_render",
    "diff_options_tag",
    "diff_slot",
    "render_options_tag",
    "render_slot",
    "find_project_store",
    # app instance + instructions
    "_INSTRUCTIONS",
    "mcp",
    # workspace-root defaulting
    "_WIN_DRIVE",
    "_WSL_UNC",
    "_win_to_wsl_path",
    "_uri_to_path",
    "_client_roots",
    "_DEFAULT_ROOTS",
    "_default_roots",
    "_resolve_project",
    "_resolve_target",
    # resolver configuration
    "_configure_resolvers_for_vi",
    # index (project-scoped) tools
    "_indexes",
    "_require_vis",
    "_get_index",
    "list_projects",
    "index",
    "query",
    "query_schema",
    # deep single-VI tools
    "_load_one",
    "read_vi",
    "render",
    "diff",
    "unresolved",
    # entry points
    "selftest",
    "main",
]
