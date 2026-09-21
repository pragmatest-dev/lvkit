"""Front-panel control glyphs: one draw function per control-type family,
dispatched by a resolver list mirroring ``render/nodes.py``'s glyph-resolver
pattern (a plain ordered list; the first match wins; a fallback always
succeeds — see ``resolve_control_glyph``).

Week-1 scope (per the plan): the control types already proven complete by
``scripts/panelgen``'s data extraction -- stdString, stdPath, stdNum/
stdNumeric, stdBool, stdEnum/stdRing, stdClust (composite), indArr
(composite). Anything else falls back to a labeled box, matching this
project's existing convention for an unresolved primitive: visible, never
silently dropped.

Each draw function receives the ABSOLUTE box to draw into (an ``FPBox``,
already placed by ``geometry.py``) and draws ONLY its own chrome — a
composite type (cluster/array) draws its own border/caption, then the
caller (``compose.py``) recurses into its already-placed children.
"""

from __future__ import annotations

from collections.abc import Callable

from ..backend import Backend
from ..glyph import fit_label
from ..style import Theme
from .geometry import FPBox

DrawFn = Callable[[FPBox, Backend, Theme], None]


def _label(box: FPBox, backend: Backend, theme: Theme) -> None:
    """A control's caption, at its own real label position when the heap
    recorded one; centered above the value box otherwise."""
    x1, y1, x2, y2 = box.label_bounds or box.bounds
    if box.label_bounds is None:
        y2 = y1 - 2
        y1 = y2 - 14
    text = fit_label(box.control.name, x2 - x1, backend, 11.0)
    backend.text((x1 + x2) / 2, y2 - 3, text, 11.0, fill=theme.text)


def _value_cell(box: FPBox, backend: Backend, theme: Theme, text: str) -> None:
    x1, y1, x2, y2 = box.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.fp_value_fill, stroke=theme.struct_border,
                 stroke_width=1.0)
    fitted = fit_label(text, x2 - x1 - 6, backend, 11.0)
    backend.text((x1 + x2) / 2, (y1 + y2) / 2 + 4, fitted, 11.0,
                 fill=theme.fp_value_text)


def draw_string(box: FPBox, backend: Backend, theme: Theme) -> None:
    _label(box, backend, theme)
    _value_cell(box, backend, theme, box.control.default_value or "")


def draw_path(box: FPBox, backend: Backend, theme: Theme) -> None:
    _label(box, backend, theme)
    _value_cell(box, backend, theme, box.control.default_value or "")


def draw_numeric(box: FPBox, backend: Backend, theme: Theme) -> None:
    _label(box, backend, theme)
    x1, y1, x2, y2 = box.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.fp_value_fill, stroke=theme.struct_border,
                 stroke_width=1.0)
    text = fit_label(box.control.default_value or "0", x2 - x1 - 16, backend, 11.0)
    backend.text((x1 + x2) / 2 - 6, (y1 + y2) / 2 + 4, text, 11.0,
                 fill=theme.fp_value_text)
    # Up/down spinner arrows, LabVIEW's numeric-control chrome.
    ax = x2 - 8
    backend.polygon([(ax - 4, (y1 + y2) / 2 - 2), (ax + 4, (y1 + y2) / 2 - 2),
                      (ax, (y1 + y2) / 2 - 7)], fill=theme.struct_border)
    backend.polygon([(ax - 4, (y1 + y2) / 2 + 2), (ax + 4, (y1 + y2) / 2 + 2),
                      (ax, (y1 + y2) / 2 + 7)], fill=theme.struct_border)


def draw_boolean(box: FPBox, backend: Backend, theme: Theme) -> None:
    _label(box, backend, theme)
    x1, y1, x2, y2 = box.bounds
    on = box.control.default_value in ("True", "1", True)
    fill = theme.wire_bool if on else theme.fp_panel
    # A slide-switch track + thumb, clean-room original chrome (never a copy
    # of LabVIEW's own switch art).
    backend.rect(x1, y1, x2, y2, fill=fill, stroke=theme.struct_border,
                 stroke_width=1.0, rx=(y2 - y1) / 2)
    thumb_x = x2 - (y2 - y1) / 2 if on else x1 + (y2 - y1) / 2
    backend.circle(thumb_x, (y1 + y2) / 2, (y2 - y1) / 2 - 3, fill=theme.canvas,
                    stroke=theme.struct_border, stroke_width=1.0)


