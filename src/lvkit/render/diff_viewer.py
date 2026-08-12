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
import re
from importlib.resources import files
from typing import Any

from ..graph.diff import ChangeMap
from ..graph.models import CURATED_PROPERTY_FLAGS, CURATED_STRUCTURE_FLAGS, bool_str
from .help_tip import HELP_TIP
from .properties_panel import DIFF_PROPERTIES_BUTTON, DIFF_PROPERTIES_PANEL
from .theme_control import THEME_CONTROL_BUTTON, THEME_CONTROL_SCRIPT

__all__ = ["build_diff_viewer"]

# Matches a root-<svg> data-lv-properties='...'/data-lv-structure='...'
# attribute (see render/__init__.py's _vi_properties_data_attrs -- always
# single-quoted, compact JSON). A plain regex over the raw SVG string, not a
# real parse -- this module stays a PURE builder (no VI/graph access), so
# re-deriving the metadata diff means reading it back out of the two already-
# rendered SVGs the caller handed in, the same way a non-Python host (e.g.
# the VS Code extension) would.
_LV_DATA_ATTR_RE = re.compile(r"data-lv-(properties|structure)='([^']*)'")


def _parse_lv_data_attrs(svg: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pull one SVG's root-level ``(properties, structure)`` dicts back out of
    its embedded ``data-lv-*`` JSON attributes. Scoped to the OPENING tag only
    (``svg.split(">", 1)[0]``, same technique ``tests/test_viewer_properties.
    py``'s ``_extract_attr`` uses) so a nested icon ``<svg>`` fragment further
    into the document can never be mistaken for the root. Degrades to
    ``({}, {})`` on anything missing/malformed -- the diff viewer must never
    fail to build just because a pane's metadata is absent."""
    head = svg.split(">", 1)[0]
    props: dict[str, Any] = {}
    struct: dict[str, Any] = {}
    for name, blob in _LV_DATA_ATTR_RE.findall(head):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if name == "properties":
            props = parsed
        else:
            struct = parsed
    return props, struct


def _metadata_change(
    kind: str, field: str, label: str, old: object, new: object,
) -> dict[str, Any]:
    """One VI-Properties/VIStructure change as a first-class ``CHANGES``
    entry: ``uid=None`` (there is no diagram element to highlight -- only a
    VI-level setting), ``change="modified"`` (a MetadataChange is ALWAYS a
    value transition, never added/removed -- see ``diff.py``'s
    ``MetadataChange`` docstring), and ``label``/``element`` chosen so the
    Flat-list JS's row renderer (``changeRowHtml`` in diff_viewer.html) shows
    ``label: old -> new`` as the row's value line -- the exact text shape
    ``diff.py``'s ``_metadata_change_text`` renders for the Tree/text/JSON
    diff. Every other ``ElementChange`` key the JS may probe (``bounds``,
    ``path``, ``container_uid``, ...) is present but None/empty, so a
    bounds-less/uid-less change just renders its row with no diagram
    highlight attempt -- never a crash.

    ``field`` is the RAW ``VIProperties``/``VIStructure`` dataclass field
    name (e.g. ``"run_when_opened"``, ``"lock_state"``) -- NOT the curated
    display ``label`` (e.g. "run-on-open"), which can differ from it. It is
    the key the properties popover's own rows carry as ``data-key`` (see
    ``properties_panel.py``'s ``_PANEL_BODY_JS``), so clicking this CHANGES
    entry (diff_viewer.html's ``revealPropertyRow``) can look the matching
    popover row up by an EXACT field match instead of parsing display text."""
    return {
        "uid": None, "full_id": None, "kind": kind, "change": "modified",
        "label": f"{label}: {old} -> {new}", "detail": f"{old} -> {new}",
        "field": field,
        "bounds": None, "bounds_before": None, "path": None, "path_before": None,
        "chain_paths": None, "container_uid": None, "frame_path": None,
        "frame_path_before": None, "element": None, "endpoints": None,
    }


# Enum-valued ``execution`` fields diffed as TRANSITIONS, mirroring
# ``graph.diff._PROPERTY_ENUM_FIELDS`` -- the raw field name IS the display
# label (no curated alias, unlike ``lock_state``'s "lock").
_PROPERTY_ENUM_FIELDS: tuple[str, ...] = ("priority", "reentrancy", "exec_system")


def _metadata_changes(before_svg: str, after_svg: str) -> list[dict[str, Any]]:
    """Curated VI-Properties/VIStructure changes between the two panes'
    embedded facets, as first-class ``CHANGES`` entries -- so the Flat list
    AND the header's "modified" tally include them, not just the Tree view
    (``netlist_diff_rows``, computed separately by the caller from the loaded
    graphs). Uses the SAME curated flag vocabulary
    (``graph.models.CURATED_PROPERTY_FLAGS``/``CURATED_STRUCTURE_FLAGS``),
    enum fields (``_PROPERTY_ENUM_FIELDS``/``typedef_status``), and
    ``label: old -> new`` text shape as ``diff.py``'s
    ``_diff_vi_properties``/``_diff_vi_structure``/``_metadata_change_text``
    -- this is a pure re-derivation from the two SVGs' own already-embedded
    JSON (see ``_parse_lv_data_attrs``), not a second pass over the graph
    (``graph/diff.py`` stays untouched by this module)."""
    before_props, before_struct = _parse_lv_data_attrs(before_svg)
    after_props, after_struct = _parse_lv_data_attrs(after_svg)

    changes: list[dict[str, Any]] = []

    if before_props and after_props:
        before_lock = before_props.get("lock_state")
        after_lock = after_props.get("lock_state")
        if before_lock != after_lock:
            changes.append(_metadata_change(
                "property", "lock_state", "lock", before_lock, after_lock,
            ))

        before_exec = before_props.get("execution") or {}
        after_exec = after_props.get("execution") or {}
        for field in _PROPERTY_ENUM_FIELDS:
            old_v = before_exec.get(field)
            new_v = after_exec.get(field)
            if old_v != new_v:
                changes.append(_metadata_change(
                    "property", field, field, old_v, new_v,
                ))
        for field, label in CURATED_PROPERTY_FLAGS.items():
            old_v = bool(before_exec.get(field))
            new_v = bool(after_exec.get(field))
            if old_v != new_v:
                changes.append(_metadata_change(
                    "property", field, label, bool_str(old_v), bool_str(new_v),
                ))

    if before_struct and after_struct:
        before_typedef = before_struct.get("typedef_status")
        after_typedef = after_struct.get("typedef_status")
        if before_typedef != after_typedef:
            changes.append(_metadata_change(
                "structure", "typedef_status", "typedef_status",
                before_typedef, after_typedef,
            ))
        for field, label in CURATED_STRUCTURE_FLAGS.items():
            old_v = bool(before_struct.get(field))
            new_v = bool(after_struct.get(field))
            if old_v != new_v:
                changes.append(_metadata_change(
                    "structure", field, label, bool_str(old_v), bool_str(new_v),
                ))

    return changes


def build_diff_viewer(
    change_map: ChangeMap,
    before_svg: str,
    after_svg: str,
    *,
    title: str,
    before_label: str,
    after_label: str,
    netlist_rows: list[dict] | None = None,
    metadata_changes: list[dict[str, Any]] | None = None,
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

    ``metadata_changes`` are VI-Properties/VIStructure changes as first-class
    ``CHANGES`` entries (``_metadata_change``'s shape) — so the Flat list AND
    the header's "modified" tally include them too, not just the Tree
    (netlist_rows already carries its own copy via ``_metadata_rows``,
    counted separately there — the two views are independent, so there is no
    double count). ``None`` (the default) auto-derives them from
    ``before_svg``/``after_svg``'s own embedded ``data-lv-*`` JSON (see
    ``_metadata_changes``) — the normal path, since this module has no graph
    access to compute them any other way. Pass an explicit list (``[]``
    included) to override/skip that derivation.

    Returns the full HTML document as a string, prefixed with a doctype +
    charset meta tag so it's a valid standalone file.
    """
    data = change_map.to_dict()
    changes = list(data["changes"]) + (
        _metadata_changes(before_svg, after_svg)
        if metadata_changes is None else metadata_changes
    )
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
        .replace("__HELP_TIP__", HELP_TIP)
    )
    return "<!doctype html>\n<meta charset='utf-8'>\n" + html
