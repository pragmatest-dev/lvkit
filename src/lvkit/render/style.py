"""Rendering theme: colors and wire styling.

All colors live on one ``Theme`` object so dark-mode/doc theming is a single
swap, not a cross-cutting edit. ``wire_style()`` is the one place that maps a
wire's LabVIEW type to a color/width — it branches on the SOURCE terminal's
``LVType`` (a wire carries no type of its own; see ``graph/models.py::Wire``).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import LVType, _is_error_cluster


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
    coercion_dot: str = "#5a5a5a"         # gray coercion-dot fill

    # Wire colors by LabVIEW type family.
    wire_float: str = "#e8821e"    # orange — DBL/float (also the P0 default)
    wire_int: str = "#1f3fbf"      # blue — integers, enums, rings
    wire_bool: str = "#4a9c3e"     # green — boolean
    wire_string: str = "#e05fa0"   # pink — string
    wire_path: str = "#1f8a8a"     # teal — path
    wire_cluster: str = "#8a5a2b"  # brown — clusters / typedefs
    wire_error: str = "#3a3a3a"    # dark — error clusters
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
_FLOAT_TYPES = {"NumFloat32", "NumFloat64", "NumFloatExt"}
# Complex is orange like floats in LabVIEW (was falling through to "unknown").
_COMPLEX_TYPES = {"NumComplex64", "NumComplex128", "NumComplexExt"}

# LabVIEW data-type terminal text, exactly as the reference manual draws it
# (e.g. an orange `[DBL]` box for a DBL array — verified against the PDF).
_TYPE_REPR = {
    "NumFloat64": "DBL", "NumFloat32": "SGL", "NumFloatExt": "EXT",
    "NumComplex64": "CSG", "NumComplex128": "CDB", "NumComplexExt": "CXT",
    "NumInt8": "I8", "NumInt16": "I16", "NumInt32": "I32", "NumInt64": "I64",
    "NumUInt8": "U8", "NumUInt16": "U16", "NumUInt32": "U32", "NumUInt64": "U64",
    "Boolean": "TF", "String": "abc", "Path": "Path",
    "Refnum": "Ref", "Variant": "Var", "LVVariant": "Var",
}


def type_repr(lv_type: LVType | None) -> str:
    """The LabVIEW data-type terminal text for an LVType.

    Array → the element repr wrapped in one bracket pair per dimension
    (`[DBL]`, `[[I32]]`); scalars → `DBL`/`I32`/`TF`/`abc`/…; cluster/None → "".
    """
    if lv_type is None:
        return ""
    if lv_type.kind == "array":
        dims = lv_type.dimensions or 1
        inner = type_repr(lv_type.element_type) or "?"
        return "[" * dims + inner + "]" * dims
    if lv_type.kind in ("enum", "ring"):
        return "Enum"
    if lv_type.kind in ("cluster", "typedef_ref"):
        return ""  # clusters have no single-token repr
    if lv_type.kind == "primitive":
        return _TYPE_REPR.get(lv_type.underlying_type or "", "")
    return ""

# Coarse type-family buckets, shared by wire coloring AND front-panel
# terminal glyph selection (draw.py) so both stay driven by one table.
_FAMILY_COLOR = {
    "float": "wire_float",
    "int": "wire_int",
    "enum": "wire_int",
    "bool": "wire_bool",
    "string": "wire_string",
    "path": "wire_path",
    "cluster": "wire_cluster",
    "error_cluster": "wire_error",
}


def type_family(lv_type: LVType | None) -> str:
    """Coarse family bucket for an LVType: "float", "int", "bool",
    "string", "path", "enum", "cluster", "error_cluster", "array", or
    "unknown". The single source of truth for both wire color and
    front-panel terminal glyph choice.
    """
    if lv_type is None:
        return "unknown"
    if lv_type.kind == "array":
        return "array"
    if lv_type.kind in ("enum", "ring"):
        return "enum"
    if lv_type.kind in ("cluster", "typedef_ref"):
        return "error_cluster" if _is_error_cluster(lv_type) else "cluster"
    if lv_type.kind == "primitive":
        ut = lv_type.underlying_type or ""
        if ut in _FLOAT_TYPES or ut in _COMPLEX_TYPES:
            return "float"
        if ut in _INT_TYPES:
            return "int"
        if ut == "Boolean":
            return "bool"
        if ut == "String":
            return "string"
        if ut == "Path":
            return "path"
    return "unknown"


def wire_style(
    lv_type: LVType | None, theme: Theme = DEFAULT_THEME,
) -> WireStyle:
    """Color/width for a wire, from the SOURCE terminal's LVType.

    Branches on ``kind`` then ``underlying_type``: DBL orange (default),
    ints blue, bool green, string pink, path teal, cluster brown, error
    cluster dark, array thicker (by ``dimensions``, color inherited from
    the element type).
    """
    if lv_type is None:
        return WireStyle(theme.wire_default, 2.0)

    if lv_type.kind == "array":
        inner = wire_style(lv_type.element_type, theme)
        return WireStyle(inner.color, inner.width + (lv_type.dimensions or 1))

    family = type_family(lv_type)
    color = getattr(theme, _FAMILY_COLOR.get(family, ""), theme.wire_default)
    return WireStyle(color, 2.0)


# --------------------------------------------------------------------- #
# Coercion detection: normalized type key, ignoring provenance-only
# fields (description/values/fields/typedef_*) that legitimately differ
# per-side via graph type enrichment (see graph/core.py::_enrich_type).
# --------------------------------------------------------------------- #

CoercionKey = tuple[str, str | None, int | None, "CoercionKey | None"]


def coercion_key(lv_type: LVType | None) -> CoercionKey | None:
    """Normalized type identity for coercion-dot detection.

    ``None`` means "no type info" — callers must treat that as "no dot"
    on either side, not as a mismatch.
    """
    if lv_type is None:
        return None
    return (
        lv_type.kind,
        lv_type.underlying_type,
        lv_type.dimensions,
        coercion_key(lv_type.element_type),
    )
