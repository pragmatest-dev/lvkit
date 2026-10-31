"""Small shared graph-node helpers used by both ``describe`` and ``netlist``.

Split out to avoid a circular import between the two. Covers: nMux/decompose
field-name resolution (``stamp_nmux_lane_names``), property-node value-
terminal correlation (``stamp_property_value_names``), constant/selector
text formatting, and the typed SubVI/component port-declaration lookup
(``_subvi_ports``) shared by describe's ``## Dependencies`` and netlist's
``## Components``. Neither ``describe.py`` nor ``netlist*.py`` may import
the other at module level, so these shared helpers live here instead.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..extractor import extract_vi_xml
from ..measure_data import measure_data_field_name
from ..models import (
    CaseFrame,
    ClusterField,
    LVType,
    LVTypeKind,
    PropertyDef,
    SelectorRange,
    Terminal,
    TerminalDirection,
    _is_error_cluster,
)
from ..num_format import format_numeric_const
from ..parser.constants import NMUX_NAMED_LANE_NODE_CLASSES
from ..structure import _fields_from_xml, private_data_field_to_cluster_field
from ..vilib_resolver import get_resolver as _get_vilib_resolver
from .models import Constant, VINode

if TYPE_CHECKING:
    from .core import InMemoryVIGraph

logger = logging.getLogger(__name__)


def _flatten_fields(
    fields: list[ClusterField],
) -> list[tuple[list[str], ClusterField]]:
    """Flatten cluster fields depth-first with path.

    A by-name drawer's field index (nMux and Event Data ``<i>``, IPE decompose
    ``<index>``) counts this flatten across the whole cluster hierarchy: a
    sub-cluster is its own slot, followed by its fields. Verified against the
    label stored on each drawer's own type, for class private data, typedefs
    and event data alike. Shared by codegen's nmux field assignment
    (``codegen/nodes/nmux.py``, which applies its own Python-identifier naming
    on top) and the netlist/describe/render field-name display below (which
    shows the raw LabVIEW name).
    """
    result: list[tuple[list[str], ClusterField]] = []
    for f in fields:
        result.append(([f.name], f))
        if f.type and f.type.fields:
            for sub_path, sub_field in _flatten_fields(f.type.fields):
                result.append(([f.name] + sub_path, sub_field))
    return result


def _own_class_private_data_fields(
    vi_name: str,
    classname: str,
    graph: InMemoryVIGraph,
) -> list[ClusterField]:
    """This VI's OWN inline "Cluster of class private data" typedef — a
    snapshot embedded in the VI's own VCTP, resolvable WITHOUT loading the
    owning ``.lvclass`` at all (works under a ``MINIMAL`` load whose class file
    is absent: the VI's own extracted XML already carries it). Tried
    before the dep_graph class fields in ``_nmux_field_sources`` because
    it's cheap and available in the common case; ``get_type_fields`` (via
    ``get_class_fields``) is the fallback, authoritative once the owning
    class actually loads. Fails soft (empty list) on any extraction/parse
    error — this is a decoration, never a reason to fail resolution.
    """
    vi_path = graph.get_vi_source_path(vi_name)
    if vi_path is None:
        return []
    try:
        _bd_xml, _fp_xml, main_xml = extract_vi_xml(vi_path)
    except Exception:
        logger.debug(
            "own private-data XML extraction failed for %r (%s)",
            vi_name,
            vi_path,
            exc_info=True,
        )
        return []
    if main_xml is None:
        return []
    # A single VI has no class file to name the private-data control; the
    # control is "<Class>.ctl", the name every class file in the corpus
    # records. By-reference private data (a lone Data Value Reference) is
    # left to the class's control file, which dereferences it.
    class_stem = classname.rsplit(":", 1)[-1].rsplit(".", 1)[0]
    fields = _fields_from_xml(main_xml, classname, f"{class_stem}.ctl")
    if not fields:
        return []
    return [private_data_field_to_cluster_field(f) for f in fields]


def _resolve_nmux_field_name(
    field_index: int | None,
    *field_sources: list[ClusterField],
) -> str | None:
    """Resolve a field-index's LabVIEW field name, trying each of
    ``field_sources`` (in priority order) in turn — each row resolved
    independently, so one source can cover a row another source misses. The
    index counts :func:`_flatten_fields`, so a whole sub-cluster is its own
    addressable slot.
    """
    if field_index is None:
        return None
    for fields in field_sources:
        if not fields:
            continue
        flat = _flatten_fields(fields)
        if 0 <= field_index < len(flat):
            name = flat[field_index][1].name
            if name:
                return name
    return None


def _resolve_nmux_field_path(
    field_index: int | None,
    *field_sources: list[ClusterField],
) -> str | None:
    """The full dotted navigation PATH to a NESTED field for a resolved nmux
    index (e.g. ``Strain Config.strain gage information.poisson ratio``), or
    None for a top-level field or when nothing resolves. Same flatten/source
    order as :func:`_resolve_nmux_field_name` (which returns only the leaf) — the
    flatten already carries the path per slot; this just keeps it for the
    tooltip. Only a genuinely nested path (more than one segment) is returned."""
    if field_index is None:
        return None
    for fields in field_sources:
        if not fields:
            continue
        flat = _flatten_fields(fields)
        if 0 <= field_index < len(flat):
            path, field = flat[field_index]
            if field.name and len(path) > 1:
                return ".".join(path)
    return None


def _nmux_field_sources(
    vi_name: str,
    agg: Terminal | None,
    graph: InMemoryVIGraph,
) -> tuple[list[ClusterField], list[ClusterField]]:
    """``(own_fields, dep_fields)`` field sources for an nMux/decompose
    aggregate terminal, in fall-through priority order: (1) for a
    class-typed aggregate, THIS VI's own inline private-data typedef copy
    (``_own_class_private_data_fields``); (2) ``graph.get_type_fields()`` --
    dep_graph class fields (authoritative once the owning class loads) or a
    non-class typedef/anonymous cluster's inline fields. Shared by
    ``_nmux_lane_name`` (single-terminal) and render's ``_bundle_by_name_glyph``
    (whole-node glyph build) so both compute the SAME sources."""
    own_fields: list[ClusterField] = []
    dep_fields: list[ClusterField] = []
    if agg is not None and agg.lv_type is not None:
        if agg.lv_type.classname:
            own_fields = _own_class_private_data_fields(
                vi_name,
                agg.lv_type.classname,
                graph,
            )
        dep_fields = graph.get_type_fields(agg.lv_type) or []
    return own_fields, dep_fields


def _nmux_lane_name(
    term: Terminal,
    agg: Terminal | None,
    vi_name: str,
    graph: InMemoryVIGraph,
) -> str | None:
    """THE canonical field-name resolution for an nMux/decompose LIST
    terminal, via ``term.nmux_field_index`` into the aggregate's field list
    (``_nmux_field_sources``, then ``_resolve_nmux_field_name``). Returns None
    when neither source resolves a name — callers keep their own index/name
    fallback (a
    bracketed index or the terminal's own name is display-only, never worth
    pinning onto ``display_name``)."""
    # A MeasureData aggregate (waveform / digital data) has no VCTP fields — its
    # component names come from the built-in flavor table, keyed directly by the
    # drawer's field index.
    if agg is not None and agg.lv_type is not None and agg.lv_type.measure_flavor:
        name = measure_data_field_name(
            agg.lv_type.measure_flavor, term.nmux_field_index
        )
        if name:
            return name
    own_fields, dep_fields = _nmux_field_sources(vi_name, agg, graph)
    name = _resolve_nmux_field_name(term.nmux_field_index, own_fields, dep_fields)
    if name is not None:
        # Also keep the full dotted path (nested fields only) for the tooltip —
        # the glyph shows the terse leaf ``name``.
        term.field_path = _resolve_nmux_field_path(
            term.nmux_field_index, own_fields, dep_fields
        )
    return name


def stamp_nmux_lane_names(graph: InMemoryVIGraph) -> None:
    """Resolve and attach every nMux/decompose LIST terminal's real field
    NAME onto ``Terminal.display_name``, graph-wide -- the ONE seam every
    consumer (netlist, diff, describe; render redundantly but harmlessly)
    relies on instead of each special-casing nMux terminals itself.

    Walks ``graph.iter_nodes(vi_name)`` -- the FLAT per-VI node list (every
    node under a VI, including loop-body/frame-nested nodes and an In Place
    Element Structure's border decompose/recompose nodes, regardless of
    containment depth): the plain flat read this needs, reaching every node
    at any depth with no tree materialization at all.

    Idempotent: only sets ``display_name`` when it is still unset AND a real
    field name resolves -- never clobbers an already-resolved name, and
    never stamps a bracketed-index/no-name placeholder (a caller's own
    index fallback still applies for those, and a later fuller load, e.g.
    once the class file is at its recorded path, can fill it in). Safe to call
    repeatedly (e.g. once per top-level ``load_vi``).

    Gated to ``NMUX_NAMED_LANE_NODE_CLASSES`` (``nMux``/
    ``decomposeClusterNode``/``eventDataNode``, whose terminals each store a
    field index) -- NOT every node carrying ``nmux_role`` terminals. ``mux``/``demux``
    (loop/structure-boundary bundlers) share the exact same dcoAgg/dcoList
    terminal shape but index POSITIONALLY, never by a real field name; naming
    their ports/wires after whatever field happens to sit at that position
    would be wrong, not just additive -- verified against a real corpus VI
    (JKI-EasyXML's XML Loop Stack Recursion.vi) where an early, broader
    version of this gate spuriously renamed a plain positional ``mux``
    node's ports.
    """
    for vi_name in graph.list_vis():
        for node in graph.iter_nodes(vi_name):
            if node.node_type not in NMUX_NAMED_LANE_NODE_CLASSES:
                continue
            agg = next((t for t in node.terminals if t.nmux_role == "agg"), None)
            if agg is None:
                continue
            for term in node.terminals:
                if term.nmux_role != "list" or term.display_name is not None:
                    continue
                name = _nmux_lane_name(term, agg, vi_name, graph)
                if name:
                    term.display_name = name


def correlate_property_terminals(
    properties: list[PropertyDef],
    terminals: list[Terminal],
    value_terminal_ids: list[str],
) -> list[tuple[PropertyDef, Terminal | None]]:
    """Pair each accessed property (``PrimitiveNode.properties``) with its
    VALUE terminal, in index order, via ``value_terminal_ids``
    (``PrimitiveNode.property_value_terminal_ids`` -- the parser's real
    dcoList, re-expressed as terminal ids, see
    ``parser.node_types._dco_list_terminal_uids``).

    This is a STRUCTURAL correlation (LabVIEW's own dcoList/permDCOList
    split), not a type-based guess: the object reference in/out and error
    in/out terminals ALWAYS live in permDCOList, never dcoList, regardless of
    their own resolved type -- so this stays exact even when a property's
    VALUE is itself Refnum-typed (e.g. a "Library:Project" property returns a
    Project reference, confirmed on a real corpus VI -- a naive Refnum/
    error-cluster type filter would wrongly exclude that terminal). This is
    the ONE correlation every consumer (the SVG glyph, the load-time
    display-name stamp, the netlist projection) shares -- see
    ``render/nodes.py::_property_node_glyph`` and
    ``stamp_property_value_names`` below.

    ``terminal`` is ``None`` when a property's id has no matching terminal
    (fewer ids than properties, or a stale/unresolved id -- shouldn't happen
    on a real VI) -- callers decide how to degrade (fall back to the
    terminal's own numeric port), never fabricate a name here.
    """
    by_id = {t.id: t for t in terminals}
    return [
        (p, by_id.get(value_terminal_ids[i]) if i < len(value_terminal_ids) else None)
        for i, p in enumerate(properties)
    ]


def stamp_property_value_names(graph: InMemoryVIGraph) -> None:
    """Resolve and attach every Property Node's accessed property NAME onto
    its correlated VALUE terminal's ``Terminal.display_name``, graph-wide --
    the SAME seam as ``stamp_nmux_lane_names`` above: the ONE place every
    consumer (netlist, diff, describe; render redundantly but harmlessly)
    relies on instead of each re-deriving the property<->terminal
    correlation itself.

    Idempotent: only sets ``display_name`` when it is still unset AND a real
    property name resolves. Safe to call repeatedly (e.g. once per top-level
    ``load_vi``).
    """
    for vi_name in graph.list_vis():
        for node in graph.iter_nodes(vi_name):
            props = getattr(node, "properties", None) or []
            if not props:
                continue
            value_ids = getattr(node, "property_value_terminal_ids", None) or []
            for prop, term in correlate_property_terminals(
                props,
                node.terminals,
                value_ids,
            ):
                if term is None or term.display_name is not None:
                    continue
                name = (prop.name or "").strip()
                if name:
                    term.display_name = name


def _format_error_cluster(value: object) -> str:
    """Render an error-cluster value as ``code N: "source"``."""
    data = value
    if isinstance(value, str):
        try:
            data = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    if isinstance(data, dict):
        code = data.get("code", 0)
        source = data.get("source", "")
        status = data.get("status", False)
        if not status and not code:
            return "no error"
        if source:
            return f'code {code}: "{source}"'
        return f"code {code}"
    return str(value)


def _const_value_str(c: Constant) -> str:
    """Human-readable value for a constant (no redundant quoting)."""
    if c.lv_type and _is_error_cluster(c.lv_type):
        return _format_error_cluster(c.value)
    formatted = format_numeric_const(c.lv_type, c.value, c.display_format)
    if formatted is not None:
        return formatted
    return str(c.value)


def _range_tokens(ranges: list[SelectorRange], fmt: Callable[[int], str]) -> list[str]:
    """A frame's selector ranges as the separate values LabVIEW lists in its
    label: singles as ``fmt(v)``, closed ranges as ``a..b``, open ranges as
    ``a..`` / ``..b``, and the whole domain as ``..`` (e.g. ``1``, ``3``,
    ``5..8``). Exactly two adjacent values list both (``a``, ``b``): ".."
    means the contiguous middle is skipped, which only applies to a span of
    three or more values."""
    tokens: list[str] = []
    for r in ranges:
        if r.open_start and r.open_end:
            tokens.append("..")
        elif r.open_start:
            tokens.append(f"..{fmt(r.end)}")
        elif r.open_end:
            tokens.append(f"{fmt(r.start)}..")
        elif r.is_single:
            tokens.append(fmt(r.start))
        elif r.end == r.start + 1:
            tokens.extend((fmt(r.start), fmt(r.end)))
        else:
            tokens.append(f"{fmt(r.start)}..{fmt(r.end)}")
    return tokens


def _format_ranges(ranges: list[SelectorRange], fmt: Callable[[int], str]) -> str:
    """Render a frame's selector ranges the way LabVIEW builds the label: the
    ``_range_tokens`` values joined with ``, `` (e.g. ``1, 3, 5..8``). Shared by
    the renderer's frame-chrome label and the netlist's case/disable frame
    label -- both need the identical faithful text."""
    return ", ".join(_range_tokens(ranges, fmt))


def _terminal_display_name(term: Terminal) -> str | None:
    """The resolved-name half of the terminal naming rule: ``display_name`` (the
    resolved-def name) else the caller-side ``name``, or ``None`` when neither is
    set. Callers append their OWN index-based fallback (the netlist's
    ``str(index)`` terminal name -- ``netlist._component_terminal_name`` -- the
    renderer's ``terminal N`` tooltip -- ``render/draw.py::_terminal_label``), so
    only the shared name lookup lives here."""
    return term.display_name or term.name


_DEFAULT_LABEL = "Default"


def _is_string_selector(frame: CaseFrame, lv_type: LVType | None) -> bool:
    """Whether the frame's values are strings (shown quoted)."""
    return bool(frame.selector_strings) or bool(
        lv_type and lv_type.underlying_type == "String"
    )


def _selector_values(frame: CaseFrame, lv_type: LVType | None) -> list[str]:
    """A frame's explicit selector values, one entry per value: enum item
    name(s), integer value(s)/range(s), or the raw strings; empty when it has
    none."""
    if (
        lv_type
        and lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING)
        and lv_type.values
        and frame.selector_ranges
    ):
        int_to_name = {ev.value: name for name, ev in lv_type.values.items()}
        return _range_tokens(
            frame.selector_ranges,
            lambda i: int_to_name.get(i, str(i)),
        )
    if frame.selector_ranges:  # integer selector
        return _range_tokens(frame.selector_ranges, str)
    return list(frame.selector_strings)  # string selector: one frame, several


def selector_frame_values(
    frame: CaseFrame, lv_type: LVType | None, is_error: bool
) -> tuple[str, ...]:
    """The values one case frame's label lists, unquoted and in order -- empty
    for a default frame that matches no value of its own. Error-cluster frames
    list their labels (``No Error``/``Error``/``Error 3..10``; a No Error frame
    that also stores codes lists ``No Error, Error 3..10``)."""
    sv = str(frame.selector_value)
    if is_error:
        # The error-cluster case switches on the status boolean: 0 = no error,
        # anything else is an error (LabVIEW: "No Error" / "Error", plus code
        # ranges like "Error 3..10" since 2019). A frame that stores status 1
        # alone is "Error"; a frame that stores no value is the default.
        status = 0 if frame.is_no_error else 1
        codes = [
            r for r in frame.selector_ranges if not (r.is_single and r.start == status)
        ]
        listed = [f"Error {_format_ranges(codes, str)}"] if codes else []
        if frame.is_no_error:
            return ("No Error", *listed)
        if listed:
            return tuple(listed)
        if frame.selector_ranges:
            return ("Error",)
        return ()
    values = _selector_values(frame, lv_type)
    if values:
        return tuple(values)
    if frame.boolean_value is not None:
        return (str(frame.boolean_value),)
    if frame.is_default:
        return ()
    return (sv,)  # an already-display token


def _double_quote(text: str) -> str:
    """Plain double quoting of one selector value (no escaping)."""
    return f'"{text}"'


def selector_values_quoted_one_by_one(
    frame: CaseFrame, lv_type: LVType | None, is_error: bool
) -> bool:
    """Whether a frame's label quotes each of its string values itself (a string
    selector that stores values). Every other label is one unquoted text."""
    return _is_string_selector(frame, lv_type) and bool(
        selector_frame_values(frame, lv_type, is_error)
    )


def _selector_label(
    frame: CaseFrame,
    lv_type: LVType | None,
    is_error: bool,
    quote_value: Callable[[str], str] = _double_quote,
) -> str:
    """The faithful case-selector text for one frame, by selector type:
    ``Default``; error cluster → ``No Error``/``Error``; enum → item name(s);
    integer → value(s)/range(s); string → each value through ``quote_value``;
    boolean → ``True``/``False``. A default frame that also matches values lists
    them first (``0, Default``).
    """
    values = selector_frame_values(frame, lv_type, is_error)
    if _is_string_selector(frame, lv_type):
        values = [quote_value(v) for v in values]
    listed = ", ".join(values)
    if not frame.is_default:
        return listed
    if not listed:
        return _DEFAULT_LABEL
    # A Boolean or error frame that stores a value is fully described -- every
    # value has a frame -- so it takes no Default marker. Any other default frame
    # can also carry explicit values ("0, Default").
    if is_error or frame.boolean_value is not None:
        return listed
    return f"{listed}, {_DEFAULT_LABEL}"


# ============================================================
# Component declarations (Verilog-module / VHDL-entity half of the netlist)
# ============================================================
#
# Shared by ``describe.py``'s ``## Dependencies`` (non-verbose) and
# ``netlist.py``'s ``NetlistModule.components`` (verbose ``## Components``) --
# both need the SAME typed subVI interface, just rendered differently (a
# one-line ``name: (ins) -> (outs) -- description`` vs a node-first
# ``name(ins) -> (outs)`` declaration), so the lookup lives here rather than
# in either consumer.


@dataclass(frozen=True)
class ComponentPort:
    """One named, typed port on a component's declared interface."""

    name: str
    # None when neither the callee nor the call records the port's type.
    type: str | None


def _type_text(lv_type: LVType | None) -> str:
    """A port's type as lvkit writes it; ``Any`` when none is known."""
    return lv_type.type_descriptor() if lv_type else "Any"


def is_void_slot(lv_type: LVType | None) -> bool:
    """Whether a terminal's type is ``Void``: the type the Function type a caller
    records gives a pane position with no parameter of the callee
    (``labview-binary-format.md``)."""
    return lv_type is not None and lv_type.type_descriptor() == "Void"


def _recorded_type(lv_type: LVType | None) -> str | None:
    """The type a call records for a terminal, None when it records none (no
    type, or a ``Void`` slot)."""
    if lv_type is None or is_void_slot(lv_type):
        return None
    return lv_type.type_descriptor()


def _subvi_ports(
    graph: InMemoryVIGraph, call: VINode
) -> tuple[list[ComponentPort], list[ComponentPort]] | None:
    """Typed (inputs, outputs) port list for the SubVI ``call`` calls.

    A loaded callee (``InMemoryVIGraph.loaded_callee_key``) gives its resolved
    front-panel signature. Without one, a vi.lib call's ports are the catalog's
    terminals (looked up by the path the call records) at their pane
    positions, each typed by what the call records at that position (no type
    when it records none), plus each terminal the call records at a position
    the catalog does not place, named by its position. A catalog terminal with
    no direction is placed by the call's terminal at its position; one with no
    position cannot be placed and is left out. Returns ``None`` when neither
    source is available -- callers decide how to render that (bare name, or an
    empty-port declaration), never fabricate ports.
    """
    callee_key = graph.loaded_callee_key(call)
    if callee_key is not None:
        sctx = graph.get_vi_context(callee_key)
        ins = [
            ComponentPort(name=t.name or str(t.index), type=_type_text(t.lv_type))
            for t in sctx.inputs
        ]
        outs = [
            ComponentPort(name=t.name or str(t.index), type=_type_text(t.lv_type))
            for t in sctx.outputs
        ]
        return ins, outs

    entry = _get_vilib_resolver().resolve_recorded(call.qualified_path)
    if entry is not None and entry.terminals:
        recorded = {t.index: t for t in call.terminals}
        # (position, direction, name, type) of every port, catalog first.
        placed: dict[int, tuple[TerminalDirection, str, str | None]] = {}
        for t in entry.terminals:
            if t.index is None or t.index in placed:
                continue
            own = recorded.get(t.index)
            direction = t.direction or (own.direction if own else None)
            if direction is None:
                continue
            placed[t.index] = (
                direction,
                t.name,
                _recorded_type(own.lv_type) if own else None,
            )
        for index, own in recorded.items():
            if index not in placed and _recorded_type(own.lv_type) is not None:
                placed[index] = (
                    own.direction,
                    own.name or str(index),
                    _recorded_type(own.lv_type),
                )
        ports: dict[TerminalDirection, list[ComponentPort]] = {
            TerminalDirection.INPUT: [],
            TerminalDirection.OUTPUT: [],
        }
        for index in sorted(placed):
            direction, name, type_text = placed[index]
            ports[direction].append(ComponentPort(name=name, type=type_text))
        return ports[TerminalDirection.INPUT], ports[TerminalDirection.OUTPUT]
    return None


def _render_ports(ins: list[ComponentPort], outs: list[ComponentPort]) -> str:
    """``"(a: T, b: U) -> (c: V)"`` -- ASCII-only, shared by describe's
    ``## Dependencies`` line and netlist's ``## Components`` declaration."""
    in_str = ", ".join(_port_text(p) for p in ins)
    out_str = ", ".join(_port_text(p) for p in outs)
    return f"({in_str}) -> ({out_str})"


def _port_text(port: ComponentPort) -> str:
    """``name: Type``, or the name alone when its type is not known."""
    return port.name if port.type is None else f"{port.name}: {port.type}"
