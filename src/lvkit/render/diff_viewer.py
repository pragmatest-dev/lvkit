"""Build the interactive VI-diff viewer HTML page (roadmap #24).

A PURE builder: given a change-map and two already-rendered SVGs, staples
them into one self-contained HTML page (overlay/before/after/split
modes, global zoom, a numbered change-list sidebar, correlated highlights,
spotlight-on-select, per-pane case-frame control, and deep-linking). No VI
loading, no disk reads beyond the packaged template, no argv — callers (the
CLI's ``diff --format html``, a future VSCode extension, the PR Action) do
all of that and pass in the results.

This module lifts (does not redesign) the prototype at
``.tmp/build_vi_diff_viewer.py``, which loaded VIs, read SVGs off disk, and
wrote its own output file. The HTML/CSS/JS template itself moved verbatim to
``templates/diff_viewer.html`` (see that file's docstring-less header for the
placeholder markers).
"""

from __future__ import annotations

import json
from importlib.resources import files

from ..graph.diff import ChangeMap

__all__ = ["build_diff_viewer"]


def build_diff_viewer(
    change_map: ChangeMap,
    before_svg: str,
    after_svg: str,
    *,
    title: str,
    before_label: str,
    after_label: str,
    netlist_rows: list[dict] | None = None,
) -> str:
    """Render the two-pane diff viewer page for one VI pair.

    ``before_svg``/``after_svg`` are expected to be script-less/id-less (i.e.
    rendered via ``render_vi(..., interactive=False)``) — the viewer drives
    its own frame/hover behavior over the ``data-*`` attributes the renderer
    still emits, so there is no id collision to work around (unlike the
    prototype's ``.replace('id="lv-', 'id="before-lv-', 1)`` hack, dropped
    here — increment 1 made it unnecessary).

    ``netlist_rows`` is the JSON form of ``diff.py``'s ``NetlistDiffRow``
    tree (``rows_to_json(netlist_diff_rows(...))`` — see
    ``.tmp/netlist-spec.md`` Phase 3): the SAME rows ``format_diff`` renders
    to text, embedded as ``__NETLIST_TREE__`` so the viewer's Tree view
    renders the identical netlist diff instead of regrouping ``CHANGES``
    client-side. This module stays PURE — it does not build the rows itself
    (that needs loaded graphs); the caller (``cmd_diff``) computes them and
    passes the JSON in. ``None``/omitted renders an empty tree (``[]``).

    Returns the full HTML document as a string, prefixed with a doctype +
    charset meta tag so it's a valid standalone file.
    """
    data = change_map.to_dict()
    changes = data["changes"]
    common = data["common_nodes"]
    added = sum(1 for c in changes if c["change"] == "added")
    removed = sum(1 for c in changes if c["change"] == "removed")
    modified = sum(1 for c in changes if c["change"] == "modified")

    template = (
        files("lvkit.render") / "templates" / "diff_viewer.html"
    ).read_text(encoding="utf-8")

    html = (
        template.replace("__TITLE__", title)
        .replace("__BEFORE_LABEL__", before_label)
        .replace("__AFTER_LABEL__", after_label)
        .replace("__BEFORE_SVG__", before_svg)
        .replace("__AFTER_SVG__", after_svg)
        .replace("__CHANGES__", json.dumps(changes))
        .replace("__NETLIST_TREE__", json.dumps(netlist_rows or []))
        .replace("__ADD__", str(added))
        .replace("__DEL__", str(removed))
        .replace("__MOD__", str(modified))
        .replace("__COMMON__", str(common))
    )
    return "<!doctype html>\n<meta charset='utf-8'>\n" + html
