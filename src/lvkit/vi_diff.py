"""Diff two VI versions into a body (``text`` | ``json`` | ``html``).

The shared core behind ``lvkit diff`` AND the MCP ``diff`` tool — both are thin
callers of this; the load+project logic lives here, never duplicated in a
surface. It ORCHESTRATES two layers: the diff ENGINE (``graph.diff`` — the
change set over two graphs, render-agnostic) and, for ``html``, the RENDERER
(``render_vi`` + ``build_diff_viewer``). It sits ABOVE both — which is why it
can't live in ``graph.diff``: the engine must never import render (that would be
an import cycle). ``text``/``json`` never touch the renderer.
"""

from __future__ import annotations

import json
from pathlib import Path

from .load_mode import LoadMode


def diff_vi_files(
    before_path: Path,
    after_path: Path,
    *,
    fmt: str = "html",
    verbose: bool = False,
    search_paths: list[Path] | None = None,
    before_ref: str | None = None,
    after_ref: str | None = None,
    mode: LoadMode = LoadMode.MINIMAL,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    warm_index: bool = True,
) -> str | None:
    """Diff two VI versions -> a body string in ``fmt``.

    ``before_path`` is the BEFORE side, ``after_path`` the AFTER. Loads both with
    one ``mode``/``search_paths``, then projects the UID-keyed change set:
    ``"text"`` (``format_diff``), ``"json"`` (``diff_to_dict``), or ``"html"``
    (the ``build_diff_viewer`` visual with both sides' faithful, theme-reactive
    SVGs). ``before_ref``/``after_ref`` are optional labels (e.g. git refs) for
    the html title. Returns ``None`` ONLY when an ``html`` render declines
    because required diagram geometry is missing. ``warm_index`` upserts both
    VIs' facts into their project index (best-effort; set ``False`` to skip)."""
    from .graph import load_vi_by_path
    from .graph.diff import (
        diff_to_dict,
        diff_uid,
        format_diff,
        netlist_diff_rows,
        rows_to_json,
    )

    layout = fmt != "text"

    # Path IS a VI's identity: load_vi_by_path returns load_vi's OWN key for
    # the exact file requested, never re-derived from the bare filename (which
    # would collide across two same-named VIs -- routine under LabVIEW
    # dynamic dispatch, where every class's override of a method is literally
    # "run.vi").
    graph_a, name_a = load_vi_by_path(
        before_path,
        mode,
        search_paths=search_paths,
        vilib_root=vilib_root,
        userlib_root=userlib_root,
        layout=layout,
    )
    graph_b, name_b = load_vi_by_path(
        after_path,
        mode,
        search_paths=search_paths,
        vilib_root=vilib_root,
        userlib_root=userlib_root,
        layout=layout,
    )

    if warm_index:
        from .index.build import warm_index_for_vi

        warm_index_for_vi(graph_a, name_a, before_path)
        warm_index_for_vi(graph_b, name_b, after_path)

    if fmt == "text":
        return format_diff(graph_a, graph_b, name_a, name_b, verbose=verbose) or ""
    if fmt == "json":
        return json.dumps(diff_to_dict(graph_a, graph_b, name_a, name_b), indent=2)

    # html — both diagrams render "auto" so the viewer's light/dark toggle can
    # re-theme them (a baked palette couldn't respond to the data-theme flip).
    from .render import render_vi
    from .render.diff_viewer import build_diff_viewer

    before_svg = render_vi(graph_a, name_a, interactive=False, theme_mode="auto")
    after_svg = render_vi(graph_b, name_b, interactive=False, theme_mode="auto")
    if before_svg is None or after_svg is None:
        return None
    cmap = diff_uid(graph_a, graph_b, name_a, name_b)
    rows = netlist_diff_rows(graph_a, graph_b, name_a, name_b)

    # DISPLAY-only names (qualified, never the vi_key = absolute source path) —
    # same rule as render titles. name_a/name_b stay the vi_key for the graph
    # ops above (render, diff_uid).
    display_a = graph_a.vi_display_name(name_a)
    display_b = graph_b.vi_display_name(name_b)
    # Rewrite each change's ``full_id`` prefix from the vi_key to the qualified
    # display name — its intended form ("Class.lvclass:vi.vi::uid") — so the
    # serialized change-map never carries the source path. Synthetic ids (no
    # vi_key prefix) are left untouched; the viewer keys on ``uid``, not full_id.
    for c in cmap.changes:
        if c.full_id.startswith(f"{name_a}::"):
            c.full_id = display_a + c.full_id[len(name_a):]
        elif c.full_id.startswith(f"{name_b}::"):
            c.full_id = display_b + c.full_id[len(name_b):]

    def _label(name: str, ref: str | None) -> str:
        return f"{name} ({ref})" if ref else name

    before_label = _label(display_a, before_ref)
    after_label = _label(display_b, after_ref)
    title = (
        before_label
        if before_label == after_label
        else f'{before_label} <span class="t-arr">&#8594; {after_label}</span>'
    )
    return build_diff_viewer(
        cmap,
        before_svg,
        after_svg,
        title=title,
        before_label=before_label,
        after_label=after_label,
        netlist_rows=rows_to_json(rows),
    )
