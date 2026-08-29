"""Deep single-VI tools (loaded on demand): ``read_vi``, ``render``, ``diff``,
``unresolved``.

.. note::
   ``_resolve_target`` lives in the package facade
   (``lvkit.mcp.server.__init__``), not a lower-level module: it reads the
   ``_DEFAULT_ROOTS`` global that tests monkeypatch directly on the
   ``lvkit.mcp.server`` module object (see the facade's module docstring for
   why). This module reaches it via ``import lvkit.mcp.server as _facade``
   (an absolute self-referential import — ``lvkit.mcp.server`` is already in
   ``sys.modules`` by the time this submodule is imported, since the facade
   imports this module as part of its own initialization) and defers the
   attribute lookup to call time, inside the tool bodies below, so no import
   ordering between the facade and this module is required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import lvkit.mcp.server as _facade

from ... import __version__
from ...graph import InMemoryVIGraph, load_vi_by_path
from ...graph.netlist import build_netlist, netlist_to_dict
from ...index.build import warm_all_loaded
from ...load_mode import LoadMode
from ...output_cache import (
    cached_diff,
    cached_render,
    diff_options_tag,
    diff_slot,
    render_options_tag,
    render_slot,
)
from ._compat import Context
from .app import mcp
from .resolvers import _configure_resolvers_for_vi


def _load_one(
    vi_path: str, search_paths: list[str] | None = None
) -> tuple[InMemoryVIGraph, str]:
    """Load ONE VI (MINIMAL) into a fresh graph and return ``(graph, vi_name)``.

    A MINIMAL load also leaf-loads direct SubVIs, so ``list_vis()`` may hold
    several names; ``vi_name`` is ``load_vi``'s OWN return key for ``vi_path``
    (see ``load_vi_by_path``), never re-derived from the bare filename.

    ``search_paths`` are extra dependency-resolution roots (an out-of-tree
    library the VI calls into) — searched IN ADDITION to the VI's own directory,
    which is always included.
    """
    p = Path(vi_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"VI not found: {vi_path}")
    _configure_resolvers_for_vi(p)
    roots = [p.parent, *(Path(s).resolve() for s in (search_paths or []))]
    # Path IS a VI's identity: load_vi_by_path returns load_vi's OWN key for
    # the exact file requested, never re-derived from p.name (which would
    # collide across two same-named VIs -- routine under LabVIEW dynamic
    # dispatch, where every class's override of a method is literally
    # "run.vi").
    graph, vi_name = load_vi_by_path(p, LoadMode.MINIMAL, search_paths=roots)
    # Progressive index: every parse warms the store — a MINIMAL load parses
    # this VI AND its SubVIs, so warm all of them (accumulates as the repo is
    # used).
    warm_all_loaded(graph)
    return graph, vi_name


@mcp.tool()
async def read_vi(
    vi_path: str,
    search_paths: list[str] | None = None,
    format: str = "json",
    verbose: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """READ one VI in full — its structure as the canonical **netlist IR**
    ``{vi, inputs, outputs, components, body}``. This is the "read" to
    ``query``'s "grep": grep the ``node`` view to find WHICH VIs match a
    pattern, then ``read_vi`` a hit to see its actual wiring/dataflow. Loaded
    on demand.

    The IR is FAITHFUL structure, not an explanation — it is the raw material
    you INTERPRET. When you answer a person about this VI, do not stop at the
    netlist: state, in a sentence, WHAT THE VI DOES — its purpose — synthesized
    from the signature, the SubVI/primitive calls, and the control flow. A bare
    dump of operations is not an answer; the purpose is the answer, backed by
    the structure. (The ``.vi`` is read WITHOUT a LabVIEW license — never tell
    the user to open it in LabVIEW to figure out what it does.)

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
    relative to the client's workspace root. ``search_paths`` are extra
    dependency-resolution roots for an out-of-tree library the VI calls into
    (its own directory is always searched).

    ``format`` picks the surface: ``"json"`` (default, unchanged when
    ``verbose=False``) returns the structured IR dict above; ``verbose=True``
    additionally nests the ``uses :`` dependency manifest (each resolved
    subVI's own interface) and every terminal's structured type alongside
    its existing flattened type string -- the JSON counterpart of
    ``"lvnet"``'s verbose elements below (see ``netlist_to_dict``'s
    docstring). ``"lvnet"`` instead returns
    ``{"lvnet": <text>}`` — the same lvnet text surface as
    ``lvkit describe --format lvnet`` (see
    ``docs/_internal/design/netlist-language.md``): terse by default, or
    ``verbose=True`` to also inline each direct SubVI's connector-pane
    interface plus a trailing ``types :`` appendix (type-rehydratable)."""
    vi_path = await _facade._resolve_target(vi_path, ctx)

    def _work() -> dict[str, Any]:
        graph, vi_name = _load_one(vi_path, search_paths)
        if format == "lvnet":
            from ...graph.netlist import build_netlist_from_graph, render_lvnet

            module = build_netlist_from_graph(graph, vi_name)
            display_name = graph.vi_display_name(vi_name)
            return {
                "lvnet": render_lvnet(
                    module, display_name=display_name, verbose=verbose
                )
            }
        # verbose's `dependencies` + structured `lv_type` facts only exist
        # on `build_netlist_from_graph`'s module (the OLD `build_netlist`
        # never populates them) -- non-verbose stays on the OLD builder,
        # byte-identical to before.
        if verbose:
            from ...graph.netlist import build_netlist_from_graph

            return netlist_to_dict(
                build_netlist_from_graph(graph, vi_name), verbose=True
            )
        return netlist_to_dict(build_netlist(graph, vi_name))

    return await asyncio.to_thread(_work)


@mcp.tool()
async def render(
    vi_path: str,
    search_paths: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Render one VI's **block diagram** to a self-contained interactive
    **HTML viewer** and return its path — the faithful visual (node positions,
    wires, structures, constants) as it appears in LabVIEW, reconstructed from
    the ``.vi`` binary, in a zoom/pan page with a light/dark toggle. This is the
    tool for "show me / draw / what does this VI look like".

    Returns ``{render_path, bytes}``: ``render_path`` is a local ``.html`` file
    to open in a browser (same shape as ``diff``'s output). The markup is written
    to disk, NOT inlined — a diagram is large and would flood the context — so
    **relay the path; do NOT read the file back**. You cannot reconstruct this
    geometry from ``read_vi``; only lvkit can.

    lvkit renders and reads ``.vi`` files WITHOUT a LabVIEW license — this tool
    IS how the diagram is produced. NEVER tell the user to open the VI in
    LabVIEW, click a node in LabVIEW, or take a screenshot from LabVIEW; that is
    neither necessary nor available. ``vi_path`` may be relative to the client's
    workspace root; ``search_paths`` are extra dependency-resolution roots (the
    VI's own directory is always searched).
    """
    vi_path = await _facade._resolve_target(vi_path, ctx)

    def _work() -> dict[str, Any]:
        p = Path(vi_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"VI not found: {vi_path}")
        _configure_resolvers_for_vi(p)
        roots = [p.parent, *(Path(s).resolve() for s in (search_paths or []))]
        opts = render_options_tag("html", "auto", None)
        # Shared cached core: look up, and only on a miss build + refresh the slot.
        # "auto" theme so the viewer's live light/dark toggle can re-theme it.
        html = cached_render(
            p,
            fmt="html",
            options=opts,
            version=__version__,
            search_paths=roots,
            theme_mode="auto",
        )
        if html is None:
            raise RuntimeError(f"Could not render {p.name} (unresolvable diagram).")
        return {"render_path": str(render_slot(p, "html")), "bytes": len(html)}

    return await asyncio.to_thread(_work)


