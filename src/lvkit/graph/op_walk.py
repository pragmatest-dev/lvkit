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
    _is_error_cluster,
)
from ..num_format import format_numeric_const
from ..parser.constants import NMUX_BY_NAME_NODE_CLASSES, NODE_CLASS_NMUX
from ..structure import _fields_from_xml, private_data_field_to_cluster_field
from ..vilib_resolver import get_resolver as _get_vilib_resolver
from .models import Constant

if TYPE_CHECKING:
    from .core import InMemoryVIGraph

logger = logging.getLogger(__name__)


def _flatten_fields(
    fields: list[ClusterField],
) -> list[tuple[list[str], ClusterField]]:
    """Flatten cluster fields depth-first with path.

    LabVIEW nMux ``<i>`` tags use flattened indices across the entire
    cluster hierarchy, not just the top level. Shared by codegen's nmux
    field assignment (``codegen/nodes/nmux.py``, which applies its own
    Python-identifier naming on top) and the netlist/describe field-name
    display below (which shows the raw LabVIEW name).
    """
    result: list[tuple[list[str], ClusterField]] = []
    for f in fields:
        result.append(([f.name], f))
        if f.type and f.type.fields:
            for sub_path, sub_field in _flatten_fields(f.type.fields):
                result.append(([f.name] + sub_path, sub_field))
    return result


def _flatten_leaf_fields(
    fields: list[ClusterField],
) -> list[tuple[list[str], ClusterField]]:
    """Depth-first LEAF-only flatten of nested cluster fields.

    Unlike ``_flatten_fields`` (which also yields an entry for each
    intermediate sub-cluster field, since Bundle/Unbundle By Name CAN
    address a whole sub-cluster as a value), a nested cluster's raw
    nMux/IPES-decompose flat ``<i>`` index counts LEAF elements only — an
    intermediate sub-cluster is never itself an addressable flat slot.
    Verified against a real corpus VI (DAQmx Module Runtime's private data:
    ``DAQ Tasks``/``Channel Indeces`` sub-clusters, flat indices 7/1/9/3 ->
    DI Index/AO Task/PWM Freq Index/DO Task).
    """
    return [
        (path, f)
        for path, f in _flatten_fields(fields)
        if not (f.type and f.type.fields)
    ]


