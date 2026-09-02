"""Rendering theme: colors and wire styling.

All colors live on one ``Theme`` object so dark-mode/doc theming is a single
swap, not a cross-cutting edit. ``wire_style()`` is the one place that maps a
wire's LabVIEW type to a color/width — it branches on the SOURCE terminal's
``LVType`` (a wire carries no type of its own; see ``graph/models.py::Wire``).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from ..models import (
    _NUMERIC_TYPE_DESCRIPTOR,
    LVType,
    LVTypeKind,
    WireStyle,
    _is_error_cluster,
)

# LabVIEW help/description strings carry rich-text tags (<B>..</B>, <BR>, ...).
_HELP_TAG_RE = re.compile(r"<[^>]+>")


def clean_help_text(text: str | None) -> str | None:
    """Plain-text a LabVIEW help/description string for display: strip its
    rich-text tags (``<B>..</B>``, ``<BR>``, ...) and collapse whitespace/
    newlines. Returns None for empty-or-whitespace-only text so a panel draws
    no blank description line. Shared by every surface that shows a VI/node
    description (the block-diagram hover panel AND the connector-pane aside),
    so the same text reads identically whichever panel presents it."""
    if not text:
        return None
    return " ".join(_HELP_TAG_RE.sub(" ", text).split()) or None


@dataclass(frozen=True)
class Theme:
    """The renderer's full color palette."""

    canvas: str = "#ffffff"
    struct_border: str = "#1e1e1e"  # loop/structure border (near-black, per GT)
    while_border: str = "#808080"  # While loop's thick grey border (per GT)
    prim_fill: str = "#fff6d8"
    prim_stroke: str = "#b07d10"
    prim_text: str = "#1a1a1a"  # label text on prim_fill (Arith/Bundle/…)
    term_fill: str = "#fff3e2"
    const_fill: str = "#ffffff"  # numeric/string constant box background
    const_text: str = "#1a1a1a"  # label text on const_fill
    loop_term: str = "#1f3fbf"  # N / i border terminal
    loop_term_fill: str = "#ffffcc"  # pale-yellow fill of the N/i box (per GT)
    loop_term_text: str = "#1f3fbf"  # "N"/"i" glyph text — integer blue (I32)
    cond_stop: str = "#c62828"  # while-loop cond terminal: Stop-if-True
    cond_continue: str = "#2e7d32"  # while-loop cond terminal: Continue-if-True
    subvi_fill: str = "#eef0e6"
    subvi_stroke: str = "#7a7d63"
    subvi_text: str = "#1a1a1a"  # wrapped subVI name text on subvi_fill
    case_bar_fill: str = "#e9e6d2"
    case_bar_text: str = "#4a4636"
    case_no_error_border: str = "#2e9e3f"  # green — error-cluster "No Error" frame
    case_error_border: str = "#d32f2f"  # red — error-cluster "Error" frame
    # Event Structure border — a distinct warm amber/gold (LabVIEW's own
    # diagonal-hatch border uses a similar tan hue): a WIDE filled BAND (not
    # a thin dashed line) between the structure's outer heap bounds and its
    # inset inner content, mirroring LabVIEW's own hatched border margin
    # (see glyphs/structures/event.py::EventGlyph). ``event_border`` is the thin
    # edge-line color (outer + inner rule); ``event_band`` is the band's
    # light-yellow fill.
    event_border: str = "#b8860b"
    event_band: str = "#fdf6cf"
    # Translucent wash painted over a DISABLED subdiagram of a Diagram/
    # Conditional Disable structure (every frame except the enabled one), so
    # its greyed-out contents read as inactive — matches LabVIEW. Applied at
    # ~0.5 group opacity (see draw_scene); this is the solid grey it washes to.
    disabled_mask: str = "#9a9a9a"
    selector_stroke: str = "#5a8f3a"
    selector_text: str = "#3f6b28"
    sr_stroke: str = "#555555"
    tunnel_border: str = "#4a4a3a"  # dark-olive border of tunnel/index boxes
    coercion_dot: str = "#e01f1f"  # red coercion-dot fill (per LabVIEW)
    fp_panel: str = "#e2e2e2"  # grey inner panel of an FP control/indicator
    fp_value_fill: str = "#ffffff"  # recessed numeric value cell
    fp_value_text: str = "#333333"
    fp_index_fill: str = "#c8c8c8"  # array index-display cell
    localvar_fill: str = "#ffffff"  # local-variable box background
    localvar_stroke: str = "#4a4a3a"  # local-variable box border
    localvar_text: str = "#1a1a1a"  # wrapped local-var name text on localvar_fill
    text: str = "#1a1a1a"  # default/canvas label text (node names, FP names, …)
    # Secondary/muted text on the connector-help panel (panel bg = ``canvas``) —
    # paired with the panel's type-annotation text specifically (smaller,
    # lighter than the terminal name beside it).
    pane_type_text: str = "#777777"

    # Wire colors by LabVIEW type family.
    wire_float: str = "#e8821e"  # orange — DBL/float (also the P0 default)
    wire_int: str = "#1f3fbf"  # blue — integers, enums, rings
    wire_bool: str = "#4a9c3e"  # green — boolean
    wire_string: str = "#e05fa0"  # pink — string
    wire_path: str = "#1f8a8a"  # teal — path
    wire_cluster: str = "#8a5a2b"  # brown — clusters / typedefs
    wire_refnum: str = "#1f6b2e"  # dark green — refnums (VI refs, driver/DAQ/
    #                                VISA sessions, queues, notifiers, controls);
    #                                LabVIEW's generic reference wire color
    wire_error: str = "#a88d1e"  # mustard/dark-yellow — error clusters (LV 8.2+)
    wire_variant: str = "#840984"  # purple — Variant (NI rgb(132,9,132))
    # Unresolved / unknown-type wires — a DISTINCT dark grey, NOT the float
    # orange. A wire with no resolved type is a type-propagation BUG; colouring
    # it like a DBL float hid every such gap. Dark grey makes them stand out so
    # the real type-propagation scope is visible instead of masquerading as float.
    wire_default: str = "#555555"
    # Half-width (per side) of the canvas-colored casing drawn under each wire.
    # A background "moat" that separates a wire from its neighbours and, because
    # casing is painted per-NET (all casings then all colors, net by net), the
    # NEXT net's casing punches a gap in the PRIOR net's color at an orthogonal
    # crossing — so a crossing reads as one wire passing OVER the other, not a
    # solid `+`. Same-net branches share a solid trunk (their casings never
    # punch each other, since a net draws all its own casing before its color).
    wire_casing: float = 1.1


