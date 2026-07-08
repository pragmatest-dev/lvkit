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
    struct_border: str = "#1e1e1e"       # loop/structure border (near-black, per GT)
    prim_fill: str = "#fff6d8"
    prim_stroke: str = "#b07d10"
    term_fill: str = "#fff3e2"
    const_fill: str = "#ffffff"       # numeric/string constant box background
    loop_term: str = "#1f3fbf"           # N / i border terminal
    loop_term_fill: str = "#ffffcc"      # pale-yellow fill of the N/i box (per GT)
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
    tunnel_border: str = "#4a4a3a"       # dark-olive border of tunnel/index boxes
    coercion_dot: str = "#e01f1f"         # red coercion-dot fill (per LabVIEW)
    fp_panel: str = "#e2e2e2"       # grey inner panel of an FP control/indicator
    fp_value_fill: str = "#ffffff"  # recessed numeric value cell
    fp_value_text: str = "#333333"
    fp_index_fill: str = "#c8c8c8"  # array index-display cell
    localvar_fill: str = "#ffffff"       # local-variable box background
    localvar_stroke: str = "#4a4a3a"     # local-variable box border

    # Wire colors by LabVIEW type family.
    wire_float: str = "#e8821e"    # orange — DBL/float (also the P0 default)
    wire_int: str = "#1f3fbf"      # blue — integers, enums, rings
    wire_bool: str = "#4a9c3e"     # green — boolean
    wire_string: str = "#e05fa0"   # pink — string
    wire_path: str = "#1f8a8a"     # teal — path
    wire_cluster: str = "#8a5a2b"  # brown — clusters / typedefs
    wire_error: str = "#a88d1e"    # mustard/dark-yellow — error clusters (LV 8.2+)
    wire_variant: str = "#840984"  # purple — Variant (NI rgb(132,9,132))
    wire_default: str = "#e8821e"  # anything unrecognized falls back to DBL


DEFAULT_THEME = Theme()

# Unified diagram line width (wires + structure borders), matched to the
# ground truth's ~1px scalar wire / structure-border weight.
_LINE_W = 1.2
# Extra stroke width per array dimension — an array wire is drawn markedly
# bolder than its scalar element (1D ~2.8px vs 1.2px), thicker still for 2D+.
_ARRAY_W_PER_DIM = 1.6


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


_NUMERIC_TYPES = _INT_TYPES | _FLOAT_TYPES | _COMPLEX_TYPES


def numeric_repr(lv_type: LVType | None) -> str | None:
    """The numeric representation of a type (recursing into array elements),
    or None if it isn't numeric. A LabVIEW coercion dot appears ONLY when two
    wired terminals differ in numeric representation (I32 vs DBL) — NOT for
    structural differences like array↔element at an auto-indexing tunnel."""
    if lv_type is None:
        return None
    if lv_type.kind == "array":
        return numeric_repr(lv_type.element_type)
    if lv_type.kind == "primitive" and lv_type.underlying_type in _NUMERIC_TYPES:
        return lv_type.underlying_type
    return None


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

def numeric_sample(lv_type: LVType | None) -> str | None:
    """LabVIEW's type-representative glyph shown inside a front-panel numeric/
    string terminal in icon view (e.g. a DBL numeric shows "1.23", an integer
    "123"). This is a deterministic TYPE->glyph mapping (LabVIEW chrome), not
    per-VI data — analogous to type_repr mapping DBL->"DBL". Returns None for
    types with no text glyph (e.g. Boolean, which LabVIEW draws as a button)."""
    if lv_type is None:
        return None
    if lv_type.kind == "array":
        return numeric_sample(lv_type.element_type)
    ut = lv_type.underlying_type or ""
    if "Float" in ut or "Ext" in ut or "Complex" in ut:
        return "1+2i" if "Complex" in ut else "1.23"
    if "Int" in ut:
        return "123"
    if ut == "String":
        return "abc"
    return None


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
    "variant": "wire_variant",
}


def type_family(lv_type: LVType | None) -> str:
    """Coarse family bucket for an LVType: "float", "int", "bool",
    "string", "path", "enum", "cluster", "error_cluster", "variant",
    "array", or "unknown". The single source of truth for wire color and
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
        if ut in ("Variant", "LVVariant"):
            return "variant"
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
        return WireStyle(theme.wire_default, _LINE_W)

    if lv_type.kind == "array":
        inner = wire_style(lv_type.element_type, theme)
        return WireStyle(
            inner.color, inner.width + _ARRAY_W_PER_DIM * (lv_type.dimensions or 1),
        )

    family = type_family(lv_type)
    color = getattr(theme, _FAMILY_COLOR.get(family, ""), theme.wire_default)
    return WireStyle(color, _LINE_W)


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
