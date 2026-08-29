"""Index (project-scoped) tools: build/refresh the facts index and answer
project-wide questions over it — ``list_projects``, ``index``, ``query``,
``query_schema``.

.. note::
   ``_resolve_project``/``_default_roots`` live in the package facade
   (``lvkit.mcp.server.__init__``), not a lower-level module: they read/write
   the ``_DEFAULT_ROOTS`` global that tests monkeypatch directly on the
   ``lvkit.mcp.server`` module object (see the facade's module docstring for
   why). This module reaches them via ``import lvkit.mcp.server as _facade``
   (an absolute self-referential import — ``lvkit.mcp.server`` is already in
   ``sys.modules`` by the time this submodule is imported, since the facade
   imports this module as part of its own initialization) and defers the
   attribute lookup to call time, inside the tool bodies below, so no import
   ordering between the facade and this module is required.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import lvkit.mcp.server as _facade

from ...index import sql as isql
from ...index.build import (
    build_index,
    build_lvproj_membership,
    refresh_index,
)
from ...index.model import VIFacts
from ...index.project import resolve_project
from ...index.store import db_path as store_db_path
from ...index.store import delete as store_delete
from ...index.store import load as store_load
from ...index.store import save as store_save
from ...index.store import save_lvproj_members as store_save_lvproj_members
from ._compat import Context
from .app import mcp
from .resolvers import _configure_resolvers_for_vi
from .roots import _client_roots

# Per-project-root facts cache: {resolved project_root str -> [VIFacts]}. This
# replaces the old module-global _graph — different repos get different entries,
# so parallel agents on different projects never collide, and there is no
# session-wide `clear` that wipes another caller's state.
_indexes: dict[str, list[VIFacts]] = {}


def _require_vis(root: Path, vi_paths: list[Path]) -> None:
    """Raise a caller-actionable message when the resolved root holds no ``.vi``
    files — the "nothing to search yet" case (notably Claude Desktop, whose cwd
    isn't your files). The model/user then passes ``project=<a path to your VIs>``
    once, which Claude's memory can retain across conversations; an IDE client
    (Claude Code / VS Code) resolves it from the open workspace (cwd) for free."""
    if not vi_paths:
        raise ValueError(
            f"No .vi files found under {root}. Point me at your LabVIEW files — a "
            "folder, or even a single .vi/.lvproj — via project=<path> (an "
            "absolute path, or one the client has open)."
        )


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
    _require_vis(root, vi_paths)
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
    (``lvkit mcp <dir>...`` CLI dirs / env / cwd). Pass one as a
    tool's ``project=`` to scope a call — when exactly one root is active it is
    used automatically, so you only need this when several are configured."""
    client = [str(r) for r in await _client_roots(ctx)]
    return {
        "client_roots": client,
        "configured_roots": _facade._default_roots(),
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
    project = await _facade._resolve_project(project, ctx)

    def _work() -> dict[str, Any]:
        start = time.monotonic()
        root, vi_paths = resolve_project(Path(project))
        _require_vis(root, vi_paths)
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
    project = await _facade._resolve_project(project, ctx)

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


__all__ = [
    "_indexes",
    "_require_vis",
    "_get_index",
    "list_projects",
    "index",
    "query",
    "query_schema",
]