DEFAULT_THEME = Theme()


def css_var_theme(base: Theme = DEFAULT_THEME) -> Theme:
    """A ``Theme`` where every HEX-COLOR field (a ``str`` starting with
    ``"#"``) is rewritten to ``var(--lv-<dashed-name>, <hex>)`` — e.g.
    ``prim_fill`` -> ``"var(--lv-prim-fill, #fff6d8)"``. Non-color fields
    (``wire_casing``, a float) pass through unchanged.

    This is SAMPLER-ONLY: ``DEFAULT_THEME`` itself stays raw hex (every
    existing render/test keeps its exact output), and only a page that also
    defines the matching ``--lv-*`` custom properties gets live recoloring.
    A standalone SVG with no such page CSS still renders correct LIGHT mode,
    since the hex value is the ``var()`` fallback.
    """
    overrides: dict[str, str] = {}
    for f in dataclasses.fields(base):
        value = getattr(base, f.name)
        if isinstance(value, str) and value.startswith("#"):
            var_name = "--lv-" + f.name.replace("_", "-")
            overrides[f.name] = f"var({var_name}, {value})"
    return dataclasses.replace(base, **overrides)


# Unified diagram line width (wires + structure borders), matched to the
# ground truth's ~1px scalar wire / structure-border weight.
_LINE_W = 1.2
# Extra stroke width per array dimension — an array wire is drawn markedly
# bolder than its scalar element (1D ~2.8px vs 1.2px), thicker still for 2D+.
_ARRAY_W_PER_DIM = 1.6