def _own_class_private_data_fields(
    vi_name: str,
    classname: str,
    graph: InMemoryVIGraph,
) -> list[ClusterField]:
    """This VI's OWN inline "Cluster of class private data" typedef — a
    snapshot embedded in the VI's own VCTP, resolvable WITHOUT loading the
    owning ``.lvclass`` at all (works under a ``MINIMAL`` load with no
    ``--search-path``: the VI's own extracted XML already carries it). Tried
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
    # allow_display_label_fallback: in the SINGLE-VI case (an uploaded VI with
    # no .lvclass / .ctl on any search path) the class's private-data cluster is
    # often labeled with its control's display name rather than "class private
    # data"; accept it (identified by owner class + "<Class>.ctl") so fields
    # resolve from the VI alone instead of falling back to raw [index]. The
    # dep_graph/.ctl path (authoritative, and it dereferences by-reference data)
    # is unaffected — it never sets this flag.
    fields = _fields_from_xml(main_xml, classname, allow_display_label_fallback=True)
    if not fields:
        return []
    return [private_data_field_to_cluster_field(f) for f in fields]


def _nmux_index_leaf_only(node_type: str | None, agg: Terminal | None) -> bool:
    """How a node's flat ``<i>`` field index counts a NESTED cluster's fields.

    FULL depth-first flatten (an intermediate sub-cluster is itself an
    addressable slot) applies to exactly ONE case: a Bundle/Unbundle By Name
    (``nMux``) over a plain typedef/cluster aggregate. Class private data —
    accessed by ``nMux`` OR the IPES cluster border ``decomposeClusterNode`` —
    and event data index LEAF fields only. Verified: ``nMux`` on
    ``Parameters.ctl`` uses ``<i>``=40 for the whole ``PWM Config`` sub-cluster
    (slot 40 of the full flatten; leaf-only has no such slot); ``nMux`` on a
    class's own private data resolves ``pin_map_id`` (leaf); and a
    ``decomposeClusterNode`` on ``Daqmx Module runtime`` private data uses
    7/1/9/3 -> the leaf fields DI Index/AO Task/PWM Freq Index/DO Task. So the
    private-data (``classname``-bearing) aggregate stays leaf even under
    ``nMux``."""
    if node_type != NODE_CLASS_NMUX:
        return True
    return not (
        agg is not None
        and agg.lv_type is not None
        and agg.lv_type.classname is None
    )


def _resolve_nmux_field_name(
    field_index: int | None,
    *field_sources: list[ClusterField],
    leaf_only: bool,
) -> str | None:
    """Resolve a field-index's LabVIEW field name, trying each of
    ``field_sources`` (in priority order) in turn — each row resolved
    independently, so one source can cover a row another source misses.

    ``leaf_only`` picks how each source is flattened for a NESTED cluster (see
    :func:`_nmux_index_leaf_only`): ``True`` counts LEAF fields only
    (``_flatten_leaf_fields`` — IPES decompose / event data), ``False`` counts
    every node depth-first so a whole sub-cluster is its own addressable slot
    (``_flatten_fields`` — Bundle/Unbundle By Name).
    """
    if field_index is None:
        return None
    flatten = _flatten_leaf_fields if leaf_only else _flatten_fields
    for fields in field_sources:
        if not fields:
            continue
        flat = flatten(fields)
        if 0 <= field_index < len(flat):
            name = flat[field_index][1].name
            if name:
                return name
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
        dep_fields = graph.get_type_fields(agg.lv_type, caller_vi_key=vi_name) or []
    return own_fields, dep_fields


def _nmux_lane_name(
    term: Terminal,
    agg: Terminal | None,
    vi_name: str,
    graph: InMemoryVIGraph,
    node_type: str | None,
) -> str | None:
    """THE canonical field-name resolution for an nMux/decompose LIST
    terminal, via ``term.nmux_field_index`` into the aggregate's field list
    (``_nmux_field_sources``, then ``_resolve_nmux_field_name`` — flattened per
    ``node_type``, see :func:`_nmux_index_leaf_only`). Returns None when neither
    source resolves a name — callers keep their own index/name fallback (a
    bracketed index or the terminal's own name is display-only, never worth
    pinning onto ``display_name``)."""
    # A MeasureData aggregate (waveform / digital data) has no VCTP fields — its
    # component names come from the built-in flavor table, keyed directly by the
    # drawer's field index (no leaf-flatten).
    if agg is not None and agg.lv_type is not None and agg.lv_type.measure_flavor:
        name = measure_data_field_name(
            agg.lv_type.measure_flavor, term.nmux_field_index
        )
        if name:
            return name
    own_fields, dep_fields = _nmux_field_sources(vi_name, agg, graph)
    return _resolve_nmux_field_name(
        term.nmux_field_index,
        own_fields,
        dep_fields,
        leaf_only=_nmux_index_leaf_only(node_type, agg),
    )


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
    once a class search-path resolves, can fill it in). Safe to call
    repeatedly (e.g. once per top-level ``load_vi``).

    Gated to ``NMUX_BY_NAME_NODE_CLASSES`` (``nMux``/``decomposeClusterNode``)
    -- NOT every node carrying ``nmux_role`` terminals. ``mux``/``demux``
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
            if node.node_type not in NMUX_BY_NAME_NODE_CLASSES:
                continue
            agg = next((t for t in node.terminals if t.nmux_role == "agg"), None)
            if agg is None:
                continue
            for term in node.terminals:
                if term.nmux_role != "list" or term.display_name is not None:
                    continue
                name = _nmux_lane_name(term, agg, vi_name, graph, node.node_type)
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


def _format_ranges(ranges: list[SelectorRange], fmt: Callable[[int], str]) -> str:
    """Render a frame's selector ranges the way LabVIEW builds the label:
    singles as ``fmt(v)``, closed ranges as ``a..b``, open ranges as ``a..``
    / ``..b``, joined with ``, `` (e.g. ``1, 3, 5..8``). Shared by the
    renderer's frame-chrome label and the netlist's case/disable frame
    label -- both need the identical faithful text."""
    parts: list[str] = []
    for r in ranges:
        if r.open_start:
            parts.append(f"..{fmt(r.end)}")
        elif r.open_end:
            parts.append(f"{fmt(r.start)}..")
        elif r.is_single:
            parts.append(fmt(r.start))
        else:
            parts.append(f"{fmt(r.start)}..{fmt(r.end)}")
    return ", ".join(parts)


def is_no_error_selector(selector_value: str) -> bool:
    """The error-cluster case that switches on ``status == False`` -- selector
    value ``"0"`` -- is LabVIEW's "No Error" (green) frame; every other value is
    an "Error" (red) frame. Shared by ``_selector_label`` (the label text) and
    ``render/scene.py``'s green/red frame classification, so the two never
    disagree on which frame is No-Error."""
    return selector_value == "0"


def _terminal_display_name(term: Terminal) -> str | None:
    """The resolved-name half of the terminal naming rule: ``display_name`` (the
    resolved-def name) else the caller-side ``name``, or ``None`` when neither is
    set. Callers append their OWN index-based fallback (the netlist's
    ``str(index)`` terminal name -- ``netlist._component_terminal_name`` -- the
    renderer's ``terminal N`` tooltip -- ``render/draw.py::_terminal_label``), so
    only the shared name lookup lives here."""
    return term.display_name or term.name


def _selector_label(frame: CaseFrame, lv_type: LVType | None, is_error: bool) -> str:
    """The faithful case-selector text for one frame, by selector type:
    ``Default``; error cluster → ``No Error``/``Error``; enum → item name(s);
    integer → value(s)/range(s); string → quoted; boolean → ``True``/``False``.
    """
    sv = str(frame.selector_value)
    if is_error:
        # The error-cluster case switches on the status boolean: 0 = no error,
        # anything else is an error (LabVIEW: "No Error" / "Error", plus code
        # ranges like "Error 3..10" since 2019). The Error frame is often the
        # structure's default — LabVIEW still labels it "Error", not "Default",
        # so this precedes the plain-default branch below.
        if is_no_error_selector(sv):
            return "No Error"
        codes = [r for r in frame.selector_ranges if not (r.is_single and r.start == 1)]
        if codes:
            return f"Error {_format_ranges(codes, str)}"
        return "Error"
    if frame.is_default or sv == "Default":
        return "Default"
    if (
        lv_type
        and lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING)
        and lv_type.values
        and frame.selector_ranges
    ):
        int_to_name = {ev.value: name for name, ev in lv_type.values.items()}
        return _format_ranges(
            frame.selector_ranges,
            lambda i: int_to_name.get(i, str(i)),
        )
    if frame.selector_ranges:  # integer selector
        return _format_ranges(frame.selector_ranges, str)
    if frame.selector_strings:  # string selector — one frame, several strings
        return ", ".join(f'"{s}"' for s in frame.selector_strings)
    if lv_type and lv_type.underlying_type == "String":
        return f'"{sv}"'
    return sv  # boolean True/False, or an already-display token


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
    type: str


def _subvi_ports(
    graph: InMemoryVIGraph,
    name: str,
) -> tuple[list[ComponentPort], list[ComponentPort]] | None:
    """Typed (inputs, outputs) port list for a called SubVI.

    Loaded VIs use their resolved front-panel signature; unloaded vilib
    refs fall back to the resolver's terminal layout. Returns ``None`` when
    neither is available -- callers decide how to render that (bare name,
    or an empty-port declaration), never fabricate ports.
    """
    loaded = set(graph.list_vis())
    qname = name
    if qname not in loaded:
        try:
            resolved = graph.resolve_vi_name(name)
        except (KeyError, ValueError):
            resolved = None
        if resolved in loaded:
            qname = resolved  # type: ignore[assignment]

    if qname in loaded:
        sctx = graph.get_vi_context(qname)
        ins = [
            ComponentPort(
                name=t.name or str(t.index),
                type=t.lv_type.type_descriptor() if t.lv_type else "Any",
            )
            for t in sctx.inputs
        ]
        outs = [
            ComponentPort(
                name=t.name or str(t.index),
                type=t.lv_type.type_descriptor() if t.lv_type else "Any",
            )
            for t in sctx.outputs
        ]
        return ins, outs

    entry = _get_vilib_resolver().resolve_by_name(name)
    if entry is not None and entry.terminals:
        # str(t.type): the vilib resolver's terminal type is frequently
        # unset for real entries (e.g. niDigital.* instrument handles) --
        # ``str(None) == "None"`` reproduces the exact text the old
        # f-string-based signature rendered, so non-verbose output (which
        # still calls through here) is byte-identical to before this
        # module existed.
        ins = [
            ComponentPort(name=t.name, type=str(t.type))
            for t in entry.terminals
            if t.direction == "input"
        ]
        outs = [
            ComponentPort(name=t.name, type=str(t.type))
            for t in entry.terminals
            if t.direction == "output"
        ]
        return ins, outs
    return None


def _render_ports(ins: list[ComponentPort], outs: list[ComponentPort]) -> str:
    """``"(a: T, b: U) -> (c: V)"`` -- ASCII-only, shared by describe's
    ``## Dependencies`` line and netlist's ``## Components`` declaration."""
    in_str = ", ".join(f"{p.name}: {p.type}" for p in ins)
    out_str = ", ".join(f"{p.name}: {p.type}" for p in outs)
    return f"({in_str}) -> ({out_str})"
