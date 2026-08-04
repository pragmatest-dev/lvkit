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
    EventStructureNode,
    FormulaNode,
    LocalVariableNode,
    PrimitiveNode,
    VINode,
)
from ..graph.op_walk import _nmux_field_sources, _resolve_nmux_field_name
from ..models import ClusterField, LVType, Terminal, bundle_unbundle_name
from ..num_format import format_numeric_const as _format_numeric_const
from ..parser.constants import NMUX_BY_NAME_NODE_CLASSES
from ..primitive_resolver import NodeIcon
from ..primitive_resolver import get_resolver as get_prim_resolver
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .glyph import (
    ArithGlyph,
    ArrayBuildGlyph,
    ArrayReverseGlyph,
    ArraySearchGlyph,
    ArraySizeGlyph,
    ArraySortGlyph,
    ArraySplitGlyph,
    BooleanConstantGlyph,
    BooleanGateGlyph,
    BundleByNameGlyph,
    BundleGlyph,
    CenteredSvgGlyph,
    ClusterConstantGlyph,
    CompoundArithGlyph,
    ConstantGlyph,
    ControlRefConstGlyph,
    ConvertGlyph,
    ErrorClusterGlyph,
    EventDataGlyph,
    FormulaNodeGlyph,
    Glyph,
    IconImageGlyph,
    InlineSvgGlyph,
    InPlaceElementGlyph,
    InvokeNodeGlyph,
    LocalVariableGlyph,
    PropertyNodeGlyph,
    UnbundleGlyph,
    VariantGlyph,
    WrappedBoxGlyph,
)
from .style import lv_type_label, numeric_repr, type_family, type_repr, wire_style

logger = logging.getLogger(__name__)

# Numeric-constant radix/precision formatting lives in ``lvkit.num_format`` now,
# shared with the graph/netlist text output so render and describe render the
# same way; imported above as ``_format_numeric_const``.


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
#
# The four array-REDUCTION primitives (And/Or/Add/Multiply Array Elements)
# are drawn with the SAME borderless ``ArithGlyph`` triangle as scalar
# arithmetic — LabVIEW's own function-reference images show them as a plain
# triangle with a symbol, not a gate shape (goal #99) — and ``Select``
# (the ternary s?t:f primitive, prim_id 1516) likewise: a right-pointing
# triangle with a small "?" (its "s?" selector), not the case-structure-like
# ``node_type=="select"`` used internally for In-Place-Element decompose/
# recompose (unrelated construct — see ``graph/builders/structures.py``).
_ARITH_SYMBOL = {
    "Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷",
    "Increment": "+1", "Decrement": "-1",
    "And Array Elements": "∧", "Or Array Elements": "∨",
    "Add Array Elements": "+", "Multiply Array Elements": "×",
    "Select": "?",
}

# Comparison primitives — LabVIEW draws each as the same borderless triangle
# as an arithmetic op, only the interior symbol differs (see NI docs slugs
# equal/not-equal/greater/less/greater-or-equal/less-or-equal). Keyed by the
# resolved primitive NAME (primitives.json), the same way _ARITH_SYMBOL keys
# arithmetic — several type-variant prim_ids share one comparison name.
_COMPARE_SYMBOL = {
    "Equal?": "=", "Not Equal?": "≠", "Greater?": ">", "Less?": "<",
    "Greater Or Equal?": "≥", "Less Or Equal?": "≤",
    # Comparison-to-0 variants (LabVIEW draws the same triangle with a "0").
    "Equal To 0?": "=0", "Not Equal To 0?": "≠0",
    "Greater Than 0?": ">0", "Less Than 0?": "<0",
    "Greater Or Equal To 0?": "≥0", "Less Or Equal To 0?": "≤0",
}

# Boolean logic-GATE primitives (goal #99) -> (BooleanGateGlyph kind, interior
# symbol, negated-output-bubble, negated-input-bubble). Keyed by the resolved
# primitive NAME, same convention as ``_COMPARE_SYMBOL``. Only names actually
# present in ``primitives.json`` fire: And (1062), Or (1061), Not (1064).
# Exclusive Or / Not And / Not Or / Not Exclusive Or / Implies have no
# confirmed primResID yet, so they still fall through to the labeled-box
# fallback until a future primitive-resolution pass adds them.
_BOOLEAN_GATE = {
    "And": ("and", "∧", False, False),
    "Or": ("or", "∨", False, False),
    "Not": ("not", "¬", False, True),
}

