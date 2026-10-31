"""Deep single-VI tools (loaded on demand): ``read_vi``, ``read_ctl``,
``render``, ``diff``, ``unresolved``.

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
from ...build_options import BuildOptions
from ...graph import InMemoryVIGraph, load_ctl_by_path, load_vi_by_path
from ...graph.lvnet_typedef import render_lvnet_typedef
from ...graph.netlist import build_netlist_from_graph, netlist_to_dict, render_lvnet
from ...graph.netlist_json import typedef_to_dict
from ...index.build import warm_all_loaded
from ...library_folders import folder_list
from ...load_mode import LoadMode
from ...output_cache import (
    cached_diff,
    cached_render,
    diff_options_tag,
    diff_slot,
    read_diff_identity,
    read_render_identity,
    render_options_tag,
    render_slot,
)
from ...run_report import ProblemStage, RunProblems, declined
from ._compat import Context
from .app import mcp
from .problems import new_run, stopped_on, with_problems
from .resolvers import _configure_resolvers_for_vi

_FORMATS = ("json", "lvnet")


def _check_format(format: str) -> None:
    """Reject a ``format`` other than the two the read tools return."""
    if format not in _FORMATS:
        raise ValueError(f"Unknown format {format!r}; expected one of {_FORMATS}")


def _build(
    vilib: str | list[str] | None,
    userlib: str | list[str] | None,
    instrlib: str | list[str] | None,
    search_path: list[str] | None = None,
    store: Path | None = None,
) -> BuildOptions:
    """The build options of one tool call: the folders each pseudo path names
    (each resolved, in order), the VI Search Path and the project store."""

    def _roots(folders: str | list[str] | None) -> tuple[Path, ...]:
        return tuple(p.resolve() for p in folder_list(folders))

    return BuildOptions(
        vilib_root=_roots(vilib),
        userlib_root=_roots(userlib),
        instrlib_root=_roots(instrlib),
        search_path=tuple(search_path) if search_path else None,
        project_store=store,
    )


def _load_one(
    vi_path: str,
    build: BuildOptions | None = None,
    run: RunProblems | None = None,
) -> tuple[InMemoryVIGraph, str]:
    """Load ONE VI (MINIMAL) into a fresh graph and return ``(graph, vi_name)``.

    A MINIMAL load also leaf-loads direct SubVIs, so ``list_vis()`` may hold
    several names; ``vi_name`` is ``load_vi``'s OWN return key for ``vi_path``
    (see ``load_vi_by_path``), never re-derived from the bare filename.
    ``build`` carries the library roots its recorded dependency paths resolve
    against. The problems on the loaded VIs' nodes are collected into ``run``.
    """
    p = Path(vi_path).resolve()
    run = run or new_run(p)
    if not p.exists():
        raise FileNotFoundError(f"VI not found: {vi_path}")
    _configure_resolvers_for_vi(p)
    build = build or BuildOptions()
    # Path IS a VI's identity: load_vi_by_path returns load_vi's OWN key for
    # the exact file requested, never re-derived from p.name (which would
    # collide across two same-named VIs -- routine under LabVIEW dynamic
    # dispatch, where every class's override of a method is literally
    # "run.vi").
    graph, vi_name = load_vi_by_path(
        p,
        LoadMode.MINIMAL,
        vilib_root=build.vilib_root,
        userlib_root=build.userlib_root,
        instrlib_root=build.instrlib_root,
        search_path=build.search_path,
        graph=run.new_graph(),
    )
    # Progressive index: every parse warms the store — a MINIMAL load parses
    # this VI AND its SubVIs, so warm all of them (accumulates as the repo is
    # used). Warming's own failures are problems on the VIs' nodes too.
    warm_all_loaded(graph)
    return graph, vi_name


@mcp.tool()
async def read_vi(
    vi_path: str,
    vilib: str | list[str] | None = None,
    userlib: str | list[str] | None = None,
    instrlib: str | list[str] | None = None,
    search_path: list[str] | None = None,
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
    relative to the client's workspace root. ``vilib``/``userlib``/``instrlib`` are the
    folders a recorded ``<vilib>``/``<userlib>``/``<instrlib>`` dependency path starts
    from (each dependency is found only at the path its caller records).

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
    _check_format(format)
    vi_path = await _facade._resolve_target(vi_path, ctx)

    def _work() -> dict[str, Any]:
        run = new_run(vi_path)
        try:
            build = _build(vilib, userlib, instrlib, search_path)
            graph, vi_name = _load_one(vi_path, build, run)
            module = build_netlist_from_graph(graph, vi_name)
        except FileNotFoundError:
            raise
        except Exception as exc:
            return stopped_on(run, vi_path, ProblemStage.DESCRIBE, exc)
        if format == "lvnet":
            return with_problems(
                {
                    "lvnet": render_lvnet(
                        module,
                        display_name=graph.vi_display_name(vi_name),
                        verbose=verbose,
                    )
                },
                run,
            )
        return with_problems(netlist_to_dict(module, verbose=verbose), run)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def read_ctl(
    ctl_path: str,
    vilib: str | list[str] | None = None,
    userlib: str | list[str] | None = None,
    instrlib: str | list[str] | None = None,
    search_path: list[str] | None = None,
    format: str = "json",
    verbose: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """READ one ``.ctl`` typedef / custom control in full — its structure as
    ``{typedef, path, kind, type, default, fields, uses, owned_by?}``. The
    ``read_vi`` of a control: ``kind`` is the control's own type family (``cluster``,
    ``enum``, ``ring``, ``primitive``, ``array``, …); a cluster's ``fields`` nest
    (name, LabVIEW ``type``, the recorded ``default`` — an enum/ring default is the
    item NAME — and an array's saved ``elements`` or an array-of-clusters' element
    ``fields``); ``uses`` lists the typedefs/classes it references. ``owned_by``
    appears only when the control was loaded with an owner. ``verbose=True``
    additionally nests every type's full structure (``lv_type`` / ``root_type``).
    ``format="lvnet"`` instead returns ``{"lvnet": <text>}`` — the lvnet ``typedef``
    document (the same text as ``lvkit describe --format lvnet``): its ``uses``, its
    ``type`` in the lossless type grammar, and each field with its ``default``;
    ``verbose=True`` adds the ``types :`` footnote of the named types its fields reach.

    The ``.ctl`` is read WITHOUT a LabVIEW license — never tell the user to open
    it in LabVIEW. To SEE the control, call ``render`` on the same path. It does not
    report which VIs use the control (a lone control has no callers loaded); ask
    ``query`` for project-wide type use. ``ctl_path`` may be relative to the client's
    workspace root. ``vilib``/``userlib``/``instrlib`` are the folders a recorded
    ``<vilib>``/``<userlib>``/``<instrlib>`` dependency path starts from (each
    dependency is found only at the path its caller records)."""
    _check_format(format)
    ctl_path = await _facade._resolve_target(ctl_path, ctx)

    def _work() -> dict[str, Any]:
        p = Path(ctl_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Control not found: {ctl_path}")
        _configure_resolvers_for_vi(p)
        run = new_run(p)
        build = _build(vilib, userlib, instrlib, search_path)
        try:
            graph, key = load_ctl_by_path(
                p,
                graph=run.new_graph(),
                vilib_root=build.vilib_root,
                userlib_root=build.userlib_root,
                instrlib_root=build.instrlib_root,
                search_path=build.search_path,
            )
            info = graph.get_typedef(key)
        except Exception as exc:
            return stopped_on(run, p, ProblemStage.DESCRIBE, exc)
        if format == "lvnet":
            return with_problems(
                {"lvnet": render_lvnet_typedef(info, verbose=verbose)}, run
            )
        return with_problems(typedef_to_dict(info, verbose=verbose), run)

    return await asyncio.to_thread(_work)


@mcp.tool()
async def render(
    vi_path: str,
    vilib: str | list[str] | None = None,
    userlib: str | list[str] | None = None,
    instrlib: str | list[str] | None = None,
    search_path: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Render one VI's **block diagram** — or a ``.ctl`` control's **front
    panel** — to a self-contained interactive **HTML viewer** and return its path:
    the faithful visual (node positions, wires, structures, constants; or a
    control's nested clusters, arrays and saved values) as it appears in LabVIEW,
    reconstructed from the ``.vi`` / ``.ctl`` binary, in a zoom/pan page with a
    light/dark toggle. This is the tool for "show me / draw / what does this look
    like".

    Returns ``{render_path, bytes, vi_name, vi_path, status, problems}``:
    ``render_path`` is a
    local ``.html`` file to open in a browser (same shape as ``diff``'s
    output); ``vi_name`` (the VI's qualified name, e.g. ``Class.lvclass:vi.vi``)
    and ``vi_path`` identify the INPUT VI this render is of (read these, not
    the request you sent, to tell results apart when several render/diff
    calls are in flight at once). The markup is written to disk,
    NOT inlined — a diagram is large and would flood the context — so
    **relay the path; do NOT read the file back**. You cannot reconstruct this
    geometry from ``read_vi``; only lvkit can.

    lvkit renders and reads ``.vi`` files WITHOUT a LabVIEW license — this tool
    IS how the diagram is produced. NEVER tell the user to open the VI in
    LabVIEW, click a node in LabVIEW, or take a screenshot from LabVIEW; that is
    neither necessary nor available. ``vi_path`` may be relative to the client's
    workspace root. ``vilib``/``userlib``/``instrlib`` are the folders a recorded
    ``<vilib>``/``<userlib>``/``<instrlib>`` dependency path starts from (each
    dependency is found only at the path its caller records).
    """
    vi_path = await _facade._resolve_target(vi_path, ctx)

    def _work() -> dict[str, Any]:
        p = Path(vi_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"File not found: {vi_path}")
        store = _configure_resolvers_for_vi(p)
        build = _build(vilib, userlib, instrlib, search_path, store)
        opts = render_options_tag("html", "auto", None, build)
        # Shared cached core: look up, and only on a miss build + refresh the slot.
        # "auto" theme so the viewer's live light/dark toggle can re-theme it.
        run = new_run(p)
        try:
            built = cached_render(
                p,
                fmt="html",
                options=opts,
                version=__version__,
                theme_mode="auto",
                run_settings=run.settings,
                **build.build_kwargs(),
            )
        except Exception as exc:
            return stopped_on(run, p, ProblemStage.RENDER, exc)
        if built is None:
            return stopped_on(run, p, ProblemStage.RENDER, declined(p))
        html = built.body
        run.take(built.problems, announce=False)
        # vi_name/vi_path identify the INPUT (#114: a tool result read apart
        # from the call that produced it -- several render/diff calls run
        # concurrently, not serialized -- had no way to say which VI it was
        # for). The qualified name (not a bare filename, which collides across
        # LabVIEW dynamic dispatch's many same-named "run.vi"s) comes from
        # read_render_identity's cache-meta read -- the MISS that just built
        # `html` above already had the graph in hand and stored its name
        # alongside the body, so this is a cheap metadata read, never a second
        # graph load, on either a hit or a miss. Only an old slot written
        # before this field existed falls back to the bare filename.
        name = read_render_identity(p, "html") or p.name
        return with_problems(
            {
                "render_path": str(render_slot(p, "html")),
                "bytes": len(html),
                "vi_name": name,
                "vi_path": str(p),
            },
            run,
        )

    return await asyncio.to_thread(_work)


