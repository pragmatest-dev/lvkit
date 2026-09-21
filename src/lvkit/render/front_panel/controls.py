"""Front-panel control glyphs: resolve each ``ParsedFPControl`` to a plain
``Glyph`` (``draw(backend, bounds, theme)`` -- the same protocol every
block-diagram glyph already implements, ``glyphs/nodes/base.py:16-19``),
REUSING the existing constant glyphs for chrome this project already solved
rather than redrawing it:

- ``ClusterConstantGlyph`` already places a cluster's fields at their real
  heap geometry (uniform-scale fit + real per-field label position) AND
  draws the container border/fill -- a front-panel cluster control is built
  as one of these, not a second, weaker implementation that forgot the
  container box (the bug this file used to have).
- ``ArrayConstantGlyph`` already draws the container box + a whole number of
  repeated element rows (computed from the real container height and the
  element's own cell size) -- a front-panel array control is built as one of
  these too, so an empty/default array shows LabVIEW's own "repeat the
  element type's default value to fill the visible rows" convention instead
  of one blank cell.

Genuinely new here is only the front-panel CONTROL chrome a block-diagram
CONSTANT never needs -- a numeric control's spinner arrows, a boolean
control's slide switch, an enum/ring control's dropdown chevron -- plus the
labeled fallback box for anything outside week-1 scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...parser.layout import Rect
from ...parser.models import ParsedFPControl, ParsedFPPart
from ..backend import Backend
from ..glyph import (
    ArrayConstantGlyph,
    ClusterConstantGlyph,
    ConstantGlyph,
    Glyph,
    PathGlyph,
    fit_label,
    fit_value,
)
from ..style import Theme


def draw_label(name: str, bounds: Rect, backend: Backend, theme: Theme) -> None:
    """A TOP-LEVEL control's own caption, synthesized just above its box.

    Only called once per top-level control (see ``compose.draw_front_panel``)
    -- a nested cluster FIELD's caption is drawn by ``ClusterConstantGlyph``
    itself, at the field's own real heap ``label_rect``, so it is never drawn
    twice."""
    x1, y1, x2, _y2 = bounds
    label_y2 = y1 - 2
    text = fit_label(name, x2 - x1, backend, 11.0)
    backend.text((x1 + x2) / 2, label_y2 - 3, text, 11.0, fill=theme.text)


@dataclass(frozen=True)
class NumericControlGlyph:
    """A front-panel numeric control: a recessed value cell + up/down spinner
    arrows -- chrome a numeric block-diagram CONSTANT (``ConstantGlyph``)
    never has, so this stays its own glyph."""

    value: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(x1, y1, x2, y2, fill=theme.fp_value_fill,
                     stroke=theme.struct_border, stroke_width=1.0)
        text = fit_value(self.value or "0", x2 - x1 - 16, backend, 11.0)
        backend.text((x1 + x2) / 2 - 6, (y1 + y2) / 2 + 4, text, 11.0,
                     fill=theme.fp_value_text)
        ax = x2 - 8
        cy = (y1 + y2) / 2
        backend.polygon([(ax - 4, cy - 2), (ax + 4, cy - 2), (ax, cy - 7)],
                         fill=theme.struct_border)
        backend.polygon([(ax - 4, cy + 2), (ax + 4, cy + 2), (ax, cy + 7)],
                         fill=theme.struct_border)


@dataclass(frozen=True)
class BooleanControlGlyph:
    """A front-panel boolean control: a slide-switch track + thumb, clean-room
    original chrome (never a copy of LabVIEW's own switch art) -- a different,
    correct visual metaphor from ``BooleanConstantGlyph``'s fixed push-button
    T/F box, which a CONSTANT (never resized, never "on/off" as a control is)
    always uses regardless of style."""

    on: bool

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        fill = theme.wire_bool if self.on else theme.fp_panel
        backend.rect(x1, y1, x2, y2, fill=fill, stroke=theme.struct_border,
                     stroke_width=1.0, rx=(y2 - y1) / 2)
        thumb_x = x2 - (y2 - y1) / 2 if self.on else x1 + (y2 - y1) / 2
        backend.circle(thumb_x, (y1 + y2) / 2, (y2 - y1) / 2 - 3, fill=theme.canvas,
                        stroke=theme.struct_border, stroke_width=1.0)


@dataclass(frozen=True)
class EnumControlGlyph:
    """A front-panel ring/enum control: a value cell showing the selected
    item's real label + a dropdown chevron -- chrome a block-diagram enum
    constant never needs."""

    default_value: str | None
    enum_values: tuple[str, ...]

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        idx = 0
        try:
            idx = int(self.default_value or 0)
        except ValueError:
            idx = 0
        text = (
            self.enum_values[idx]
            if self.enum_values and 0 <= idx < len(self.enum_values)
            else (self.default_value or "")
        )
        backend.rect(x1, y1, x2, y2, fill=theme.fp_value_fill,
                     stroke=theme.struct_border, stroke_width=1.0)
        fitted = fit_label(text, x2 - x1 - 20, backend, 11.0)
        backend.text(x1 + 6, (y1 + y2) / 2 + 4, fitted, 11.0, fill=theme.fp_value_text,
                     anchor="start")
        cx = x2 - 10
        cy = (y1 + y2) / 2
        backend.polygon([(cx - 4, cy - 2), (cx + 4, cy - 2), (cx, cy + 3)],
                         fill=theme.struct_border)


@dataclass(frozen=True)
class UnknownControlGlyph:
    """A control type outside week-1 scope (listbox, tab control, refnum,
    waveform graph, ...) -- a labeled dashed fallback box, matching this
    project's existing convention for an unresolved primitive: visible,
    never silently dropped."""

    control_type: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(x1, y1, x2, y2, fill=theme.canvas, stroke=theme.struct_border,
                     stroke_width=1.0, stroke_dasharray="3,2")
        fitted = fit_label(self.control_type, x2 - x1 - 6, backend, 10.0)
        backend.text((x1 + x2) / 2, (y1 + y2) / 2 + 3, fitted, 10.0,
                     fill=theme.pane_type_text)


def _resolve_leaf_glyph(
    control_type: str,
    default_value: str | None,
    enum_values: list[str],
    theme: Theme,
) -> Glyph:
    """The value glyph for a SCALAR control type. Front-panel value cells use
    a neutral ``struct_border`` outline (a real LabVIEW control's border is
    never type-colored -- wire colors are a block-diagram-only convention),
    so ``ConstantGlyph``/``PathGlyph`` are reused for their wrapping/folder-
    icon LOGIC with that neutral color passed in, not their diagram color
    choice."""
    if control_type == "stdString":
        return ConstantGlyph(
            value=default_value or "", color=theme.struct_border,
            fill_attr="fp_value_fill", text_attr="fp_value_text", multiline=True,
        )
    if control_type == "stdPath":
        return PathGlyph(value=default_value or "", color=theme.struct_border)
    if control_type in ("stdNum", "stdNumeric"):
        return NumericControlGlyph(value=default_value or "0")
    if control_type == "stdBool":
        return BooleanControlGlyph(on=default_value in ("True", "1"))
    if control_type in ("stdEnum", "stdRing"):
        return EnumControlGlyph(
            default_value=default_value, enum_values=tuple(enum_values)
        )
    return UnknownControlGlyph(control_type=control_type)


def _default_element_glyph(ctrl: ParsedFPControl, theme: Theme) -> Glyph | None:
    """The array's ELEMENT type's own glyph at its type-default value -- drawn
    for every unset/past-end row by ``ArrayConstantGlyph`` (see its
    docstring), so an EMPTY array (this real corpus's common case -- an array
    control left at its saved-empty default) still shows real, type-correct
    content instead of a blank cell. Built from ``ctrl.children``/
    ``cluster_geom`` when the element is a cluster (the SAME real per-field
    geometry a populated row would use); from the element's own control-type
    PART (``parser.vi._parse_fp_parts`` records the array's element ddo as
    the ``part_id=None`` part, e.g. ``part_class="stdNum"``) for a scalar
    element. ``None`` when neither is available (an unresolved element
    shape) -- ``ArrayConstantGlyph`` falls back to a flat grey cell then,
    same as it already does for any caller with no default glyph."""
    if ctrl.children:
        fields = tuple((f.name, resolve_glyph(f, theme)) for f in ctrl.children)
        return ClusterConstantGlyph(
            fields=fields, cluster_geom=ctrl.cluster_geom, fill_attr="fp_panel"
        )
    element_part: ParsedFPPart | None = next(
        (p for p in ctrl.parts if p.part_id is None), None
    )
    if element_part is None:
        return None
    return _resolve_leaf_glyph(element_part.part_class, None, ctrl.enum_values, theme)


def resolve_glyph(ctrl: ParsedFPControl, theme: Theme) -> Glyph:
    """The ``Glyph`` that draws ``ctrl``'s VALUE (never its own caption -- see
    ``draw_label``/``ClusterConstantGlyph``'s own field-label handling).
    Resolves recursively: a cluster's fields and an array's element are each
    resolved the same way, mirroring how ``render/nodes.py`` composes a
    block-diagram cluster/array constant from its own field/element glyphs
    -- so this is the single place a control type maps to its chrome, never
    duplicated per call site."""
    if ctrl.control_type == "stdClust":
        fields = tuple((f.name, resolve_glyph(f, theme)) for f in ctrl.children)
        return ClusterConstantGlyph(
            fields=fields, cluster_geom=ctrl.cluster_geom, fill_attr="fp_panel"
        )
    if ctrl.control_type == "indArr":
        geom = ctrl.cluster_geom
        return ArrayConstantGlyph(
            elements=(),
            element_color=theme.struct_border,
            struct_uid=ctrl.uid,
            cell_h=geom.height if geom else None,
            cell_w=geom.width if geom else None,
            default_element=_default_element_glyph(ctrl, theme),
            fill_attr="fp_panel",
        )
    return _resolve_leaf_glyph(
        ctrl.control_type, ctrl.default_value, ctrl.enum_values, theme
    )
