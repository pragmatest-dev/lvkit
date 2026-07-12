"""Node glyph resolver chain — the scalable node system (P2).

``resolve_glyph(node, ctx)`` tries an ORDERED list of resolvers; the first
one to return a non-``None`` ``Glyph`` wins. This is the whole extensibility
story: adding a new node's visual means ONE of —

1. Shipping the SubVI's own ``_ICON.png`` next to its ``.vi`` file — free,
   no code, no data change (``ExtractedIconResolver``).
2. Adding an ``icon`` field to a ``primitives.json`` / vilib JSON entry — a
   declaration, no code (``JsonGlyphResolver``).
3. Registering a new case in ``GeneratedGlyphResolver`` for a code-drawn
   built-in (arithmetic triangle, bracket, ...).
4. Doing nothing — ``FallbackBoxResolver`` always succeeds with a labeled
   box, so resolution can never fail.

Resolvers are a plain ordered list of instances (not a plugin framework —
there is nothing here that benefits from dynamic registration; the list in
this module IS the registration point).
"""

from __future__ import annotations

import ast
import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .._data import data_dir as _bundled_data_dir
from ..extractor import extract_vi_xml
from ..graph.core import InMemoryVIGraph
from ..graph.models import (
    AnyGraphNode,
    ConstantNode,
    FormulaNode,
    LocalVariableNode,
    PrimitiveNode,
    VINode,
)
from ..models import LVType
from ..primitive_resolver import NodeIcon
from ..primitive_resolver import get_resolver as get_prim_resolver
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .glyph import (
    ArithGlyph,
    BooleanConstantGlyph,
    BundleByNameGlyph,
    BundleGlyph,
    CenteredSvgGlyph,
    ClusterConstantGlyph,
    CompoundArithGlyph,
    ConstantGlyph,
    ErrorClusterGlyph,
    FormulaNodeGlyph,
    Glyph,
    IconImageGlyph,
    InlineSvgGlyph,
    LocalVariableGlyph,
    UnbundleGlyph,
    VariantGlyph,
    WrappedBoxGlyph,
)
from .style import int_byte_width, numeric_repr, type_family, wire_style

logger = logging.getLogger(__name__)

# A LabVIEW numeric constant's display-format string, verbatim off the
# DCO's ``numLabel/format`` XML field — a printf-style spec (LabVIEW's own
# "Display Format" dialog writes these). Only the plain forms
# ``%[flags][width].<precision><conv>`` are matched; anything else (e.g.
# LabVIEW's ``%<%.3X\n%x>T`` timestamp syntax, or the engineering-notation
# ``%#_13g`` seen once in the corpus with a non-numeric width token) is left
# for the caller to fall back on default formatting rather than guess at.
_NUMERIC_FORMAT_RE = re.compile(
    r"^%[#0\- +]*\d*\.(?P<prec>\d+)(?P<conv>[fFeEgGxXob])$"
)
# LabVIEW prefixes a non-decimal numeric constant with a lowercase letter —
# "x" for hex, "o" for octal, "b" for binary — never a "0x"/"0o"/"0b" style
# prefix (verified against the task's own example: U8 31 -> "x1F").
_RADIX_PREFIX = {"x": "x", "X": "x", "o": "o", "b": "b"}
_RADIX_FORMAT_SPEC = {"x": "X", "X": "X", "o": "o", "b": "b"}


def _format_numeric_const(
    lv_type: LVType | None, value: object, display_format: str | None,
) -> str | None:
    """Apply a numeric constant's DCO-provided display-format string to its
    decoded value: hex/octal/binary radix (with LabVIEW's lowercase x/o/b
    prefix, negative values two's-complemented to the type's bit width) or
    float precision (``%.Nf``/``%.Ng``/``%.Ne`` -> N digits).

    Returns None — caller falls back to the default decimal display —
    when there's no format string, or it doesn't match the plain printf
    spec this function understands (see ``_NUMERIC_FORMAT_RE``)."""
    if not display_format:
        return None
    m = _NUMERIC_FORMAT_RE.match(display_format)
    if not m:
        return None
    conv = m.group("conv")
    prec = int(m.group("prec"))
    try:
        fval = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

    if conv in _RADIX_PREFIX:
        ival = int(fval)
        width = int_byte_width(lv_type)
        if ival < 0:
            if width is None:
                # Can't two's-complement without a known bit width — don't
                # guess a width, fall back to default formatting instead.
                return None
            ival &= (1 << (width * 8)) - 1
        digits = format(ival, _RADIX_FORMAT_SPEC[conv])
        if len(digits) < prec:
            digits = digits.rjust(prec, "0")
        return _RADIX_PREFIX[conv] + digits

    # f/F/e/E/g/G — printf precision digits; Python's format mini-language
    # uses the identical conversion letters and semantics.
    return format(fval, f".{prec}{conv}")


