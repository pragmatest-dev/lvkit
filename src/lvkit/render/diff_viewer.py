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

VI-Properties/VIHealth/Signature changes are ordinary ``kind="property"``/
``"health"``/``"signature"`` entries inside ``change_map`` now (see
``graph.diff.diff_uid``) -- there is no separate re-derivation here any
more. This module stays a PURE builder: it just serializes whatever
``change_map`` already carries.
"""

from __future__ import annotations

import json
from importlib.resources import files

from ..graph.diff import ChangeMap
from .connector_pane_panel import (
    DIFF_CONNECTOR_PANE_BUTTON,
    DIFF_CONNECTOR_PANE_SCRIPT,
)
from .help_tip import HELP_TIP
from .properties_panel import DIFF_PROPERTIES_BUTTON, DIFF_PROPERTIES_PANEL
from .theme_control import THEME_CONTROL_BUTTON, THEME_CONTROL_SCRIPT

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

    ``change_map``'s own ``"changes"`` (``ChangeMap.to_dict()``) already
    includes VI-Properties/VIHealth/Signature changes as ordinary
    ``kind="property"``/``"health"``/``"signature"`` entries (see
    ``graph.diff.diff_uid``) — the Flat list AND the header's "modified"
    tally pick them up for free, with no separate re-derivation from the two
    SVGs' embedded ``data-lv-*`` JSON.

    Returns the full HTML document as a string, prefixed with a doctype +
    charset meta tag so it's a valid standalone file.
    """
    data = change_map.to_dict()
    changes = list(data["changes"])
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
        .replace("__THEME_BTN__", THEME_CONTROL_BUTTON)
        .replace("__THEME_SCRIPT__", THEME_CONTROL_SCRIPT)
        .replace("__DIFF_PROPERTIES_BTN__", DIFF_PROPERTIES_BUTTON)
        .replace("__DIFF_PROPERTIES_PANEL__", DIFF_PROPERTIES_PANEL)
        .replace("__DIFF_CONNECTOR_PANE_BTN__", DIFF_CONNECTOR_PANE_BUTTON)
        .replace("__DIFF_CONNECTOR_PANE_SCRIPT__", DIFF_CONNECTOR_PANE_SCRIPT)
        .replace("__HELP_TIP__", HELP_TIP)
    )
    return "<!doctype html>\n<meta charset='utf-8'>\n" + html