# ARRAY family (goal #14 follow-up) -> a fixed, stateless glyph instance,
# keyed by the resolved primitive NAME (same convention as
# ``_COMPARE_SYMBOL``/``_BOOLEAN_GATE``). Build Array (``aBuild``) is NOT
# here — it grows with its input count, so it's constructed per-node in
# ``resolve()`` instead of shared as one instance.
_ARRAY_GLYPH: dict[str, Glyph] = {
    "Array Size": ArraySizeGlyph(),
    "Reverse 1D Array": ArrayReverseGlyph(),
    "Search 1D Array": ArraySearchGlyph(),
    "Sort 1D Array": ArraySortGlyph(),
    "Split 1D Array": ArraySplitGlyph(),
}

# ARRAY-family glyphs (the dict above + Build Array's ``ArrayBuildGlyph``) are
# gated OFF: the compositional array-assembly drawings read poorly and Build
# Array did not expand its element rows to the wired input count (4 wires
# landing on a 3-row glyph). With this False they fall back to the labeled box
# (primitive name + its real terminals). Flip to True to restore the glyphs
# once expand-to-wired-count is fixed.
_ARRAY_GLYPHS_ENABLED = False

# CONVERTER family (goal #14 follow-up): target-type abbreviation shown by
# ``ConvertGlyph``, keyed by the resolved primitive NAME.
_CONVERTER_ABBR = {
    "To Long Integer": "I32",
    "To Word Integer": "I16",
    "To Byte Integer": "I8",
    "To Unsigned Byte Integer": "U8",
    "To Unsigned Word Integer": "U16",
    "To Double Precision Float": "DBL",
    "Boolean To (0,1)": "0,1",
}

# Cluster assemble/disassemble node classes. LabVIEW parses the palette Bundle
# and Unbundle functions (and their case/loop-boundary variants) as these
# "Node Multiplexer" classes; bundling vs unbundling is read from the FIELD
# (nmux_role=="list") terminals' direction — inputs = Bundle, outputs =
# Unbundle. The AGGREGATE (nmux_role=="agg") terminal(s) are never counted.
#
# ``eventDataNode`` (an Event Structure's Event Data Node / Event Filter Node
# — same heap class for both) is structurally identical to nMux (dcoAgg +
# named dcoList/<i> fields via nmxDCO terminal DCOs, see
# parser/node_types.py::_EventDataNodeHandler) so it shares the same
# named-rows glyph instead of the old generic-fallback giant box.
#
# ``decomposeClusterNode`` (the In Place Element Structure's cluster border
# node, parser/node_types.py::DecomposeClusterHandler) is ALSO structurally
# identical — same dcoAgg/dcoList shape, same ``<poser>``-linked decompose/
# recompose halves accessing fields BY NAME (confirmed: real corpus field
# indices are non-sequential, e.g. {7, 1, 9, 3} — positional access would be
# 0..N-1). It shares nMux's Bundle/Unbundle BY NAME treatment, not the
# compact positional mux/demux one. The internal "decompose" heap jargon must
# never reach a user-facing label — see ``mux_display_name`` below.
_CLUSTER_MUX_TYPES = frozenset(
    {"nMux", "mux", "demux", "eventDataNode", "decomposeClusterNode"},
)

# node_type -> (bundle name, unbundle name), for when direction can't be
# determined from a ``list``-role terminal (defensive fallback only).
_MUX_TYPE_DEFAULT_NAMES = {
    "nMux": "Bundle/Unbundle By Name",
    "mux": "Bundle",
    "demux": "Unbundle",
    "eventDataNode": "Event Data",
    "decomposeClusterNode": "Bundle/Unbundle By Name",
}