def _format_const(value: object) -> str:
    """LabVIEW-style scalar constant text: a whole-valued float (or a numeric
    string like '0.0') shows with no trailing '.0'; non-whole and non-numeric
    values stringify as-is. Only call this for NUMERIC-typed constants."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            return value
        return str(int(f)) if f.is_integer() else value
    return str(value)


def string_const_display(raw: object) -> str:
    """DISPLAY text for a string constant. The parser stores the value as a
    Python string literal wrapped in single quotes with ``\\`` and ``'``
    escaped (parser/vi.py — ``f"'{escaped}'"``); codegen needs those quotes,
    but the diagram must show the bare text. Strip the surrounding quotes and
    unescape ``\\'`` -> ``'`` and ``\\\\`` -> ``\\`` (a single left-to-right
    scan, so escapes never re-corrupt each other). Real newlines in the value
    are untouched. Non-quoted values (defensive) pass through unchanged."""
    s = str(raw) if raw is not None else ""
    if len(s) < 2 or s[0] != "'" or s[-1] != "'":
        return s
    body = s[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] in ("'", "\\"):
            out.append(body[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_BOOL_TRUE_TOKENS = frozenset({"true", "t", "1", "yes", "on"})


def _bool_value(raw: object) -> bool:
    """Truthiness of a boolean constant's stored value — the parser may hand
    back a real ``bool``, or a string/number token ('True'/'T'/'1'/...)."""
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _BOOL_TRUE_TOKENS

# Arithmetic-primitive name/operation -> triangle symbol (moved here from
# the old draw.py dispatch dict — this IS "add a code-drawn built-in").
_ARITH_SYMBOL = {
    "Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷",
    "Increment": "+1", "Decrement": "-1",
}

# Comparison primitives — LabVIEW draws each as the same borderless triangle
# as an arithmetic op, only the interior symbol differs (see NI docs slugs
# equal/not-equal/greater/less/greater-or-equal/less-or-equal). Keyed by the
# resolved primitive NAME (primitives.json), the same way _ARITH_SYMBOL keys
# arithmetic — several type-variant prim_ids share one comparison name.
_COMPARE_SYMBOL = {
    "Equal?": "=", "Not Equal?": "≠", "Greater?": ">", "Less?": "<",
    "Greater Or Equal?": "≥", "Less Or Equal?": "≤",
}

# Cluster assemble/disassemble node classes. LabVIEW parses the palette Bundle
# and Unbundle functions (and their case/loop-boundary variants) as these
# "Node Multiplexer" classes; bundling vs unbundling is read from the FIELD
# (nmux_role=="list") terminals' direction — inputs = Bundle, outputs =
# Unbundle. The AGGREGATE (nmux_role=="agg") terminal(s) are never counted.
_CLUSTER_MUX_TYPES = frozenset({"nMux", "mux", "demux"})

# node_type -> (bundle name, unbundle name), for when direction can't be
# determined from a ``list``-role terminal (defensive fallback only).
_MUX_TYPE_DEFAULT_NAMES = {
    "nMux": "Bundle/Unbundle By Name",
    "mux": "Bundle",
    "demux": "Unbundle",
}

_DEFAULT_ICON_SIZE = (24, 24)


def mux_display_name(node: AnyGraphNode) -> str:
    """Human name for an ``nMux``/``mux``/``demux`` node — never the internal
    "Node Multiplexer"/"Multiplexer"/"Demultiplexer" XML-class jargon.
    Direction is read from the FIELD (``nmux_role=="list"``) terminals, the
    same rule the glyph resolver uses: fields are inputs -> Bundle, fields
    are outputs -> Unbundle. Falls back to a type-based default if the node
    has no field terminals (defensive; shouldn't happen for a real mux)."""
    node_type = node.node_type
    field_terms = [t for t in node.terminals if t.nmux_role == "list"]
    if field_terms:
        bundling = field_terms[0].direction == "input"
        if node_type == "nMux":
            return "Bundle By Name" if bundling else "Unbundle By Name"
        return "Bundle" if bundling else "Unbundle"
    if node_type is None:
        return "Bundle/Unbundle By Name"
    return _MUX_TYPE_DEFAULT_NAMES.get(node_type, "Bundle/Unbundle By Name")


@dataclass(frozen=True)
class GlyphContext:
    """Context available to node glyph resolvers.

    Deliberately small: resolvers work off the graph node itself plus the
    owning graph/VI (to look up a SubVI's own source path). They never see
    the ``Scene``/``RenderNode`` or heap geometry — a glyph's shape doesn't
    depend on where it sits on the diagram.
    """

    graph: InMemoryVIGraph
    vi_name: str


class NodeGlyphResolver(Protocol):
    """One link in the resolver chain. Return ``None`` to fall through."""

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None: ...


@functools.lru_cache(maxsize=256)
def _vectorized_icon(path_str: str, mtime: float) -> CenteredSvgGlyph | None:
    """Vectorize a SubVI's ``_ICON.png`` into an SVG glyph, cached by path +
    mtime so repeated renders don't re-vectorize an unchanged icon (and an
    edited icon re-vectorizes since ``mtime`` is part of the cache key)."""
    from .icons import png_to_svg  # noqa: PLC0415 - optional dependency, guarded

    try:
        data = Path(path_str).read_bytes()
    except OSError:
        return None
    result = png_to_svg(data)
    if result is None:
        return None
    fragment, size = result
    return CenteredSvgGlyph(fragment, size)


class ExtractedIconResolver:
    """Best-effort real SubVI ``_ICON.png`` for VINode subVI calls.

    The caller's heap XML only carries the CALLER's own icon (drawn as the
    corner decoration in ``draw_scene``) — a subVI's icon requires the
    subVI's own file. This resolver only uses what's cheaply already known:

    - ``graph.get_vi_source_path(name)``, if the subVI happens to already
      be loaded in the same graph (free — a dict lookup); or
    - ``node.qualified_path``, if it happens to already be a literal,
      existing file path (rare: today's ``qualified_path`` values are raw
      LabVIEW path TOKENS like ``"<vilib>/Utility/error.llb/Foo.vi"``, not
      resolved filesystem paths — resolving the ``<vilib>``/``<userlib>``
      tokens would need a library root the graph doesn't expose publicly;
      see the JUDGMENT CALL note in this module's tests / the P2 report).

    If neither is available, it returns ``None`` (fall through) rather than
    loading/parsing the subVI's own graph — that would be exactly the
    "force-load subVIs expensively" this resolver must avoid. Extracting
    the (cached) heap XML of an already-located file is still a subprocess
    call on a cache miss, so every failure mode here is wrapped and
    fail-soft: an icon is a decoration, never a reason to fail rendering.
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if not isinstance(node, VINode):
            return None
        name = node.name
        if not name:
            return None

        src_path = ctx.graph.get_vi_source_path(name)
        if src_path is None and node.qualified_path:
            candidate = Path(node.qualified_path)
            if candidate.is_file():
                src_path = candidate
        if src_path is None:
            return None

        try:
            bd_xml, _, _ = extract_vi_xml(src_path)
        except Exception:
            logger.debug(
                "subVI icon extraction failed for %r (%s)", name, src_path,
                exc_info=True,
            )
            return None

        icon_path = bd_xml.parent / f"{bd_xml.stem.replace('_BDHb', '')}_ICON.png"
        if not icon_path.is_file():
            return None
        glyph = _vectorized_icon(str(icon_path), icon_path.stat().st_mtime)
        if glyph is not None:
            return glyph
        return IconImageGlyph(icon_path)


class JsonGlyphResolver:
    """The optional, declarative ``icon`` field on a primitive/vilib entry.

    Mirrors the exact lookup order ``graph/construction.py`` already uses
    to resolve these nodes semantically (prim_id/name, then node_type for
    primitives; poly-variant then plain name for VI calls) — so "does this
    node have a declared icon" asks the same question codegen already
    answers, just adding one optional field to the result.
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        icon: NodeIcon | None = None
        if isinstance(node, PrimitiveNode):
            icon = self._primitive_icon(node)
        elif isinstance(node, VINode):
            icon = self._vi_icon(node)
        if icon is None:
            return None
        return self._glyph_from_icon(icon)

    @staticmethod
    def _primitive_icon(node: PrimitiveNode) -> NodeIcon | None:
        resolver = get_prim_resolver()
        resolved = resolver.resolve(prim_id=node.prim_id, name=node.name)
        if resolved is not None and resolved.icon is not None:
            return resolved.icon
        if node.node_type:
            nt_resolved = resolver.resolve_by_node_type(node.node_type)
            if nt_resolved is not None:
                return nt_resolved.icon
        return None

    @staticmethod
    def _vi_icon(node: VINode) -> NodeIcon | None:
        resolver = get_vilib_resolver()
        entry = None
        if node.poly_variant_name and node.name:
            entry = resolver.resolve_poly_variant(node.name, node.poly_variant_name)
        if entry is None and node.name:
            entry = resolver.resolve_by_name(node.name)
        return entry.icon if entry is not None else None

    @staticmethod
    def _glyph_from_icon(icon: NodeIcon) -> Glyph | None:
        if icon.svg is not None:
            return InlineSvgGlyph(icon.svg, icon.size or _DEFAULT_ICON_SIZE)
        if icon.file is not None:
            path = _bundled_data_dir() / "glyphs" / icon.file
            try:
                fragment = path.read_text()
            except OSError:
                logger.debug("glyph asset not found: %s", path)
                return None
            return InlineSvgGlyph(fragment, icon.size or _DEFAULT_ICON_SIZE)
        return None


def _bundle_by_name_glyph(
    node: PrimitiveNode, graph: InMemoryVIGraph,
) -> BundleByNameGlyph | None:
    """A Bundle/Unbundle-By-Name glyph for an ``nMux`` node, with each accessed
    field's NAME resolved from the wired cluster's type. The ``agg`` terminal is
    the cluster; each ``list`` terminal carries an ``nmux_field_index`` into that
    field list. ``bundling`` is True when the cluster is the OUTPUT (fields in →
    cluster out). Field names come from ``graph.get_type_fields()`` — the ONE API
    for both anonymous clusters (fields inline on the terminal) and named/class
    types (fields only in dep_graph, e.g. an LVOOP private-data cluster). Returns
    None if no field names are resolvable (caller falls back to the compact glyph).
    """
    agg = next((t for t in node.terminals if t.nmux_role == "agg"), None)
    if agg is None or agg.lv_type is None:
        return None
    fields = graph.get_type_fields(agg.lv_type) or []
    if not fields:
        return None
    field_terms = sorted(
        (t for t in node.terminals if t.nmux_role == "list"), key=lambda t: t.index,
    )
    if not field_terms:
        return None
    names: list[str] = []
    for t in field_terms:
        fi = t.nmux_field_index
        if fi is not None and 0 <= fi < len(fields) and fields[fi].name:
            names.append(fields[fi].name)
        else:
            names.append(t.name or (f"field {fi}" if fi is not None else "?"))
    # Direction comes from the FIELD terminals, not the aggregate — there can
    # be TWO aggregate terminals (an input source cluster and an output
    # assembled cluster) sharing one DCO, so ``agg.direction`` is ambiguous.
    bundling = field_terms[0].direction == "input"
    return BundleByNameGlyph(names=tuple(names), bundling=bundling)


class OriginalGlyphResolver:
    """Clean-room ORIGINAL glyphs for primitives whose SHAPE we draw ourselves
    (roadmap #14). Each matched primitive gets a glyph that reproduces the real
    LabVIEW outline + footprint (looked up from NI's function-reference images)
    with OUR OWN interior symbol — never NI's pixel artwork. Placed FIRST in the
    resolver list; every primitive NOT listed here returns ``None`` and falls
    through to the remaining resolvers (a subVI's own icon, a declared inline
    SVG, procedural shapes, or the labeled-box fallback). The NI-derived
    PDF-icon path was removed for licensing, so an un-migrated primitive now
    renders as a labeled box rather than pixel-matched NI art.

    Migrated so far:

    - Bundle / Unbundle (``nMux``/``mux``/``demux`` "Node Multiplexer" classes,
      disambiguated by FIELD (``nmux_role=="list"``) terminal direction) —
      ``BundleGlyph`` / ``UnbundleGlyph``.
    - The six comparison functions (Equal?, Not Equal?, Greater?, Less?,
      Greater Or Equal?, Less Or Equal?) — the arithmetic ``ArithGlyph``
      triangle with the comparison symbol.
    - Build Array (``aBuild``) — the name in a box, replacing the noisy
      vectorized NI pixel icon (no distinctive clean-room shape yet).
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if not isinstance(node, PrimitiveNode):
            return None
        if node.node_type in _CLUSTER_MUX_TYPES:
            return self._cluster_glyph(node, ctx.graph)
        symbol = _COMPARE_SYMBOL.get(node.name or "")
        if symbol is not None:
            return ArithGlyph(symbol)
        if node.node_type == "aBuild" or node.name == "Build Array":
            # No distinctive clean-room shape yet; the vectorized NI pixel icon
            # read as a noisy little grid. The name in a box is clearer and
            # matches the neighbouring text-box prims (Add Array Elements,
            # Random Number). Swap in a real glyph here later.
            return WrappedBoxGlyph(
                "Build Array", "prim_fill", "prim_stroke", 1.0,
                text_attr="prim_text",
            )
        return None

    @staticmethod
    def _cluster_glyph(node: PrimitiveNode, graph: InMemoryVIGraph) -> Glyph | None:
        # nMux is Bundle/Unbundle BY NAME — a box with the accessed field NAMES,
        # resolved from the wired cluster's type (mux/demux are the compact,
        # positional Bundle/Unbundle handled by field count below).
        if node.node_type == "nMux":
            named = _bundle_by_name_glyph(node, graph)
            if named is not None:
                return named
        # FIELD terminals (nmux_role=="list") are the real payload count — the
        # AGGREGATE terminal(s) (nmux_role=="agg", up to two: an input source
        # cluster and an output assembled cluster sharing one DCO) are NOT
        # fields and must never be counted. Direction comes from the fields,
        # not the aggregate, for the same reason. N=1 is not special-cased.
        field_terms = [t for t in node.terminals if t.nmux_role == "list"]
        num_fields = len(field_terms)
        if num_fields == 0:
            # No field terminals at all — an invisible SR/tunnel muxer, not an
            # assemble/disassemble. Leave it to the existing rendering.
            return None
        bundling = field_terms[0].direction == "input"
        if bundling:
            return BundleGlyph(num_fields=num_fields)
        return UnbundleGlyph(num_fields=num_fields)


# cpdArith operation -> Boolean-context translation. Same mapping codegen
# applies in ``codegen/nodes/compound.py::generate_compound_arith`` — a
# Boolean compound-arith's "add" is a logical OR and "multiply" is a
# logical AND, not the raw arithmetic operator.
_CPD_ARITH_BOOLEAN_OP = {"add": "or", "multiply": "and"}


def _cpd_arith_boolean(node: PrimitiveNode) -> bool:
    """Whether ``node`` (a cpdArith primitive) operates on Booleans — true if
    its output OR any input terminal carries a Boolean type. Mirrors
    ``codegen/nodes/compound.py::_is_boolean``/its use in
    ``generate_compound_arith``, so the glyph and the generated code agree."""
    return any(
        t.lv_type is not None and t.lv_type.underlying_type == "Boolean"
        for t in node.terminals
    )


def _cpd_arith_operation(node: PrimitiveNode) -> str:
    """The operator symbol key to feed ``CompoundArithGlyph`` for a cpdArith
    node: ``node.operation`` translated to its Boolean-context equivalent
    (add->or, multiply->and) when the node's terminals are Boolean."""
    operation = node.operation or "or"
    if _cpd_arith_boolean(node):
        return _CPD_ARITH_BOOLEAN_OP.get(operation, operation)
    return operation


def _enum_const_name(lv_type: LVType | None, raw: object) -> str:
    """An enum/ring constant's item NAME for its stored value, or the raw value
    as text when the members aren't known (or the value is out of range). The
    type already tells us it's an enum — show the string, not the ordinal."""
    text = str(raw) if raw is not None else ""
    values = getattr(lv_type, "values", None)
    if not values or raw is None:
        return text
    try:
        idx = int(str(raw))
    except (ValueError, TypeError):
        return text
    return next((name for name, ev in values.items() if ev.value == idx), text)


def _leaf_const_glyph(
    lv_type: LVType | None, raw: object, display_format: str | None = None,
) -> Glyph:
    """One non-cluster constant's glyph, from its type + raw value. Shared by
    top-level constants and by each field of a composed cluster constant.

    ``display_format`` is the constant's own DCO-provided printf-style
    numeric display-format string (top-level constants only for now — a
    cluster constant's individual FIELDS carry their own format too, but
    extracting those isn't implemented, so field callers pass None and get
    the default decimal display; see ``ParsedConstant.display_format``)."""
    fam = type_family(lv_type)
    if fam == "variant":
        return VariantGlyph()
    if fam == "bool":
        return BooleanConstantGlyph(_bool_value(raw))
    color = wire_style(lv_type).color
    if fam == "enum":
        # We know the type is an enum: show the item-name string, keeping the
        # enum/numeric wire color the box already uses (wire_int). An unset
        # cluster field (raw None) is the first item (ordinal 0), not "None".
        name = _enum_const_name(lv_type, 0 if raw is None else raw)
        return ConstantGlyph(name, color)
    if numeric_repr(lv_type) is not None:
        # An unset numeric cluster field shows its default 0, never "None".
        value_raw = 0 if raw is None else raw
        value = (
            _format_numeric_const(lv_type, value_raw, display_format)
            or _format_const(value_raw)
        )
    elif fam == "string":
        # Show the bare text (quotes/escapes are a codegen artifact); empty
        # for an unset field.
        value = string_const_display(raw) if raw is not None else ""
    else:
        value = str(raw) if raw is not None else ""
    # String constants word-wrap to fill their (already content-sized) box.
    return ConstantGlyph(value or "", color, multiline=fam == "string")


def _cluster_field_values(value: object) -> dict[str, object]:
    """A cluster constant's field values as a dict. The graph stores the value
    as its Python ``repr`` string (e.g. ``"{'status': True, 'code': 17}"``); parse
    it back the same way ``graph.describe`` does (``ast.literal_eval``)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        # The repr can carry non-literal tokens like ``Variant()`` for a variant
        # field, which ast.literal_eval rejects — dropping the WHOLE dict and
        # blanking every field. Neutralize those to None (a variant field draws
        # from its TYPE anyway) so the other fields still parse.
        for candidate in (value, re.sub(r"\b[A-Z]\w*\(\)", "None", value)):
            try:
                parsed = ast.literal_eval(candidate)
            except (ValueError, SyntaxError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _cluster_const_glyph(node: ConstantNode, is_error: bool) -> Glyph | None:
    """Compose a cluster constant from its fields' own leaf glyphs. None when
    the cluster type carries no field info (nothing to compose from)."""
    fields = getattr(node.lv_type, "fields", None) or []
    if not fields:
        return None
    values = _cluster_field_values(node.value)
    composed = tuple(
        (f.name, _leaf_const_glyph(f.type, values.get(f.name))) for f in fields
    )
    summary = "\n".join(
        f"{f.name}: {_field_summary_value(f.type, values.get(f.name))}"
        for f in fields
    )
    return ClusterConstantGlyph(composed, is_error=is_error, value_summary=summary)


def _field_summary_value(lv_type: LVType | None, raw: object) -> str:
    """A cluster field's value as short text for the hover tooltip — mirrors
    what ``_leaf_const_glyph`` draws (type defaults for an unset field)."""
    fam = type_family(lv_type)
    if fam == "bool":
        return "True" if _bool_value(raw) else "False"
    if fam == "variant":
        return "Variant"
    if fam == "enum":
        return _enum_const_name(lv_type, 0 if raw is None else raw)
    if numeric_repr(lv_type) is not None:
        return _format_const(0 if raw is None else raw)
    if fam == "string":
        return string_const_display(raw) if raw is not None else ""
    return str(raw) if raw is not None else ""


class GeneratedGlyphResolver:
    """Code-drawn built-ins — migrated from the old ``draw.py`` dispatch
    dict. This is where "adding a visual" still means writing a branch,
    reserved for shapes that are cheap to describe procedurally and don't
    warrant a hand-authored SVG asset (arithmetic triangles, brackets)."""

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if isinstance(node, PrimitiveNode):
            return self._primitive_glyph(node)
        if isinstance(node, VINode):
            # No custom icon: draw the VI name wrapped inside the box (up to 4
            # lines), LabVIEW's default no-icon subVI look — see WrappedBoxGlyph.
            return WrappedBoxGlyph(node.name or "")
        if isinstance(node, ConstantNode):
            fam = type_family(node.lv_type)
            if fam in ("cluster", "error_cluster"):
                composed = _cluster_const_glyph(node, is_error=fam == "error_cluster")
                if composed is not None:
                    return composed
                # No field info to compose from — keep the old schematic.
                if fam == "error_cluster":
                    return ErrorClusterGlyph()
            raw = node.raw_value if node.value is None else node.value
            return _leaf_const_glyph(node.lv_type, raw, node.display_format)
        if isinstance(node, FormulaNode):
            return FormulaNodeGlyph(node.script or "")
        if isinstance(node, LocalVariableNode):
            # LabVIEW's Local Variable glyph: the referenced control's NAME in a
            # box marked by a ▶ badge (so it's not mistaken for a constant box),
            # with a read=thick / write=thin border.
            return LocalVariableGlyph(
                node.control_name or node.name or "Local Variable",
                is_write=node.is_write,
            )
        return None

    @staticmethod
    def _primitive_glyph(node: PrimitiveNode) -> Glyph:
        if node.node_type == "cpdArith":
            num_inputs = sum(1 for t in node.terminals if t.direction == "input")
            return CompoundArithGlyph(
                _cpd_arith_operation(node), num_inputs=max(1, num_inputs),
            )
        sym = _ARITH_SYMBOL.get(node.operation or node.name or "")
        if sym:
            return ArithGlyph(sym)
        # Unresolved primitive: show a compact "#<prim_id>" rather than the
        # verbose "unknown_primitive_N" placeholder (the hover still lists the
        # terminals we do know). Display-only — node.name is left for codegen.
        if node.prim_id is not None and (
            node.name == f"unknown_primitive_{node.prim_id}"
        ):
            return WrappedBoxGlyph(
                f"#{node.prim_id}", "prim_fill", "prim_stroke", 1.0,
                text_attr="prim_text",
            )
        # No icon yet: wrap the primitive's name inside the box (up to 4 lines,
        # adaptive font) — same treatment as an icon-less subVI.
        return WrappedBoxGlyph(
            node.name or "?", "prim_fill", "prim_stroke", 1.0, text_attr="prim_text",
        )


class FallbackBoxResolver:
    """The labeled box. ALWAYS returns a ``Glyph`` — resolution can't fail."""

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph:
        label = node.name or node.node_type or "?"
        return WrappedBoxGlyph(
            label, "prim_fill", "prim_stroke", 1.0, text_attr="prim_text",
        )


# The registration point: an ordered list, tried in order, first hit wins.
# Add a resolver here to extend the mechanism; add an icon/asset/JSON entry
# to extend WITHOUT touching this list at all.
_RESOLVERS: list[NodeGlyphResolver] = [
    # Clean-room ORIGINAL glyphs win over every NI-derived asset (roadmap #14):
    # it returns None for any primitive it hasn't migrated, so un-migrated prims
    # still fall through to the icon resolvers below and never regress.
    OriginalGlyphResolver(),
    ExtractedIconResolver(),
    JsonGlyphResolver(),
    GeneratedGlyphResolver(),
    FallbackBoxResolver(),
]


def resolve_glyph(node: AnyGraphNode, ctx: GlyphContext) -> Glyph:
    """Resolve ``node``'s visual via the ordered resolver chain.

    Always returns a ``Glyph`` — the last resolver (``FallbackBoxResolver``)
    never returns ``None``.
    """
    for resolver in _RESOLVERS:
        glyph = resolver.resolve(node, ctx)
        if glyph is not None:
            return glyph
    raise AssertionError("FallbackBoxResolver must always return a Glyph")
