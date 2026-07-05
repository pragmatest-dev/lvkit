"""Rendering theme: colors and wire styling.

All colors live on one ``Theme`` object so dark-mode/doc theming is a single
swap, not a cross-cutting edit. ``wire_style()`` is the one place that maps a
wire's LabVIEW type to a color/width — it branches on the SOURCE terminal's
``LVType`` (a wire carries no type of its own; see ``graph/models.py::Wire``).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import LVType


@dataclass(frozen=True)
class Theme:
    """The renderer's full color palette."""

    canvas: str = "#fbfbf5"
    struct_border: str = "#9b9b73"       # loop/structure border (muted olive)
    prim_fill: str = "#fff6d8"
    prim_stroke: str = "#b07d10"
    term_fill: str = "#fff3e2"
    loop_term: str = "#1f3fbf"           # N / i border terminal
    cond_stop: str = "#c62828"           # while-loop conditional terminal
    subvi_fill: str = "#eef0e6"
    subvi_stroke: str = "#7a7d63"
    case_bar_fill: str = "#e9e6d2"
    case_bar_text: str = "#4a4636"
    selector_fill: str = "#e6f4d9"
    selector_stroke: str = "#5a8f3a"
    selector_text: str = "#3f6b28"
    sr_fill: str = "#cfcfcf"
    sr_stroke: str = "#555555"

    # Wire colors by LabVIEW type family.
    wire_float: str = "#e8821e"   # orange — DBL/float (also the P0 default)
    wire_int: str = "#1f3fbf"     # blue — integers, enums, rings
    wire_bool: str = "#4a9c3e"    # green — boolean
    wire_string: str = "#e05fa0"  # pink — string
    wire_cluster: str = "#8a5a2b"  # brown — clusters / typedefs
    wire_default: str = "#e8821e"  # anything unrecognized falls back to DBL


DEFAULT_THEME = Theme()


@dataclass(frozen=True)
class WireStyle:
    color: str
    width: float


_INT_TYPES = {
    "NumInt8", "NumInt16", "NumInt32", "NumInt64",
    "NumUInt8", "NumUInt16", "NumUInt32", "NumUInt64",
}
_FLOAT_TYPES = {"NumFloat32", "NumFloat64"}


def wire_style(
    lv_type: LVType | None, theme: Theme = DEFAULT_THEME,
) -> WireStyle:
    """Color/width for a wire, from the SOURCE terminal's LVType.

    Branches on ``kind`` then ``underlying_type``: DBL orange (default),
    ints blue, bool green, string pink, cluster/typedef brown, array
    thicker (color inherited from the element type).
    """
    if lv_type is None:
        return WireStyle(theme.wire_default, 2.0)

    if lv_type.kind == "array":
        inner = wire_style(lv_type.element_type, theme)
        return WireStyle(inner.color, inner.width + 1.0)

    if lv_type.kind in ("enum", "ring"):
        return WireStyle(theme.wire_int, 2.0)

    if lv_type.kind in ("cluster", "typedef_ref"):
        return WireStyle(theme.wire_cluster, 2.0)

    if lv_type.kind == "primitive":
        ut = lv_type.underlying_type or ""
        if ut in _FLOAT_TYPES:
            return WireStyle(theme.wire_float, 2.0)
        if ut in _INT_TYPES:
            return WireStyle(theme.wire_int, 2.0)
        if ut == "Boolean":
            return WireStyle(theme.wire_bool, 2.0)
        if ut == "String":
            return WireStyle(theme.wire_string, 2.0)

    return WireStyle(theme.wire_default, 2.0)