# node_types whose fields are accessed BY NAME (nMux's own "Node Multiplexer"
# class, and the IPES cluster border node) rather than positionally (mux/
# demux) — see ``_CLUSTER_MUX_TYPES`` docstring above. Shared with
# ``graph.op_walk.stamp_nmux_lane_names`` (the netlist/diff/describe seam)
# via ``parser.constants.NMUX_BY_NAME_NODE_CLASSES`` so render and the
# graph-layer resolver can never disagree on which node classes get
# field-name (not positional-index) treatment.
_BY_NAME_MUX_TYPES = NMUX_BY_NAME_NODE_CLASSES

_DEFAULT_ICON_SIZE = (24, 24)


def mux_display_name(node: AnyGraphNode) -> str:
    """Human name for an ``nMux``/``mux``/``demux``/``decomposeClusterNode``
    node — never the internal "Node Multiplexer"/"Multiplexer"/
    "Demultiplexer"/"Decompose Cluster" XML-class jargon. Direction is read
    from the FIELD (``nmux_role=="list"``) terminals, the same rule the glyph
    resolver uses: fields are inputs -> Bundle, fields are outputs ->
    Unbundle. Falls back to a type-based default if the node has no field
    terminals (defensive; shouldn't happen for a real mux)."""
    node_type = node.node_type
    # An Event Data/Filter Node isn't a real Bundle/Unbundle — it's always
    # "Event Data" regardless of field direction (the Filter Node's fields
    # can be writable, unlike a genuine Unbundle's read-only outputs).
    if node_type == "eventDataNode":
        return "Event Data"
    name = bundle_unbundle_name(
        node.terminals, by_name=node_type in _BY_NAME_MUX_TYPES,
    )
    if name is not None:
        return name
    if node_type is None:
        return "Bundle/Unbundle By Name"
    return _MUX_TYPE_DEFAULT_NAMES.get(node_type, "Bundle/Unbundle By Name")


# NI docs pages for the cluster-mux family. nMux/mux/demux carry no primResID
# (the node-type primitive flavor) AND are direction-polymorphic — one node_type
# resolves to Bundle vs Unbundle by field direction — so a single node_type->url
# entry can't express them. Their doc link keys off the resolved DISPLAY name.
_MUX_DOC_BASE = (
    "https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/functions/"
)
_MUX_DOC_URL = {
    "Bundle By Name": _MUX_DOC_BASE + "bundle-by-name.html",
    "Unbundle By Name": _MUX_DOC_BASE + "unbundle-by-name.html",
    "Bundle": _MUX_DOC_BASE + "bundle.html",
    "Unbundle": _MUX_DOC_BASE + "unbundle.html",
}


def mux_doc_url(node: AnyGraphNode) -> str | None:
    """NI docs URL for a cluster-mux node, keyed by its resolved display name
    (Bundle vs Unbundle is direction-dependent — the raw node name can't tell
    them apart). None for the ambiguous fallback name (direction undetermined)."""
    return _MUX_DOC_URL.get(mux_display_name(node))


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


@dataclass(frozen=True)
class SubVISource:
    """A SubVI VINode's resolved on-disk ``.vi`` source, plus whether it was
    found in a PROJECT-local location (an already-loaded VI, a search-path
    filename match, or a literal existing ``qualified_path``) as opposed to
    the user's own LOCAL vi.lib/user.lib install (``resolve_library_vi_path``).

    The distinction matters to any consumer that emits a portable reference
    to the path — e.g. render/scene.py's click-navigation
    ``data-lv-vi-rel`` attribute (task #76): a vi.lib path is a system
    location on THIS machine, never something a rendered/shared SVG should
    point at.
    """

    path: Path
    project_local: bool