def draw_enum(box: FPBox, backend: Backend, theme: Theme) -> None:
    _label(box, backend, theme)
    x1, y1, x2, y2 = box.bounds
    idx = 0
    try:
        idx = int(box.control.default_value or 0)
    except ValueError:
        idx = 0
    text = (
        box.control.enum_values[idx]
        if box.control.enum_values and 0 <= idx < len(box.control.enum_values)
        else (box.control.default_value or "")
    )
    backend.rect(x1, y1, x2, y2, fill=theme.fp_value_fill, stroke=theme.struct_border,
                 stroke_width=1.0)
    fitted = fit_label(text, x2 - x1 - 20, backend, 11.0)
    backend.text(x1 + 6, (y1 + y2) / 2 + 4, fitted, 11.0, fill=theme.fp_value_text,
                 anchor="start")
    # Dropdown chevron, clean-room original.
    cx = x2 - 10
    cy = (y1 + y2) / 2
    backend.polygon([(cx - 4, cy - 2), (cx + 4, cy - 2), (cx, cy + 3)],
                     fill=theme.struct_border)


def draw_cluster(box: FPBox, backend: Backend, theme: Theme) -> None:
    """A cluster's own border + caption; its FIELDS are drawn by the caller
    recursing into ``box.children`` (already placed by geometry.py) — this
    function draws ONLY the container chrome, never its contents, matching
    the composite-glyph split ``composite.py::StructureObject`` already
    uses for block-diagram structures."""
    x1, y1, x2, y2 = box.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.fp_panel, stroke=theme.struct_border,
                 stroke_width=1.5)
    _label(box, backend, theme)


def draw_array(box: FPBox, backend: Backend, theme: Theme) -> None:
    """An array's index display + its one visible element cell. Week-1 scope
    draws the element's box (recursed by the caller when it's a cluster);
    the index spinner is drawn here since it's the array's OWN chrome, not a
    child control."""
    x1, y1, x2, y2 = box.bounds
    idx_w = min(24.0, (x2 - x1) / 4)
    backend.rect(x1, y1, x1 + idx_w, y1 + 16, fill=theme.fp_index_fill,
                 stroke=theme.struct_border, stroke_width=1.0)
    backend.text(x1 + idx_w / 2, y1 + 12, "0", 10.0, fill=theme.fp_value_text)
    _label(box, backend, theme)
    if not box.children:
        # No element geometry to recurse into -- draw a plain placeholder box
        # so the array is still visible.
        backend.rect(x1, y1 + 18, x2, y2, fill=theme.canvas, stroke=theme.struct_border,
                      stroke_width=1.0)


def draw_unknown(box: FPBox, backend: Backend, theme: Theme) -> None:
    """A control type outside week-1 scope (listbox, tab control, refnum,
    waveform graph, ...) -- a labeled fallback box, matching this project's
    existing convention for an unresolved primitive: visible, never silently
    dropped."""
    x1, y1, x2, y2 = box.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.canvas, stroke=theme.struct_border,
                 stroke_width=1.0, stroke_dasharray="3,2")
    fitted = fit_label(box.control.control_type, x2 - x1 - 6, backend, 10.0)
    backend.text((x1 + x2) / 2, (y1 + y2) / 2 + 3, fitted, 10.0,
                 fill=theme.pane_type_text)


# control_type -> draw function. A plain dict is the registration point (no
# dynamic-registration framework needed for a closed, known set) -- mirrors
# render/nodes.py's ordered-resolver-list simplicity for a case where every
# entry is an EXACT key match rather than a predicate chain.
_BY_CONTROL_TYPE: dict[str, DrawFn] = {
    "stdString": draw_string,
    "stdPath": draw_path,
    "stdNum": draw_numeric,
    "stdNumeric": draw_numeric,
    "stdBool": draw_boolean,
    "stdEnum": draw_enum,
    "stdRing": draw_enum,
    "stdClust": draw_cluster,
    "indArr": draw_array,
}

# Composite types whose children the caller recurses into (drawn on TOP of
# this function's own container chrome) -- everything else is a leaf.
COMPOSITE_CONTROL_TYPES = frozenset({"stdClust", "indArr"})


def resolve_control_glyph(control_type: str) -> DrawFn:
    """The draw function for ``control_type``, or the labeled-fallback box
    for anything outside week-1 scope. Never fails to resolve."""
    return _BY_CONTROL_TYPE.get(control_type, draw_unknown)