@mcp.tool()
async def diff(
    before_vi: str,
    after_vi: str,
    vilib: str | list[str] | None = None,
    userlib: str | list[str] | None = None,
    instrlib: str | list[str] | None = None,
    search_path: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Diff two versions of a VI — write a **visual HTML diff** and return its
    path. Compares BEFORE (``before_vi``) to AFTER (``after_vi``), rendering both
    block diagrams with the changes highlighted — the faithful "what changed"
    that you cannot reconstruct from ``read_vi``.

    Returns ``{diff_path, bytes, before, after, status, problems}``:
    ``diff_path`` is a local
    ``.html`` file to open in a browser; ``before``/``after`` are each
    ``{name, path}``, identifying the INPUT pair this diff is of (read these,
    not the request you sent, to tell results apart when several diff/render
    calls are in flight at once). The markup is written, NOT inlined — it's
    large — so
    **relay the path; do NOT read the file back**.

    lvkit diffs ``.vi`` files WITHOUT a LabVIEW license — NEVER suggest opening
    either version in LabVIEW or comparing them by eye there; this tool IS the
    compare. Paths may be relative to the client's workspace root.
    ``vilib``/``userlib``/``instrlib`` are the folders a recorded
    ``<vilib>``/``<userlib>``/``<instrlib>`` dependency path starts from (each
    dependency is found only at the path its caller records).
    """
    before_vi = await _facade._resolve_target(before_vi, ctx)
    after_vi = await _facade._resolve_target(after_vi, ctx)

    def _work() -> dict[str, Any]:
        pa, pb = Path(before_vi).resolve(), Path(after_vi).resolve()
        for p in (pa, pb):
            if not p.exists():
                raise FileNotFoundError(f"VI not found: {p}")
        store = _configure_resolvers_for_vi(pa)
        build = _build(vilib, userlib, instrlib, search_path, store)
        opts = diff_options_tag("html", False, None, None, build)
        run = new_run(pb)
        try:
            built = cached_diff(
                pa,
                pb,
                fmt="html",
                options=opts,
                version=__version__,
                run_settings=run.settings,
                **build.build_kwargs(),
            )
        except Exception as exc:
            return stopped_on(run, pa, ProblemStage.DIFF, exc)
        if built is None:
            return stopped_on(run, pb, ProblemStage.DIFF, declined(pb))
        body = built.body
        run.take(built.problems, announce=False)
        # before/after identify the INPUT pair, same shape as --format json's
        # diff_to_dict (#114). The qualified names come from read_diff_identity
        # -- a cache-meta read, never a graph load -- same rationale as
        # render's vi_name above. Falls back to the bare filename only for an
        # old slot written before this field existed.
        names = read_diff_identity(pa, pb, "html")
        before_name, after_name = names if names is not None else (pa.name, pb.name)
        return with_problems(
            {
                "diff_path": str(diff_slot(pa, pb, "html")),
                "bytes": len(body),
                "before": {"name": before_name, "path": str(pa)},
                "after": {"name": after_name, "path": str(pb)},
            },
            run,
        )

    return await asyncio.to_thread(_work)


@mcp.tool()
async def unresolved(
    target: str,
    vilib: str | list[str] | None = None,
    userlib: str | list[str] | None = None,
    instrlib: str | list[str] | None = None,
    search_path: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Every gap under ``target`` (a VI, library, class, or directory): what
    ``lvkit generate`` cannot convert, the same list as its warnings, collected
    in ONE pass. Use before converting a large library to triage the gaps up
    front. Returns ``{items, status, problems}``: ``items`` is a list of
    ``{kind, identifier, name, count, vi_names}`` (kind ∈
    ``unknown_primitive``/``placeholder_primitive``/``unsupported_node``/
    ``unmapped_vilib``/``terminal_mapping``/``unknown_default``/``missing_vi``/
    ``unsupported_vi``); an empty list means no gaps. ``problems`` lists the VIs
    that could not be read or written (they are not gaps). ``target`` may be
    relative to the client's workspace root. ``vilib``/``userlib``/``instrlib``
    are the folders a recorded ``<vilib>``/``<userlib>``/``<instrlib>``
    dependency path starts from (each one folder or several, tried in order);
    ``search_path`` is the VI search path a file not at its recorded path is
    looked for on."""
    target = await _facade._resolve_target(target, ctx)
    _configure_resolvers_for_vi(target)

    def _work() -> dict[str, Any]:
        from ...unresolved import collect_unresolved

        run = new_run(target)
        build = _build(vilib, userlib, instrlib, search_path)
        try:
            items = collect_unresolved(
                target,
                vilib_root=build.vilib_root,
                userlib_root=build.userlib_root,
                instrlib_root=build.instrlib_root,
                search_path=build.search_path,
                run=run,
            )
        except Exception as exc:
            return stopped_on(run, target, ProblemStage.GENERATE, exc)
        return with_problems(
            {
                "items": [
                    {
                        "kind": it.kind,
                        "identifier": it.identifier,
                        "name": it.name,
                        "count": it.count,
                        "vi_names": it.vi_names,
                    }
                    for it in items
                ]
            },
            run,
        )

    return await asyncio.to_thread(_work)


__all__ = ["_load_one", "read_vi", "render", "diff", "unresolved"]