def resolve_subvi_source(
    node: VINode, graph: InMemoryVIGraph,
) -> SubVISource | None:
    """Resolve a SubVI VINode's own on-disk ``.vi`` file — the ONE lookup
    chain shared by both SubVI icon resolution (``ExtractedIconResolver``)
    and click-navigation (render/scene.py's ``data-lv-vi-rel``), so both
    features ask the identical question rather than each inventing its own.

    Tries, in order:

    - ``graph.locate_vi_file(name)``: an already-loaded source (free — a
      dict lookup) or a filename search of the graph's search paths —
      PROJECT-local either way.
    - ``node.qualified_path``, if it happens to already be a literal,
      existing file path (rare: today's ``qualified_path`` values are raw
      LabVIEW path TOKENS like ``"<vilib>/Utility/error.llb/Foo.vi"``, not
      resolved filesystem paths — resolving the ``<vilib>``/``<userlib>``
      tokens would need a library root the graph doesn't expose publicly;
      see the JUDGMENT CALL note in this module's tests / the P2 report) —
      PROJECT-local.
    - ``graph.resolve_library_vi_path(node.qualified_path)``: the user's
      LOCAL vi.lib/user.lib install — NOT project-local (a system path).

    Returns ``None`` (fall through) rather than loading/parsing the subVI's
    own graph — that would be exactly the "force-load subVIs expensively"
    this lookup must avoid; it is a path lookup only.
    """
    name = node.name
    if not name:
        return None

    src_path = graph.locate_vi_file(name)
    if src_path is not None:
        return SubVISource(src_path, project_local=True)
    if node.qualified_path:
        candidate = Path(node.qualified_path)
        if candidate.is_file():
            return SubVISource(candidate, project_local=True)
    # A user's LOCAL vi.lib/user.lib (their own licensed install): resolve
    # the <vilib>/<userlib> token to a real on-disk .vi. Rendering it
    # locally is not distribution; lvkit never ships this art, and a hosted
    # service has no roots set so this stays None. Fail-soft to name-box.
    src_path = graph.resolve_library_vi_path(node.qualified_path)
    if src_path is not None:
        return SubVISource(src_path, project_local=False)
    return None