_INT_TYPES = {
    "NumInt8",
    "NumInt16",
    "NumInt32",
    "NumInt64",
    "NumUInt8",
    "NumUInt16",
    "NumUInt32",
    "NumUInt64",
}
_FLOAT_TYPES = {"NumFloat32", "NumFloat64", "NumFloatExt"}
# Complex is orange like floats in LabVIEW (was falling through to "unknown").
_COMPLEX_TYPES = {"NumComplex64", "NumComplex128", "NumComplexExt"}

# LabVIEW data-type terminal text, exactly as the reference manual draws it
# (e.g. an orange `[DBL]` box for a DBL array — verified against the PDF).
# The numeric/Boolean/Path tokens are shared with ``LVType.type_descriptor()``'s
# faithful text label (see ``models._NUMERIC_TYPE_DESCRIPTOR``); String/Variant
# stay glyph-abbreviated here ("abc"/"Var") since this table drives compact
# terminal-icon text, not prose.
_TYPE_REPR = {
    **_NUMERIC_TYPE_DESCRIPTOR,
    "String": "abc",
    "Variant": "Var",
    "LVVariant": "Var",
}


_NUMERIC_TYPES = _INT_TYPES | _FLOAT_TYPES | _COMPLEX_TYPES

# An enum/ring is stored as an unsigned integer (the UnitUInt8/16/32 codes
# 0x15/0x16/0x17) and coerces to/from numerics AS that integer — so it takes a
# coercion dot against a differently-represented numeric exactly like a plain
# uint would. Map the underlying unit token to the equivalent numeric token so
# ``numeric_repr`` compares an enum by its width.
_ENUM_UNIT_TO_NUMERIC = {
    "UnitUInt8": "NumUInt8",
    "UnitUInt16": "NumUInt16",
    "UnitUInt32": "NumUInt32",
}


def numeric_repr(lv_type: LVType | None) -> str | None:
    """The numeric representation of a type (recursing into array elements),
    or None if it isn't numeric. A LabVIEW coercion dot appears ONLY when two
    wired terminals differ in numeric representation (I32 vs DBL) — NOT for
    structural differences like array↔element at an auto-indexing tunnel.

    An enum/ring counts as its underlying unsigned integer (``UnitUInt16`` ->
    ``NumUInt16``): LabVIEW treats an enum as a special number, so wiring one to
    a differently-sized numeric (e.g. a U16 enum into an I32 ``Initialize Array``
    dimension-size input) coerces and draws a dot, just like a plain uint would
    (issue #33)."""
    if lv_type is None:
        return None
    if lv_type.kind == LVTypeKind.ARRAY:
        return numeric_repr(lv_type.element_type)
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return _ENUM_UNIT_TO_NUMERIC.get(lv_type.underlying_type or "")
    if (
        lv_type.kind == LVTypeKind.PRIMITIVE
        and lv_type.underlying_type in _NUMERIC_TYPES
    ):
        return lv_type.underlying_type
    return None


def type_repr(lv_type: LVType | None) -> str:
    """The LabVIEW data-type terminal text for an LVType.

    Array → the element repr wrapped in one bracket pair per dimension
    (`[DBL]`, `[[I32]]`); scalars → `DBL`/`I32`/`TF`/`abc`/…; cluster/None → "".
    """
    if lv_type is None:
        return ""
    if lv_type.kind == LVTypeKind.ARRAY:
        dims = lv_type.dimensions or 1
        inner = type_repr(lv_type.element_type) or "?"
        return "[" * dims + inner + "]" * dims
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return "Enum"
    if lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        return ""  # clusters have no single-token repr
    if lv_type.kind == LVTypeKind.PRIMITIVE:
        # A class/DVR object refnum draws "Class"; a plain refnum (queue, event,
        # control ref, …) draws "Ref". (The full class name is the LARGE-form
        # label — see ``lv_type_label`` in this module.)
        if lv_type.underlying_type == "Refnum":
            return "Class" if lv_type.classname else "Ref"
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
    if lv_type.kind == LVTypeKind.ARRAY:
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
    "refnum": "wire_refnum",
}