@mcp.tool()
async def diff(
    before_vi: str,
    after_vi: str,
    search_paths: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Diff two versions of a VI — write a **visual HTML diff** and return its
    path. Compares BEFORE (``before_vi``) to AFTER (``after_vi``), rendering both
    block diagrams with the changes highlighted — the faithful "what changed"
    that you cannot reconstruct from ``read_vi``.

    Returns ``{diff_path, bytes}``: ``diff_path`` is a local ``.html`` file to
    open in a browser. The markup is written, NOT inlined — it's large — so
    **relay the path; do NOT read the file back**.

    lvkit diffs ``.vi`` files WITHOUT a LabVIEW license — NEVER suggest opening
    either version in LabVIEW or comparing them by eye there; this tool IS the
    compare. Paths may be relative to the client's workspace root;
    ``search_paths`` are extra dependency-resolution roots (each VI's own
    directory is always searched).
    """
    before_vi = await _facade._resolve_target(before_vi, ctx)
    after_vi = await _facade._resolve_target(after_vi, ctx)

    def _work() -> dict[str, Any]:
        pa, pb = Path(before_vi).resolve(), Path(after_vi).resolve()
        for p in (pa, pb):
            if not p.exists():
                raise FileNotFoundError(f"VI not found: {p}")
        _configure_resolvers_for_vi(pa)
        roots = [
            pa.parent,
            pb.parent,
            *(Path(s).resolve() for s in (search_paths or [])),
        ]
        opts = diff_options_tag("html", False, None, None)
        body = cached_diff(
            pa,
            pb,
            fmt="html",
            options=opts,
            version=__version__,
            search_paths=roots,
        )
        if body is None:
            raise RuntimeError(
                f"Could not render diff for {pa.name} (unresolvable diagram)."
            )
        return {"diff_path": str(diff_slot(pa, pb, "html")), "bytes": len(body)}

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
    target = await _facade._resolve_target(target, ctx)
    _configure_resolvers_for_vi(target)

    def _work() -> list[dict[str, Any]]:
        from ...unresolved import collect_unresolved

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


__all__ = ["_load_one", "read_vi", "render", "diff", "unresolved"]