class ExtractedIconResolver:
    """Best-effort real SubVI ``_ICON.png`` for VINode subVI calls.

    The caller's heap XML only carries the CALLER's own icon (parsed into
    ``layout.icon_png`` but NOT currently drawn anywhere) — a subVI's icon
    requires the subVI's own file. Source-path resolution is shared with
    click-navigation via ``resolve_subvi_source`` (see its docstring for the
    lookup chain).

    If nothing resolves, this returns ``None`` (fall through) rather than
    loading/parsing the subVI's own graph — that would be exactly the
    "force-load subVIs expensively" this resolver must avoid. Extracting
    the (cached) heap XML of an already-located file is still a subprocess
    call on a cache miss, so every failure mode here is wrapped and
    fail-soft: an icon is a decoration, never a reason to fail rendering.
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if not isinstance(node, VINode):
            return None
        resolved = resolve_subvi_source(node, ctx.graph)
        if resolved is None:
            return None
        src_path = resolved.path

        try:
            bd_xml, _, _ = extract_vi_xml(src_path)
        except Exception:
            logger.debug(
                "subVI icon extraction failed for %r (%s)", node.name, src_path,
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
                fragment = path.read_text(encoding="utf-8")
            except OSError:
                logger.debug("glyph asset not found: %s", path)
                return None
            return InlineSvgGlyph(fragment, icon.size or _DEFAULT_ICON_SIZE)
        return None


def _resolve_bundle_by_name_labels(
    field_terms: list[Terminal], *field_sources: list[ClusterField],
) -> tuple[str, ...]:
    """Resolve each By-Name field terminal's label via the fall-through
    chain (``_resolve_nmux_field_name`` over ``field_sources``, then the
    terminal's own ``name``, then the bracketed index) and ATTACH a REAL
    resolved name onto the terminal's ``display_name`` — the ONE place both
    the (width-truncated) glyph row and the (untruncated) hover
    connector-panel read from (``render/draw.py``'s ``_terminal_label`` /
    ``_pane_label``, via ``graph.op_walk._terminal_display_name``, which is
    ``display_name or name``). Only a genuinely resolved field name is
    attached — the ``t.name``/bracketed-index fallbacks are glyph-row-only:
    the panel already has its own ``terminal N`` default for that case, and
    a bracketed index isn't a field name worth pinning onto the terminal.

    ``_resolve_nmux_field_name`` (the actual field-index -> name math) now
    lives in ``graph.op_walk`` — the SAME implementation
    ``op_walk.stamp_nmux_lane_names`` uses for the netlist/diff/describe
    seam (run once at VI load), so a terminal already stamped by that pass
    is a no-op read here (``if t.display_name`` short-circuits below),
    and one still being rendered from a bare, ungraphed node (e.g. built
    directly in a test) resolves exactly the same way. Single source of
    truth either way: called once, from ``_bundle_by_name_glyph`` (which
    runs during scene construction, before the connector panel is drawn),
    so both surfaces always agree.
    """
    names: list[str] = []
    for t in field_terms:
        fi = t.nmux_field_index
        name = t.display_name or _resolve_nmux_field_name(fi, *field_sources)
        if name:
            t.display_name = name
            names.append(name)
        elif t.name:
            names.append(t.name)
        elif fi is not None:
            names.append(f"[{fi}]")
        else:
            names.append("[?]")
    return tuple(names)


def _bundle_by_name_glyph(
    node: PrimitiveNode, graph: InMemoryVIGraph,
) -> BundleByNameGlyph | None:
    """A Bundle/Unbundle-By-Name glyph for an ``nMux`` (or ``decomposeClusterNode``
    IPES cluster border) node, with each accessed field's NAME resolved from
    the wired cluster's type. The ``agg`` terminal is
    the cluster; each ``list`` terminal carries an ``nmux_field_index`` into that
    field list. ``bundling`` is True when the cluster is the OUTPUT (fields in →
    cluster out). Field names are resolved through a fall-through chain (each
    row independently): (1) for a class-typed aggregate, THIS VI's own inline
    private-data typedef copy (cheap, available even under a MINIMAL load with
    no search-path — see ``_own_class_private_data_fields``); (2)
    ``graph.get_type_fields()`` — dep_graph class fields (authoritative once
    the owning class loads) or a non-class typedef/anonymous cluster's inline
    fields; (3) the field terminal's own resolved name; (4) the accessed
    index in brackets. Returns None if there are no field terminals at all
    (caller falls back to the compact glyph).
    """
    agg = next((t for t in node.terminals if t.nmux_role == "agg"), None)
    own_fields, dep_fields = _nmux_field_sources(node.vi, agg, graph)
    field_terms = sorted(
        (t for t in node.terminals if t.nmux_role == "list"), key=lambda t: t.index,
    )
    if not field_terms:
        return None
    # A Bundle/Unbundle By Name is ALWAYS drawn with its per-field rows. When the
    # cluster type's field NAMES resolve, use them; otherwise fall back to the
    # accessed field INDEX in brackets — ``[0]``, ``[1]`` — so an index is never
    # mistaken for a field literally named "0" (the user's convention). This is
    # the render reality when a typedef's VCTP fails to serialize (its names are
    # unrecoverable from the XML), yet the node must still show what it accesses.
    # ``_resolve_bundle_by_name_labels`` also ATTACHES each resolved name onto
    # its terminal's ``display_name`` so the hover connector-panel (which has
    # room to show the name untruncated) shows the SAME resolved name as this
    # glyph row, not a generic "terminal N".
    names = _resolve_bundle_by_name_labels(field_terms, own_fields, dep_fields)
    # Direction comes from the FIELD terminals, not the aggregate — there can
    # be TWO aggregate terminals (an input source cluster and an output
    # assembled cluster) sharing one DCO, so ``agg.direction`` is ambiguous.
    bundling = field_terms[0].direction == "input"
    return BundleByNameGlyph(names=names, bundling=bundling)


def _event_data_glyph(
    node: PrimitiveNode, graph: InMemoryVIGraph,
) -> EventDataGlyph | None:
    """The Event Data Node / Event Filter Node glyph (see ``EventDataGlyph``).
    Same ``dcoAgg``/``dcoList`` field shape as ``_bundle_by_name_glyph`` (the
    parser reuses ``NMuxHandler`` wholesale — see parser/node_types.py's
    ``_EventDataNodeHandler``), so field NAMES resolve the same way, but each
    row also keeps its field's own resolved LVType (preferring the field
    TERMINAL's own type — every DCO gets one from VCTP independent of the
    cluster-field lookup — and falling back to the cluster field's type only
    when the terminal's is unresolved) for the type-colored name text.

    ``is_filter`` is read from the owning ``EventStructureNode.
    filter_node_uids`` (set during graph construction from the eventStruct's
    ``filterNodeList`` — see parser/nodes/event.py) — the ONLY way to tell an
    Event Filter Node apart from an Event Data Node, since both share this
    same heap class. Returns None if there are no field terminals at all
    (caller falls back to the generic box)."""
    agg = next((t for t in node.terminals if t.nmux_role == "agg"), None)
    # NAMES resolve through the SAME shared, leaf-first resolver Bundle-By-Name
    # uses (incl. the own-class private-data fallback), pinning display_name so
    # the glyph row and the hover connector-panel agree. The positional
    # ``fields`` list is kept ONLY for the per-row type-color FALLBACK (the
    # terminal's own VCTP type is preferred, so this rarely fires).
    own_fields, dep_fields = _nmux_field_sources(node.vi, agg, graph)
    fields = (
        graph.get_type_fields(agg.lv_type) or []
        if agg is not None and agg.lv_type is not None
        else []
    )
    field_terms = sorted(
        (t for t in node.terminals if t.nmux_role == "list"), key=lambda t: t.index,
    )
    if not field_terms:
        return None
    rows: list[tuple[str, LVType | None]] = []
    for t in field_terms:
        fi = t.nmux_field_index
        name = t.display_name or _resolve_nmux_field_name(fi, own_fields, dep_fields)
        if name:
            t.display_name = name
        elif t.name:
            name = t.name
        elif fi is not None:
            name = f"[{fi}]"
        else:
            name = "[?]"
        field = fields[fi] if fi is not None and 0 <= fi < len(fields) else None
        lv_type = t.lv_type if t.lv_type is not None else (
            field.type if field is not None else None
        )
        rows.append((name, lv_type))

    is_filter = False
    parent = graph.get_graph_node(node.parent) if node.parent else None
    if isinstance(parent, EventStructureNode):
        is_filter = node.id in parent.filter_node_uids
    return EventDataGlyph(rows=tuple(rows), is_filter=is_filter)


def _property_node_glyph(node: PrimitiveNode) -> PropertyNodeGlyph | None:
    """A Property Node glyph: one row per accessed property, labelled with the
    property NAME and marked read/write. Names come from ``node.properties``
    (parsed from the heap ``propItemInfo`` list). The per-row read/write flag is
    the direction of that property's VALUE terminal — the terminals that are
    neither the reference (``Refnum``) nor the error cluster, in index order.
    Returns None when the node carries no property names, so the caller falls
    back to the plain "Property Node" box rather than an empty drawer."""
    props = getattr(node, "properties", None) or []
    if not props:
        return None
    value_terms = sorted(
        (
            t for t in node.terminals
            if t.lv_type is not None
            and t.lv_type.underlying_type != "Refnum"
            and type_family(t.lv_type) != "error_cluster"
        ),
        key=lambda t: t.index,
    )
    rows: list[tuple[str, bool]] = []
    for i, p in enumerate(props):
        resolved = (getattr(p, "name", None) or "").strip()
        name = resolved or f"[{i}]"
        # Value flows OUT of a read (output terminal); IN to a write. Default to
        # read when the value terminal can't be matched (read is the common case
        # and only flips a small marker, never the name).
        is_read = value_terms[i].direction == "output" if i < len(value_terms) else True
        # Pin the resolved property name onto its VALUE terminal's display_name --
        # the ONE place the hover connector-panel reads (same pattern as
        # Bundle-By-Name, see _resolve_bundle_by_name_labels) -- so the panel
        # shows e.g. "data access:channel classification", not "terminal N". Only
        # a REAL name is attached (never the "[i]" fallback).
        if resolved and i < len(value_terms):
            value_terms[i].display_name = resolved
        rows.append((name, is_read))
    class_name = (getattr(node, "object_name", None) or "").strip()
    return PropertyNodeGlyph(rows=tuple(rows), class_name=class_name)


def _row_terminal_present(term: Terminal | None) -> bool:
    """Whether a dcoList row-side terminal is a real, wireable connection
    point. LabVIEW's invoke-node heap always allocates a left+right DCO slot
    per row (method select-slot/return-value, param-in/param-out), but an
    unused slot resolves to a ``Void`` type -- confirmed by cross-checking the
    VCTP-resolved types against actual wire connectivity on two real invoke
    nodes (OpenG ``Ctrl Val.Get All`` and ``Print.VI To HTML``): every
    genuinely-wired side resolved to a non-Void type, every never-wired side
    resolved to Void, with 100% agreement across both samples. A row side
    with no real type never gets an arrow, regardless of the dco's own
    direction bit (which merely marks left-vs-right position, not presence)."""
    return (
        term is not None
        and term.lv_type is not None
        and term.lv_type.underlying_type != "Void"
    )


def _invoke_node_glyph(node: PrimitiveNode) -> InvokeNodeGlyph:
    """An Invoke Node glyph: the invoked method as the first drawer row, then one
    row per parameter below it, built directly from the parser's ``dcoList``
    row structure (``invoke_row_terminal_ids`` -- 2 terminal ids per row: row 0
    = method [select-slot, return-value], rows 1..N = params [input, output]).
    This is LabVIEW's real row count (NOT one row per raw terminal): a method
    with N params always has 1 + N drawer rows, however many of the 2N+2
    termList terminals actually carry data. The reference (Refnum) and
    error-cluster rows live in ``permDCOList`` and are never in this list, so
    no filtering is needed here (unlike the property-node glyph).

    Each row's arrows follow the shared rule: an INPUT terminal draws ``▸`` at
    the LEFT edge, an OUTPUT terminal at the RIGHT edge, both if a pass-through
    param has both. The method row's left side is always a Void select-slot
    (LabVIEW draws no terminal there -- just the method name); its right side
    only draws the return-value arrow when the method actually returns
    something (also Void otherwise). Param NAMES aren't in the VI file (they
    belong to the method's VI-server signature), so rows are labeled by index
    (``[i]``)."""
    row_ids = getattr(node, "invoke_row_terminal_ids", None) or []
    by_id = {t.id: t for t in node.terminals}

    def term_at(idx: int) -> Terminal | None:
        return by_id.get(row_ids[idx]) if 0 <= idx < len(row_ids) else None

    return_present = _row_terminal_present(term_at(1))

    n_params = max(0, len(row_ids) // 2 - 1)
    rows: list[tuple[str, bool, bool]] = []
    for i in range(n_params):
        left = term_at(2 + 2 * i)
        right = term_at(2 + 2 * i + 1)
        rows.append((
            f"[{i}]", _row_terminal_present(left), _row_terminal_present(right),
        ))

    return InvokeNodeGlyph(
        method=(getattr(node, "method_name", None) or "").strip(),
        return_present=return_present,
        rows=tuple(rows),
        class_name=(getattr(node, "object_name", None) or "").strip(),
    )


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
    - The boolean logic gates (And, Or, Not) — ``BooleanGateGlyph``'s
      D-shape/shield/triangle+bubble outlines (goal #99).
    - The ARRAY family (Array Size, Reverse/Search/Sort/Split 1D Array) —
      the "little element boxes" motif, keyed by name via ``_ARRAY_GLYPH``.
    - Build Array (``aBuild``) — ``ArrayBuildGlyph``, a drawer that grows
      with its input count (same skeleton as Bundle/Compound Arithmetic).
    - The CONVERTER family (To Long/Word/Byte/Unsigned Integer, Boolean To
      (0,1)) — a small entering arrow into a bold type abbreviation,
      ``ConvertGlyph``, keyed by name via ``_CONVERTER_ABBR``.
    """

    def resolve(self, node: AnyGraphNode, ctx: GlyphContext) -> Glyph | None:
        if not isinstance(node, PrimitiveNode):
            return None
        if node.node_type in _CLUSTER_MUX_TYPES:
            return self._cluster_glyph(node, ctx.graph)
        if node.node_type == "decomposeMatchNode":
            # The IPES's generic whole-value pass-through border node (the
            # "error through" case — a variant match, no field split: the
            # corpus shows exactly 2 terminals, no dcoAgg/dcoList at all,
            # unlike decomposeClusterNode's real field-bundling shape). Both
            # the input-side and output-side border node draw IDENTICALLY —
            # a small square with a rightward arrow — never the "Decompose
            # Match" internal jargon.
            return InPlaceElementGlyph()
        if node.node_type == "propNode":
            return _property_node_glyph(node)
        if node.node_type == "invokeNode":
            return _invoke_node_glyph(node)
        symbol = _COMPARE_SYMBOL.get(node.name or "")
        if symbol is not None:
            return ArithGlyph(symbol)
        gate = _BOOLEAN_GATE.get(node.name or "")
        if gate is not None:
            kind, gate_symbol, negated, input_bubble = gate
            return BooleanGateGlyph(
                gate_symbol, kind=kind, negated=negated, input_bubble=input_bubble,
            )
        if _ARRAY_GLYPHS_ENABLED:
            array_glyph = _ARRAY_GLYPH.get(node.name or "")
            if array_glyph is not None:
                return array_glyph
        abbr = _CONVERTER_ABBR.get(node.name or "")
        if abbr is not None:
            return ConvertGlyph(abbr)
        if _ARRAY_GLYPHS_ENABLED and (
            node.node_type == "aBuild" or node.name == "Build Array"
        ):
            num_inputs = sum(1 for t in node.terminals if t.direction == "input")
            return ArrayBuildGlyph(num_inputs=max(1, num_inputs))
        return None

    @staticmethod
    def _cluster_glyph(node: PrimitiveNode, graph: InMemoryVIGraph) -> Glyph | None:
        # An Event Structure's Event Data Node / Event Filter Node (heap class
        # ``eventDataNode``) is its OWN bespoke glyph — a white named-rows box
        # with a side accent band (see ``EventDataGlyph``) — never the tan
        # Bundle/Unbundle-By-Name look below (it isn't a real cluster
        # assemble/disassemble, just a structurally-identical DCO shape).
        if node.node_type == "eventDataNode":
            return _event_data_glyph(node, graph)
        # nMux and the IPES cluster border node (decomposeClusterNode) are
        # both Bundle/Unbundle BY NAME — a box with the accessed field NAMES,
        # resolved from the wired cluster's type (mux/demux are the compact,
        # positional Bundle/Unbundle handled by field count below).
        if node.node_type in _BY_NAME_MUX_TYPES:
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
    operation = node.operation or "add"
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
    elif lv_type is not None and lv_type.underlying_type == "Refnum":
        # A refnum constant is a CLASS/LVObject constant (or a null refnum):
        # label it by its class name, never the placeholder raw value the parser
        # stores (e.g. "Refnum(1)"). Keyed on underlying_type, NOT the "refnum"
        # family — a CLASS refnum has fam=="unknown" (type_family reserves
        # "refnum" for GENERIC refs, whose wire is reference-green). Same rule as
        # a class refnum terminal — see style.lv_type_label. The name word-wraps
        # AND shrinks to fill the box (fit=True) instead of truncating.
        return ConstantGlyph(lv_type_label(lv_type), color, fit=True)
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
            # bold-bordered in the referenced control's DATA-TYPE wire color
            # (boolean green, string pink, ...) — LabVIEW type-colors a local
            # variable's border. The type comes off its own terminal.
            lv_type = next(
                (t.lv_type for t in node.terminals if t.lv_type is not None), None
            )
            border = wire_style(lv_type).color if lv_type is not None else None
            # A control-reference constant is modeled as a LocalVariableNode for
            # codegen (it resolves to the referenced FP variable), but it is NOT
            # a local variable: it emits a control REFERENCE (a refnum). LabVIEW
            # type-colors the constant's box by the CONTROL it references (its
            # own OUTPUT wire is the refnum reference wire) — so border/text take
            # the referenced control's data-type color, resolved from the FP
            # terminal the reference points at.
            if node.node_type == "ctlRefConst":
                ref_term = (
                    ctx.graph.get_terminal(node.control_terminal_id)
                    if node.control_terminal_id else None
                )
                ref_type = ref_term.lv_type if ref_term is not None else None
                type_color = (
                    wire_style(ref_type).color if ref_type is not None else border
                )
                return ControlRefConstGlyph(
                    name=node.control_name or node.name or "Control Reference",
                    type_text=type_repr(ref_type),
                    type_color=type_color,
                )
            return LocalVariableGlyph(
                node.control_name or node.name or "Local Variable",
                is_write=node.is_write,
                border_color=border,
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