def type_family(lv_type: LVType | None) -> str:
    """Coarse family bucket for an LVType: "float", "int", "bool",
    "string", "path", "enum", "cluster", "error_cluster", "variant",
    "refnum", "array", or "unknown". The single source of truth for wire color
    and front-panel terminal glyph choice.
    """
    if lv_type is None:
        return "unknown"
    if lv_type.kind == LVTypeKind.ARRAY:
        return "array"
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return "enum"
    if lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        return "error_cluster" if _is_error_cluster(lv_type) else "cluster"
    if lv_type.kind == LVTypeKind.PRIMITIVE:
        ut = lv_type.underlying_type or ""
        if ut in _FLOAT_TYPES or ut in _COMPLEX_TYPES:
            return "float"
        if ut in _INT_TYPES:
            return "int"
        if ut == "Boolean":
            return "bool"
        if ut in ("String", "SubString"):
            # "SubString" is a string subtype (e.g. Search/Split String's
            # outputs) — a string for color/glyph purposes, as the rest of the
            # codebase already treats it (graph.construction._TYPE_CATEGORIES,
            # type_defaults). Without this it fell through to grey "unknown".
            return "string"
        if ut == "Path":
            return "path"
        if ut in ("Variant", "LVVariant"):
            return "variant"
        if ut == "Refnum" and not lv_type.classname:
            # Generic refnum — VI reference, DAQmx/VISA driver session, queue,
            # notifier, control refnum, etc. LabVIEW draws all of these in the
            # same dark-green reference-wire color. An LVOOP class instance is
            # ALSO carried as a Refnum but has a ``classname`` (its wire is the
            # class's own colour, not the generic reference green), so exclude it.
            return "refnum"
    return "unknown"


def lv_type_label(lv_type: LVType | None) -> str:
    """A LabVIEW-faithful type name for tooltips + constant/terminal labels:
    ``Cluster``/``Error`` for clusters, ``[<elem>]`` (one bracket pair per
    dimension) for arrays, ``<Kind> Refnum`` for references, ``Enum``/``Variant``,
    and the LV scalar token (``DBL``/``I32``/``Boolean``/``String``/``Path``)
    otherwise. NOT the Python type — a cluster reads "Cluster", never
    ``dict[str, Any]``. Shared by draw.py (terminal type annotations) and
    nodes.py (a class/refnum CONSTANT's box label), so both name a class refnum
    the same way — by its class name, never a generic "Refnum"."""
    if lv_type is None:
        return "?"
    fam = type_family(lv_type)
    if fam == "error_cluster":
        return "Error"
    if fam == "cluster":
        return "Cluster"
    if fam == "array":
        dims = lv_type.dimensions or 1
        return "[" * dims + lv_type_label(lv_type.element_type) + "]" * dims
    if fam == "enum":
        return "Enum"
    if fam == "variant":
        return "Variant"
    ut = lv_type.underlying_type or ""
    if ut == "Refnum":
        # A class/DVR object refnum reads as the CLASS, never a generic "Refnum":
        # its short class name (lib qualifier stripped, ``.lvclass`` kept). Only a
        # non-class typed refnum (queue, event, control ref, …) keeps "<t> Refnum".
        if lv_type.classname:
            return lv_type.classname.rsplit(":", 1)[-1]
        return f"{lv_type.ref_type} Refnum" if lv_type.ref_type else "Refnum"
    if ut in ("Boolean", "String", "Path"):
        return ut
    return type_repr(lv_type) or ut or "?"


def wire_style(
    lv_type: LVType | None,
    theme: Theme = DEFAULT_THEME,
) -> WireStyle:
    """Color/width/style for a wire, from the SOURCE terminal's LVType — the ONE
    lookup every wire/tunnel/terminal drawing site calls.

    First it ASKS THE TYPE: a type that carries its OWN ``wire_style`` (a LabVIEW
    class, whose style is decoded from its ``.lvclass``) draws in that. Otherwise
    the per-family default: DBL orange, ints blue, bool green, string pink, path
    teal, cluster brown, error cluster dark; an array is thicker (by
    ``dimensions``, color inherited from the element type — so an array OF a class
    keeps the class color).
    """
    if lv_type is None:
        return WireStyle(theme.wire_default, _LINE_W)

    if lv_type.wire_style is not None:
        return lv_type.wire_style

    if lv_type.kind == LVTypeKind.ARRAY:
        inner = wire_style(lv_type.element_type, theme)
        return WireStyle(
            inner.color,
            inner.width + _ARRAY_W_PER_DIM * (lv_type.dimensions or 1),
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
