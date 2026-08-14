"""Construction mixin for InMemoryVIGraph.

Methods: _add_vi_to_graph, _build_structure_terminals, _format_lv_type_for_display.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import networkx as nx

from ..models import (
    CaseFrame,
    FPTerminal,
    LVType,
    Terminal,
    TunnelMode,
    TunnelTerminal,
    bundle_unbundle_name,
    control_type_to_lvtype,
    inplace_border_name,
)
from ..parser import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedFrontPanel,
)
from ..parser.models import ParsedConstant, ParsedType
from ..parser.node_types import (
    FeedbackNode,
    SelectNode,
    SubVINode,
)
from ..parser.node_types import (
    PrimitiveNode as ParserPrimitiveNode,
)
from ..parser.vi import _decode_element
from ..primitive_resolver import get_resolver as get_prim_resolver
from ..primitive_resolver import resolve_primitive
from ..type_defaults import get_default_for_type
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .builders import (
    DEFAULT_NODE_BUILD_HANDLER,
    NODE_BUILD_HANDLERS,
    REF_BUILD_HANDLERS,
    STRUCTURE_BUILD_HANDLERS,
    SUBVI_CALL_NODE_TYPES,
    GraphBuildContext,
)
from .core import _graph_node_to_op_kind
from .models import (
    AnyGraphNode,
    ConstantNode,
    StructureNode,
    VINode,
    WireEnd,
)

logger = logging.getLogger(__name__)

# All node types that represent a SubVI call (dynamic or static). Single source
# of truth lives with the SubVI build handler; callByRefNode is included for node
# creation/name resolution but excluded from _connect_subvi_calls (its callee is
# runtime-determined, no static enrichment is possible).
_SUBVI_CALL_NODE_TYPES = SUBVI_CALL_NODE_TYPES

# Subset of _SUBVI_CALL_NODE_TYPES that allow static callee enrichment.
_STATIC_SUBVI_CALL_NODE_TYPES: frozenset[str] = frozenset({
    "iUse", "polyIUse", "dynIUse", "callParentDynIUse",
})

# Dynamic-dispatch calls: the callee is a class method, so the owning class is
# the dispatch object's type (carried on the call's class-typed terminals)
# rather than a statically-linked path.
_DYNAMIC_DISPATCH_NODE_TYPES: frozenset[str] = frozenset({
    "dynIUse", "callParentDynIUse",
})


def _dispatch_class_names(terminals: list) -> list[str]:
    """Distinct owning-class names (``X.lvclass``) carried on a dynamic-dispatch
    call's terminals — the object type wired to the dispatch input/output. The
    generic root ("LabVIEW Object", which has no ``.lvclass`` suffix) is skipped,
    so only concrete classes are returned, most-specific first as they appear."""
    out: list[str] = []
    for t in terminals:
        lt = getattr(t, "lv_type", None)
        cls = getattr(lt, "classname", None) if lt is not None else None
        if cls and cls.endswith(".lvclass") and cls not in out:
            out.append(cls)
    return out


def decode_constant(
    const: ParsedConstant,
    lv_type: LVType | None = None,
) -> tuple[str, str]:
    """Decode a constant value to (python_type, human_readable_value).

    Args:
        const: The constant to decode
        lv_type: LVType from the graph (authoritative type info)
    """
    value = const.value

    if lv_type is not None:
        raw_bytes = bytes.fromhex(value)
        underlying = getattr(lv_type, "underlying_type", "")
        if underlying == "Boolean" and len(raw_bytes) > 1:
            return (lv_type.to_python(), "True" if any(raw_bytes) else "False")
        decoded, _ = _decode_element(raw_bytes, lv_type)
        py_type = lv_type.to_python()
        if decoded is not None:
            return (py_type, decoded)
        return (py_type, get_default_for_type(lv_type))

    return ("raw", value)


# Type categories for terminal matching
_TYPE_CATEGORIES = {
    # String types
    "string": "string", "String": "string",
    "SubString": "string",
    # Path
    "path": "path", "Path": "path",
    # Boolean
    "boolean": "boolean", "Boolean": "boolean",
    # Integer types
    "numint8": "numeric", "NumInt8": "numeric",
    "numint16": "numeric", "NumInt16": "numeric",
    "numint32": "numeric", "NumInt32": "numeric",
    "numint64": "numeric", "NumInt64": "numeric",
    "numuint8": "numeric", "NumUInt8": "numeric",
    "numuint16": "numeric", "NumUInt16": "numeric",
    "numuint32": "numeric", "NumUInt32": "numeric",
    "numuint64": "numeric", "NumUInt64": "numeric",
    # Float types
    "numfloat32": "numeric", "NumFloat32": "numeric",
    "numfloat64": "numeric", "NumFloat64": "numeric",
    "NumFloatExt": "numeric",
    # Complex types
    "NumComplex64": "numeric", "NumComplex128": "numeric",
    "NumComplexExt": "numeric",
    # Measurement / unit types
    "MeasureData": "numeric",
    "UnitUInt8": "numeric", "UnitUInt16": "numeric", "UnitUInt32": "numeric",
    # Array subtypes (kind=primitive but semantically array)
    "SubArray": "array", "Array": "array",
    # Variant
    "variant": "variant", "Variant": "variant", "LVVariant": "variant",
    # Void
    "void": "void", "Void": "void",
    # Refnum
    "Refnum": "refnum",
}

def _lv_type_category(underlying: str, kind: str) -> str:
    """Map LV type to a category for matching."""
    if kind == "cluster":
        return "cluster"
    if kind == "array":
        return "array"
    if kind in ("enum", "ring"):
        return "numeric"
    cat = _TYPE_CATEGORIES.get(underlying)
    if cat:
        return cat
    ul = underlying.lower()
    if "refnum" in ul or "ref" in ul:
        return "refnum"
    if ul.startswith("num") or ul.startswith("unit"):
        return "numeric"
    return "unknown"


class ConstructionMixin:
    """Mixin providing graph construction methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _term_to_node: dict[str, str]

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        @staticmethod
        def _qid(_vi_name: str, _uid: str) -> str: ...
        def _enrich_type(
            self, _parsed_type: ParsedType | None,
        ) -> LVType | None: ...
        def resolve_vi_name(self, _vi_name: str) -> str: ...

    @staticmethod
    def _enrich_nmux_terminals(
        node: SelectNode, graph_node: AnyGraphNode,
    ) -> None:
        """Mark agg/list roles and field indices on nMux terminals.

        Only nMux (Select) nodes reach this, which are always built as
        primitive graph nodes; the wider type is for the post-dispatch
        call site where the static type is the full node union.
        """
        if not node.dco_agg_uid:
            return
        agg_dco = node.dco_agg_uid
        list_dcos = set(node.dco_list_uids)
        for term in graph_node.terminals:
            raw_tid = term.id.split("::")[-1] if "::" in term.id else term.id
            dco_uid = node.term_to_dco.get(raw_tid)
            if dco_uid == agg_dco:
                term.nmux_role = "agg"
            elif dco_uid in list_dcos:
                term.nmux_role = "list"
                if dco_uid in node.dco_field_index:
                    term.nmux_field_index = node.dco_field_index[dco_uid]

    @staticmethod
    def _format_lv_type_for_display(lv_type: LVType) -> str:
        """Format LVType for human-readable display."""
        if lv_type.kind == "primitive":
            return lv_type.underlying_type or "Any"
        elif lv_type.kind == "enum":
            if lv_type.typedef_name:
                name = lv_type.typedef_name.split(":")[-1].replace(".ctl", "")
                return name
            return "Enum"
        elif lv_type.kind == "cluster":
            if lv_type.typedef_name:
                name = lv_type.typedef_name.split(":")[-1].replace(".ctl", "")
                return name
            return "Cluster"
        elif lv_type.kind == "array":
            if lv_type.element_type:
                elem = ConstructionMixin._format_lv_type_for_display(
                    lv_type.element_type
                )
                return f"Array[{elem}]"
            return "Array"
        elif lv_type.kind == "ring":
            return "Ring"
        elif lv_type.kind == "typedef_ref":
            if lv_type.typedef_name:
                name = lv_type.typedef_name.split(":")[-1].replace(".ctl", "")
                return name
            return "TypeDef"
        else:
            return lv_type.underlying_type or "Any"

    # === Graph Construction ===

    def _add_vi_to_graph(
        self,
        bd: ParsedBlockDiagram,
        fp: ParsedFrontPanel | None,
        conpane: ParsedConnectorPane | None,
        wiring_rules: dict[int, int],
        vi_name: str,
        type_map: dict[int, LVType] | None = None,
        iuse_to_qname: dict[str, str] | None = None,
        iuse_to_qpath: dict[str, str] | None = None,
    ) -> None:
        """Add a VI's nodes and edges to the unified graph.

        Creates typed graph nodes (VINode, ConstantNode, PrimitiveNode,
        StructureNode) and typed edges (WireEnd source/dest).

        iuse_to_qpath maps an iUse uid to its fully qualified on-disk path
        (e.g. "<vilib>/Utility/error.llb/Foo.vi"). Used to populate
        VINode.qualified_path so resolution diagnostics can point at the
        real source file.
        """
        if type_map is None:
            type_map = {}
        if iuse_to_qpath is None:
            iuse_to_qpath = {}

        g = self._graph
        vi_node_uids: set[str] = set()

        # term_lookup: terminal_uid -> WireEnd (for wiring)
        term_lookup: dict[str, WireEnd] = {}

        # === 1. Build VINode (FP terminals become terminals on this node) ===

        # Build FP control lookup
        fp_by_uid: dict[str, Any] = {}
        if fp:
            for ctrl in fp.controls:
                fp_by_uid[ctrl.uid] = ctrl

        # Build inner ddo_uid → fPDCO uid mapping for ctlRefConst resolution.
        # ctlRefConst.ddo_uid points to the inner ddo element inside an fPDCO;
        # we need to map that back to the fPDCO uid to find the FP terminal.
        ddo_to_fpdco: dict[str, str] = (
            {ctrl.ddo_uid: ctrl.uid for ctrl in fp.controls if ctrl.ddo_uid}
            if fp else {}
        )

        # Build connector pane lookup: fp_dco_uid -> slot index
        conpane_slots: dict[str, int] = {}
        if conpane:
            for slot in conpane.slots:
                if slot.fp_dco_uid:
                    conpane_slots[slot.fp_dco_uid] = slot.index

        # fp_dco_uid -> FP terminal WireEnd, for gRef (Local Variable) aliasing
        # below. A local variable references its control by paramIdx, which is
        # the control's position in the VI's FULL front-panel control list
        # (NOT the connector-pane slot — non-conpane controls have no slot yet
        # are still referenceable). We resolve paramIdx via fp.controls order.
        fp_dco_wire_ends: dict[str, WireEnd] = {}

        # Build FP terminals list for the VINode
        vi_terminals: list[Terminal] = []
        for fp_term in bd.fp_terminals:
            slot_index = conpane_slots.get(fp_term.fp_dco_uid)
            is_public = slot_index is not None or not conpane_slots
            direction = "output" if fp_term.is_indicator else "input"
            ctrl = fp_by_uid.get(fp_term.fp_dco_uid)
            wiring_rule = wiring_rules.get(slot_index, 0) if slot_index else 0

            # Resolve type
            lv_type = None
            control_type_str = ctrl.control_type if ctrl else None

            term_info = bd.terminal_info.get(fp_term.uid)
            if term_info and term_info.parsed_type:
                lv_type = self._enrich_type(term_info.parsed_type)

            if not lv_type and control_type_str:
                lv_type = control_type_to_lvtype(control_type_str)

            q_term_uid = self._qid(vi_name, fp_term.uid)
            terminal = FPTerminal(
                id=q_term_uid,
                index=slot_index if slot_index is not None else 0,
                direction=direction,
                name=ctrl.name if ctrl else fp_term.name,
                lv_type=lv_type,
                wiring_rule=wiring_rule,
                is_indicator=fp_term.is_indicator,
                is_public=is_public,
                fp_dco_uid=fp_term.fp_dco_uid,
                control_type=ctrl.control_type if ctrl else None,
                default_value=ctrl.default_value if ctrl else None,
                enum_values=ctrl.enum_values if ctrl else [],
            )
            vi_terminals.append(terminal)

            # Register in term_lookup for wire resolution. Owning node is the
            # VI itself (vi_node, built below once all FP terminals are
            # collected) — every VINode maps to "vi" per
            # _graph_node_to_op_kind, so that's used directly rather than
            # deferring until vi_node exists.
            term_lookup[fp_term.uid] = WireEnd(
                terminal_id=q_term_uid,
                node_id=vi_name,
                index=slot_index,
                name=ctrl.name if ctrl else fp_term.name,
                parent_kind="vi",
            )
            fp_dco_wire_ends[fp_term.fp_dco_uid] = term_lookup[fp_term.uid]

        # paramIdx -> FP terminal WireEnd, indexed by the control's position in
        # the front-panel control list (the order a local variable's paramIdx
        # refers to — verified type-consistent, unlike the conpane slot).
        param_wire_ends: dict[int, WireEnd] = {}
        if fp:
            for i, ctrl in enumerate(fp.controls):
                we = fp_dco_wire_ends.get(ctrl.uid)
                if we is not None:
                    param_wire_ends[i] = we

        # Create the VINode, carrying the VI's own documentation text (parsed
        # into _vi_metadata from STRG/DSTM) so its help panel can show it.
        _meta = getattr(self, "_vi_metadata", {}).get(vi_name)
        vi_node = VINode(
            id=vi_name,
            vi=vi_name,
            name=vi_name,
            terminals=vi_terminals,
            description=_meta.description if _meta else None,
            owning_libraries=list(_meta.owning_libraries) if _meta else [],
            connector_pattern_id=conpane.pattern_id if conpane else None,
        )
        g.add_node(vi_name, node=vi_node)
        vi_node_uids.add(vi_name)

        # === 2. Add Constants ===
        for const in bd.constants:
            lv_type = None
            term_info = bd.terminal_info.get(const.uid)
            if term_info and term_info.parsed_type:
                lv_type = self._enrich_type(term_info.parsed_type)

            _, decoded_value = decode_constant(const, lv_type=lv_type)

            q_const_uid = self._qid(vi_name, const.uid)
            # Single output terminal
            const_terminal = Terminal(
                id=q_const_uid,
                index=0,
                direction="output",
                lv_type=lv_type,
            )

            const_node = ConstantNode(
                id=q_const_uid,
                vi=vi_name,
                value=decoded_value,
                lv_type=lv_type,
                raw_value=const.value,
                label=const.label,
                display_format=const.display_format,
                terminals=[const_terminal],
            )
            g.add_node(q_const_uid, node=const_node)
            vi_node_uids.add(q_const_uid)

            term_lookup[const.uid] = WireEnd(
                terminal_id=q_const_uid,
                node_id=q_const_uid,
                index=0,
                name=const.label,
                parent_kind=_graph_node_to_op_kind(const_node),
            )

        # === 3. Add operations (SubVIs, primitives, structures) ===

        # Collect structure info indexed by UID for later use
        loop_by_uid = {loop.uid: loop for loop in bd.loops}
        case_by_uid = {cs.uid: cs for cs in bd.case_structures}
        flatseq_by_uid = {fs.uid: fs for fs in bd.flat_sequences}
        decompose_by_uid = {ds.uid: ds for ds in bd.decompose_structures}
        disable_by_uid = {ds.uid: ds for ds in bd.disable_structures}
        event_by_uid = {es.uid: es for es in bd.event_structures}

        # Structure nodes (loop/case/sequence/IPES/disable/event) are built by
        # registered per-kind handlers rather than an inlined if/elif — see
        # graph/builders/.
        build_ctx = GraphBuildContext(
            mixin=self, bd=bd, vi_name=vi_name, term_lookup=term_lookup,
            loop_by_uid=loop_by_uid, case_by_uid=case_by_uid,
            flatseq_by_uid=flatseq_by_uid, decompose_by_uid=decompose_by_uid,
            disable_by_uid=disable_by_uid, event_by_uid=event_by_uid,
            iuse_to_qname=iuse_to_qname or {}, iuse_to_qpath=iuse_to_qpath,
            fp=fp, ddo_to_fpdco=ddo_to_fpdco,
            param_wire_ends=param_wire_ends, vi_node_uids=vi_node_uids,
        )

        for node in bd.nodes:
            q_node_uid = self._qid(vi_name, node.uid)

            # Reference-style nodes (ctlRefConst / gRef / statVIRef) fully
            # resolve here — aliasing an FP terminal, adding a LocalVariable/
            # Constant node — then the loop skips normal node building. See
            # graph/builders/refs.py.
            ref_handler = REF_BUILD_HANDLERS.get(node.node_type)
            if ref_handler is not None and ref_handler.handle(
                node, q_node_uid, build_ctx,
            ):
                continue

            # Get known terminal layout for index matching.
            # Same system for all node types: primitives, node_types, vilib SubVIs.
            known_terminals = None
            if isinstance(node, ParserPrimitiveNode) and node.prim_res_id:
                prim_resolved = resolve_primitive(prim_id=node.prim_res_id)
                if prim_resolved and prim_resolved.terminals:
                    known_terminals = prim_resolved.terminals
            if not known_terminals and node.node_type:
                nt_resolved = get_prim_resolver().resolve_by_node_type(node.node_type)
                if nt_resolved and nt_resolved.terminals:
                    known_terminals = nt_resolved.terminals
            if not known_terminals and isinstance(node, SubVINode):
                # SubVI calls: look up vilib terminal layout
                vilib_r = get_vilib_resolver()
                subvi_name = node.name or ""
                # Polymorphic: resolve to variant
                if node.poly_variant_name:
                    vilib_vi = vilib_r.resolve_poly_variant(
                        subvi_name, node.poly_variant_name
                    )
                else:
                    vilib_vi = None
                if not vilib_vi:
                    vilib_vi = vilib_r.resolve_by_name(subvi_name)
                if vilib_vi and vilib_vi.terminals:
                    known_terminals = vilib_vi.terminals

            # Collect terminals, then resolve unknown indices by elimination
            raw_terms: list[tuple[str, Any, LVType | None]] = []
            for term_uid, t_info in bd.terminal_info.items():
                if t_info.parent_uid == node.uid:
                    lv_type = None
                    if t_info.parsed_type:
                        lv_type = self._enrich_type(t_info.parsed_type)
                    raw_terms.append((term_uid, t_info, lv_type))

            # Resolve -1 indices by type+direction matching
            if known_terminals:
                self._resolve_terminal_indices(
                    [(t_info, lv_type) for _, t_info, lv_type in raw_terms],
                    known_terminals,
                )

            # Surface the resolved definition's terminal NAMES (primitives.json
            # / node-type / vilib) as a DISPLAY-ONLY label, keyed by the
            # (now-resolved) connector-pane index. Kept separate from ``name``
            # because ``name`` feeds codegen variable naming — this must not
            # change generated code. Feeds the connector-pane hover, the
            # <title> tooltip, and describe (real "x"/"y"/"difference" instead
            # of "terminal N"); the caller almost never labels a primitive's
            # terminals, so without this they are anonymous.
            known_names: dict[int, str] = {}
            if known_terminals:
                for kt in known_terminals:
                    if kt.name and kt.index is not None:
                        known_names[kt.index] = kt.name

            # Snapshot of term_lookup keys before this node registers any of
            # its own (raw_terms below, plus — for structures — the tunnel/
            # sRN terminals _build_structure_terminals registers during the
            # handler dispatch further down). Used after graph_node exists to
            # backfill parent_kind on exactly the entries this node added —
            # the node object isn't available yet at either registration site.
            _term_lookup_keys_before = set(term_lookup)

            node_terminals: list[Terminal] = []
            for term_uid, t_info, lv_type in raw_terms:
                q_term_uid = self._qid(vi_name, term_uid)
                terminal = Terminal(
                    id=q_term_uid,
                    index=t_info.index,
                    direction="output" if t_info.is_output else "input",
                    name=t_info.name,
                    display_name=t_info.name or known_names.get(t_info.index),
                    lv_type=lv_type,
                    inverted=t_info.inverted,
                )
                node_terminals.append(terminal)

                term_lookup[term_uid] = WireEnd(
                    terminal_id=q_term_uid,
                    node_id=q_node_uid,
                    index=t_info.index,
                    name=t_info.name,
                )

            node_terminals.sort(key=lambda t: t.index)

            # Resolve node name. A specialized node_type (subset, aIndx, ...)
            # OWNS its name via its parser handler's display_name; only a
            # generic class="prim" node resolves its name from primResID. This
            # matters because some primResIDs are SHARED across XML classes
            # (e.g. 1516 = both Select for class="prim" and Array Subset for
            # class="subset", 1809 = both Array Size and Index Array), so a
            # prim_id lookup would mislabel the specialized node. Mirrors
            # codegen's node_type-first rule (codegen/nodes/primitive.py).
            node_name = node.name
            if (
                isinstance(node, ParserPrimitiveNode)
                and node.prim_res_id
                and node.node_type == "prim"
            ):
                resolved = resolve_primitive(prim_id=node.prim_res_id)
                if resolved:
                    node_name = resolved.name

            if not node_name and node.node_type:
                resolved_nt = get_prim_resolver().resolve_by_node_type(node.node_type)
                if resolved_nt:
                    node_name = resolved_nt.name

            # For older VIs, node_name may be a generic placeholder ("SubVI",
            # "VI Refnum", etc.) because pylabview cannot decode binary textRec
            # indices. Fall back to the iUse UID → qualified name map from LIbd.
            if node.node_type in _SUBVI_CALL_NODE_TYPES:
                iuse_resolved = (iuse_to_qname or {}).get(node.uid)
                _is_placeholder = not node_name or node_name == "SubVI"
                if iuse_resolved and _is_placeholder:
                    node_name = iuse_resolved

            # Get description for SubVIs from vilib
            description = None
            if node.node_type in _SUBVI_CALL_NODE_TYPES and node_name:
                vilib_r = get_vilib_resolver()
                vi_entry = vilib_r.resolve_by_name(node_name)
                if vi_entry and vi_entry.description:
                    description = vi_entry.description

            # Determine what kind of graph node to create. SubVI calls, fBox,
            # and primitives/cpdArith/property/invoke all go through the
            # operation registry (subvi + fBox keyed; default = primitive);
            # loop/case/sequence/IPES go through the structure registry.
            graph_node: AnyGraphNode
            if node.node_type in STRUCTURE_BUILD_HANDLERS:
                # Loop / case / sequence / IPES — built by a registered
                # per-kind handler (graph/builders/structures.py). Shared
                # post-dispatch (nMux enrichment, g.add_node) is below.
                graph_node = STRUCTURE_BUILD_HANDLERS[node.node_type].build(
                    node, node_name, q_node_uid, build_ctx,
                )
            else:
                # Operation node (formula box, primitive, cpdArith, property/
                # invoke) — built by a registered handler off the ordinary
                # node_terminals, else the default primitive handler.
                op_handler = NODE_BUILD_HANDLERS.get(
                    node.node_type, DEFAULT_NODE_BUILD_HANDLER,
                )
                graph_node = op_handler.build(
                    node, node_name, q_node_uid, node_terminals, description,
                    build_ctx,
                )

            # IPES DVR / array border tiles (plain PrimitiveNodes, no field
            # shape): name each tile by its read/write role from the terminal
            # types -- the same single-source rename seam as the cluster border
            # node below, so "In Place Element" only survives as a fallback.
            border_name = inplace_border_name(
                graph_node.node_type or "", graph_node.terminals,
            )
            if border_name is not None:
                graph_node.name = border_name

            # Backfill parent_kind on every term_lookup entry this node just
            # registered for itself (see snapshot above) now that graph_node
            # exists. Covers both the raw per-node terminal loop and, for
            # structures, the tunnel/sRN terminals _build_structure_terminals
            # registered during handler dispatch.
            _new_term_lookup_keys = set(term_lookup) - _term_lookup_keys_before
            if _new_term_lookup_keys:
                _node_kind = _graph_node_to_op_kind(graph_node)
                for _t_uid in _new_term_lookup_keys:
                    _we = term_lookup[_t_uid]
                    if _we.parent_kind is None:
                        term_lookup[_t_uid] = _we.model_copy(
                            update={"parent_kind": _node_kind}
                        )

            # Mark nMux terminal roles (agg vs list) and field indices
            if isinstance(node, SelectNode):
                self._enrich_nmux_terminals(node, graph_node)
                if graph_node.node_type == "decomposeClusterNode":
                    # The IPES cluster border node — Bundle/Unbundle BY NAME,
                    # direction read off the FIELD terminals just enriched
                    # above (same rule render.nodes.mux_display_name uses).
                    # Fixed here, at the one place every consumer (render
                    # header, describe, netlist) reads ``graph_node.name``
                    # from, so "decompose" jargon never leaks to any of them.
                    renamed = bundle_unbundle_name(
                        graph_node.terminals, by_name=True,
                    )
                    if renamed is not None:
                        graph_node.name = renamed
                if node.poser_uid:
                    g.add_node(q_node_uid, node=graph_node, poser_uid=node.poser_uid)
                    vi_node_uids.add(q_node_uid)
                    continue

            # Feedback Node (hiddenFBNode master / slaveFBInputNode write side):
            # stash the master<->slave link + z^-N delay as node attributes so
            # _build_operation can lift them onto a FeedbackOperation. The graph
            # node itself stays a GraphPrimitiveNode, so render/codegen treat it
            # exactly as before — only the Operation layer gains feedback
            # identity. See parser FeedbackNode and models.FeedbackOperation.
            if isinstance(node, FeedbackNode):
                g.add_node(
                    q_node_uid,
                    node=graph_node,
                    feedback_is_master=node.is_master,
                    feedback_partner=(
                        self._qid(vi_name, node.partner_uid)
                        if node.partner_uid else None
                    ),
                    feedback_delay=node.delay_depth,
                )
                vi_node_uids.add(q_node_uid)
                continue

            g.add_node(q_node_uid, node=graph_node)
            vi_node_uids.add(q_node_uid)

        # === 4. Set parent/frame on inner operation nodes ===
        # After all nodes are created, walk parser structures and stamp
        # containment info on the graph nodes they own.
        #
        # frame_owner maps each structure inner-node UID (operations AND the
        # per-frame sRN boundary nodes) to (structure_id, frame_key). It lets
        # us attribute *constants* to their frame below: a diagram constant
        # nested inside a frame carries an sRN as its terminal's parent_uid,
        # and that sRN is one of the frame's inner_node_uids.
        frame_owner: dict[str, tuple[str, str | int | None]] = {}

        def _stamp(uid: str, struct_id: str, frame_key: str | int | None) -> None:
            frame_owner[uid] = (struct_id, frame_key)
            q_uid = self._qid(vi_name, uid)
            if q_uid in g and "node" in g.nodes[q_uid]:
                inner_node = g.nodes[q_uid]["node"]
                inner_node.parent = struct_id
                inner_node.frame = frame_key

        for loop in bd.loops:
            q_loop_uid = self._qid(vi_name, loop.uid)
            for uid in loop.inner_node_uids:
                _stamp(uid, q_loop_uid, None)

        for cs in bd.case_structures:
            q_cs_uid = self._qid(vi_name, cs.uid)
            for frame in cs.frames:
                for uid in frame.inner_node_uids:
                    _stamp(uid, q_cs_uid, frame.selector_value)

        for fs in bd.flat_sequences:
            q_fs_uid = self._qid(vi_name, fs.uid)
            for idx, frame in enumerate(fs.frames):
                for uid in frame.inner_node_uids:
                    _stamp(uid, q_fs_uid, str(idx))

        for ds in bd.decompose_structures:
            q_ds_uid = self._qid(vi_name, ds.uid)
            for uid in ds.inner_node_uids:
                _stamp(uid, q_ds_uid, None)

        for disable in bd.disable_structures:
            q_disable_uid = self._qid(vi_name, disable.uid)
            for frame in disable.frames:
                for uid in frame.inner_node_uids:
                    _stamp(uid, q_disable_uid, frame.selector_value)

        # Event structure frames are keyed by INDEX (str(idx)) — like a
        # stacked sequence, not a case — since the active frame is chosen at
        # runtime by whichever event fires, not a selector wire/value.
        for es in bd.event_structures:
            q_es_uid = self._qid(vi_name, es.uid)
            for idx, frame in enumerate(es.frames):
                for uid in frame.inner_node_uids:
                    _stamp(uid, q_es_uid, str(idx))

        # Attribute constants to the frame that contains them. A constant
        # wired inside a structure is a diagram constant whose output
        # terminal (keyed by the constant UID) is parented to the frame's
        # sRN boundary node. Map: constant -> its terminal's parent -> frame.
        for const in bd.constants:
            ct = bd.terminal_info.get(const.uid)
            if ct is None or ct.parent_uid not in frame_owner:
                continue
            q_const_uid = self._qid(vi_name, const.uid)
            if q_const_uid in g and "node" in g.nodes[q_const_uid]:
                struct_id, frame_key = frame_owner[ct.parent_uid]
                cnode = g.nodes[q_const_uid]["node"]
                cnode.parent = struct_id
                cnode.frame = frame_key

        # Attribute FP terminals (front-panel controls placed on a diagram
        # via an sRN — e.g. an Event Structure's registered event-source
        # control glyph) to the frame that contains THAT placement. Same
        # shape as the constants loop just above: the terminal's own
        # terminal_info.parent_uid points to the owning sRN, already stamped
        # into frame_owner by the loops above. FPTerminal isn't a GraphNode
        # (one FPTerminal is shared VI-wide, not one per placement — a
        # control can be referenced from multiple frames), so the
        # attribution is stamped directly on the Terminal rather than via
        # ``_stamp``. Without this, render/scene.py has no structural
        # signal for these terminals and falls back to inferring frame scope
        # from wire connectivity — which is wrong/absent for a control used
        # purely as an event source (no ordinary data wire into the frame),
        # making its glyph (and any wire touching it) render as always-
        # visible base content instead of scoped to its real frame.
        vi_terminal_by_id = {
            t.id: t for t in vi_node.terminals if isinstance(t, FPTerminal)
        }
        for fp_term in bd.fp_terminals:
            ct = bd.terminal_info.get(fp_term.uid)
            if ct is None or ct.parent_uid not in frame_owner:
                continue
            terminal = vi_terminal_by_id.get(self._qid(vi_name, fp_term.uid))
            if terminal is None:
                continue
            struct_id, frame_key = frame_owner[ct.parent_uid]
            terminal.parent = struct_id
            terminal.frame = frame_key

        # === 5. Register remaining terminal_info entries in term_lookup ===
        # Most tunnel/sRN terminals are already registered by
        # _build_structure_terminals. This catches any stragglers whose
        # parent is not a recognized graph node (e.g., orphan sRN
        # terminals not referenced by any tunnel).
        for term_uid, t_info in bd.terminal_info.items():
            if term_uid not in term_lookup:
                q_term_uid = self._qid(vi_name, term_uid)
                parent_uid = t_info.parent_uid
                q_parent_uid = (
                    self._qid(vi_name, parent_uid) if parent_uid else None
                )
                effective_parent = q_parent_uid
                # If parent is not a graph node, find the structure
                # that contains it. Check both terminal lists and
                # parser structure inner_node_uids (catches sRNs
                # not referenced by tunnels).
                if q_parent_uid and q_parent_uid not in g:
                    # First: check structure terminal lists
                    for s_uid in vi_node_uids:
                        if s_uid not in g:
                            continue
                        snode = g.nodes[s_uid].get("node")
                        if isinstance(snode, StructureNode):
                            for st in snode.terminals:
                                if st.id == q_term_uid:
                                    effective_parent = s_uid
                                    break
                            if effective_parent == s_uid:
                                break
                    # Second: check parser structures for containment
                    if effective_parent == q_parent_uid:
                        for cs in bd.case_structures:
                            for frame in cs.frames:
                                if parent_uid in frame.inner_node_uids:
                                    effective_parent = self._qid(vi_name, cs.uid)
                                    break
                            if effective_parent != q_parent_uid:
                                break
                    if effective_parent == q_parent_uid:
                        for loop in bd.loops:
                            if parent_uid in loop.inner_node_uids:
                                effective_parent = self._qid(vi_name, loop.uid)
                                break
                    if effective_parent == q_parent_uid:
                        for fs in bd.flat_sequences:
                            for frame in fs.frames:
                                if parent_uid in frame.inner_node_uids:
                                    effective_parent = self._qid(vi_name, fs.uid)
                                    break
                            if effective_parent != q_parent_uid:
                                break
                    if effective_parent == q_parent_uid:
                        for ds in bd.decompose_structures:
                            if parent_uid in ds.inner_node_uids:
                                effective_parent = self._qid(vi_name, ds.uid)
                                break
                    if effective_parent == q_parent_uid:
                        for disable in bd.disable_structures:
                            for frame in disable.frames:
                                if parent_uid in frame.inner_node_uids:
                                    effective_parent = self._qid(
                                        vi_name, disable.uid,
                                    )
                                    break
                            if effective_parent != q_parent_uid:
                                break
                    if effective_parent == q_parent_uid:
                        for es in bd.event_structures:
                            for frame in es.frames:
                                if parent_uid in frame.inner_node_uids:
                                    effective_parent = self._qid(vi_name, es.uid)
                                    break
                            if effective_parent != q_parent_uid:
                                break

                owner_node_id = effective_parent or q_term_uid
                owner_node = g.nodes.get(owner_node_id, {}).get("node")
                term_lookup[term_uid] = WireEnd(
                    terminal_id=q_term_uid,
                    node_id=owner_node_id,
                    index=t_info.index,
                    name=t_info.name,
                    parent_kind=(
                        _graph_node_to_op_kind(owner_node) if owner_node else None
                    ),
                )

        # === 6. Add edges (wires) ===
        for wire in bd.wires:
            src_end = term_lookup.get(wire.from_term)
            dst_end = term_lookup.get(wire.to_term)

            if src_end is None:
                q_from = self._qid(vi_name, wire.from_term)
                src_end = WireEnd(
                    terminal_id=q_from,
                    node_id=q_from,
                )
            if dst_end is None:
                q_to = self._qid(vi_name, wire.to_term)
                dst_end = WireEnd(
                    terminal_id=q_to,
                    node_id=q_to,
                )

            g.add_edge(
                src_end.node_id,
                dst_end.node_id,
                source=src_end,
                dest=dst_end,
                vi=vi_name,
            )

        # === 7. Connect SubVI call terminals to callee FP terminals ===
        # Callees are already loaded (topological order), so their FP
        # terminals are in the graph. Create edges so types propagate — and copy
        # each callee's connector-pane control label onto the call terminal so
        # hovers read real names, not "terminal N". Runs whenever there are SubVI
        # call nodes: ``_connect_subvi_calls`` resolves the callee from the call
        # node's own name (``gnode.name``), using ``iuse_to_qname`` only as a
        # fallback for placeholder-named iUse nodes — so an EMPTY iuse map (e.g. a
        # class method whose LIvi records no iUse→qname entry) must NOT skip it.
        self._connect_subvi_calls(vi_name, vi_node_uids, iuse_to_qname or {})

        # === 8. Propagate types through wires and re-match indices ===
        # Now follows edges ACROSS VI boundaries too.
        self._propagate_types_and_rematch(g, vi_node_uids)

        # Store per-VI node index
        self._vi_nodes[vi_name] = vi_node_uids

        # Populate terminal ownership from term_lookup
        for _raw_tid, wire_end in term_lookup.items():
            self._term_to_node[wire_end.terminal_id] = wire_end.node_id

    def resolve_dispatch_qnames(self) -> None:
        """Class-qualify dynamic-dispatch calls across the whole graph.

        A dispatch call carries only the bare method name; the owning class is
        the dispatch object's static type, which lands on the call's terminals
        only after every VI is loaded and types have propagated (the object may
        itself come from another dispatch call — a cross-VI fixpoint). So this
        runs ONCE after loading finishes: link an ``addError.vi`` call whose
        object is typed ``TestResult.lvclass`` to ``TestResult.lvclass:addError.vi``
        when that method VI is loaded. Idempotent."""
        g = self._graph
        for _nid, data in g.nodes(data=True):
            gnode = data.get("node")
            if (
                not isinstance(gnode, VINode)
                or gnode.node_type not in _DYNAMIC_DISPATCH_NODE_TYPES
                or not gnode.name
                or gnode.qualified_name != gnode.name
            ):
                continue
            for cls in _dispatch_class_names(gnode.terminals):
                cand = self.resolve_vi_name(f"{cls}:{gnode.name}")
                if cand and cand in g:
                    gnode.qualified_name = cand
                    break

    def _connect_subvi_calls(
        self,
        vi_name: str,
        vi_node_uids: set[str],
        iuse_to_qname: dict[str, str],
    ) -> None:
        """Create dataflow edges from SubVI call terminals to callee FP terminals.

        Callees are already in the graph (topological load order).
        Match by terminal index. Types then propagate across VI boundaries
        through normal wire-following — no special cross-VI logic needed.
        """
        g = self._graph
        for nid in vi_node_uids:
            gnode = g.nodes.get(nid, {}).get("node")
            if not isinstance(gnode, VINode) or gnode.id == vi_name:
                continue
            # callByRefNode excluded — callee is runtime-determined, no static
            # enrichment possible. Only iUse/polyIUse/dynIUse/callParentDynIUse.
            if gnode.node_type not in _STATIC_SUBVI_CALL_NODE_TYPES:
                continue

            # Resolve callee VI name
            raw_uid = nid.split("::")[-1] if "::" in nid else nid
            callee_qname = iuse_to_qname.get(raw_uid, gnode.name or "")
            callee_name = self.resolve_vi_name(callee_qname)
            if not callee_name or callee_name not in g:
                continue

            callee_node = g.nodes[callee_name].get("node")
            if not isinstance(callee_node, VINode):
                continue

            # A SubVI call node has no documentation of its own — carry the
            # callee VI's description onto it so its hover help panel shows what
            # the SubVI does (same enrich-from-callee pattern as terminal names).
            if not gnode.description and callee_node.description:
                gnode.description = callee_node.description
            # ...and its fully qualified name (Class.lvclass:vi.vi), so the hover
            # title disambiguates a bare leaf name.
            if not gnode.qualified_name and callee_qname:
                gnode.qualified_name = callee_qname
            # ...and the callee's OWNERSHIP CHAIN (from the callee's own <LIBN>),
            # so a SubVI-CALL label can show ``Class.lvclass:method`` — the whole
            # point of qualifying two classes' same-named methods.
            if not gnode.owning_libraries and callee_node.owning_libraries:
                gnode.owning_libraries = list(callee_node.owning_libraries)

            # Build callee terminal lookup: (index, direction) → Terminal
            callee_term_map: dict[tuple[int, str], Any] = {}
            for t in callee_node.terminals:
                if t.index is not None and t.index >= 0:
                    callee_term_map[(t.index, t.direction)] = t

            # Connect matching terminals and enrich caller from callee
            matched_callee: set[str] = set()
            # First pass: match terminals with known indices
            for call_term in gnode.terminals:
                if call_term.index is None or call_term.index < 0:
                    continue
                callee_key = (call_term.index, call_term.direction)
                callee_t = callee_term_map.get(callee_key)
                if callee_t:
                    matched_callee.add(callee_t.id)

            for call_term in gnode.terminals:
                callee_t = None
                if call_term.index is not None and call_term.index >= 0:
                    callee_t = callee_term_map.get(
                        (call_term.index, call_term.direction)
                    )
                else:
                    # idx=-1: match by elimination — find unmatched callee
                    # terminal with same direction
                    unmatched = [
                        t for (_, d), t in callee_term_map.items()
                        if d == call_term.direction and t.id not in matched_callee
                    ]
                    if len(unmatched) == 1:
                        callee_t = unmatched[0]
                        call_term.index = callee_t.index

                if not callee_t:
                    continue
                matched_callee.add(callee_t.id)

                # Enrich: copy name and type from callee FP terminal
                if not call_term.name and callee_t.name:
                    call_term.name = callee_t.name
                if not call_term.lv_type and callee_t.lv_type:
                    call_term.lv_type = callee_t.lv_type
                elif (call_term.lv_type and callee_t.lv_type
                      and not call_term.lv_type.fields and callee_t.lv_type.fields):
                    call_term.lv_type.fields = callee_t.lv_type.fields

                # Create dataflow edge
                src_we = WireEnd(terminal_id=call_term.id, node_id=nid,
                                 index=call_term.index, name=call_term.name,
                                 parent_kind=_graph_node_to_op_kind(gnode))
                dst_we = WireEnd(terminal_id=callee_t.id, node_id=callee_name,
                                 index=call_term.index, name=callee_t.name,
                                 parent_kind=_graph_node_to_op_kind(callee_node))
                if call_term.direction == "input":
                    g.add_edge(nid, callee_name, source=src_we, dest=dst_we)
                else:
                    g.add_edge(callee_name, nid, source=dst_we, dest=src_we)

    @staticmethod
    def _resolve_terminal_indices(
        raw_terms: list[tuple[Any, LVType | None]],
        known_terminals: list,
    ) -> None:
        """Resolve -1 indices. Direct match by type+direction or bust.

        For each unresolved parser terminal: find the ONE resolver
        terminal with matching direction AND type category. If exactly
        one match, assign. Otherwise leave -1.
        """
        assigned_indices: set[int] = set()
        for t_info, _ in raw_terms:
            if t_info.index >= 0:
                assigned_indices.add(t_info.index)

        for t_info, lv_type in raw_terms:
            if t_info.index >= 0:
                continue
            if not lv_type or not lv_type.underlying_type:
                continue

            prim_dir = "out" if t_info.is_output else "in"
            cat = _lv_type_category(lv_type.underlying_type, lv_type.kind)
            if cat in ("unknown", "void"):
                continue

            # Find resolver terminals: same direction, same type, not taken.
            # "polymorphic" matches any parser category.
            matches = [
                pt for pt in known_terminals
                if pt.direction == prim_dir
                and pt.index not in assigned_indices
                and (pt.type == cat or pt.type == "polymorphic")
            ]

            if len(matches) == 1:
                t_info.index = matches[0].index
                assigned_indices.add(matches[0].index)
            elif len(matches) != 1:
                # Check for expandable terminal — all unresolved terminals of
                # matching type map to the expandable slot's index
                expandable = [
                    pt for pt in known_terminals
                    if pt.direction == prim_dir
                    and getattr(pt, "expandable", False)
                    and (pt.type == cat or pt.type == "polymorphic")
                ]
                if len(expandable) == 1:
                    t_info.index = expandable[0].index
                    # Don't add to assigned — expandable can be reused

    def _propagate_types_and_rematch(
        self, g: nx.MultiDiGraph, vi_node_uids: set[str],
    ) -> None:
        """Propagate types through wires, then re-match -1 index terminals.

        Same pattern as name resolution — follow the graph for types.
        """
        # Resolve each wire's (src_term, dst_term) pair ONCE, via a per-node
        # {terminal_id: terminal} index (O(1)), then run the type-propagation
        # fixpoint over that flat list. The terminal *objects* are stable — only
        # their .lv_type mutates — so resolving once is safe. The old code
        # re-walked g.edges() AND linearly scanned node.terminals on every pass
        # (O(passes x edges x terminals), the profile's ~820K generator calls);
        # this is O(edges) to resolve + O(passes x edges) cheap attribute checks.
        node_terms: dict[str, dict[str, Terminal]] = {}

        def _term(node_id: str, term_id: str) -> Terminal | None:
            idx = node_terms.get(node_id)
            if idx is None:
                node = g.nodes.get(node_id, {}).get("node")
                idx = {t.id: t for t in node.terminals} if node else {}
                node_terms[node_id] = idx
            return idx.get(term_id)

        # Scope to THIS VI's wires (out-edges of its own nodes), not the whole
        # shared graph. The old `g.edges()` rescanned EVERY loaded VI's edges on
        # every per-VI call — O(VIs x total_edges), the super-linear cost that
        # made a 487-VI build 11x a 106-VI one. Intra-VI wires have both ends in
        # vi_node_uids, so out-edges of vi_node_uids cover this VI's wires exactly
        # once; other VIs' wires were already propagated in their own calls.
        pairs: list[tuple[Terminal, Terminal]] = []
        for nid in vi_node_uids:
            for _u, _v, _k, d in g.out_edges(nid, data=True, keys=True):
                src = d.get("source")
                dst = d.get("dest")
                if not src or not dst:
                    continue
                src_term = _term(src.node_id, src.terminal_id)
                dst_term = _term(dst.node_id, dst.terminal_id)
                if src_term is not None and dst_term is not None:
                    pairs.append((src_term, dst_term))

        # Propagate: if one side of a wire has lv_type and the other doesn't
        changed = True
        while changed:
            changed = False
            for src_term, dst_term in pairs:
                if src_term.lv_type and not dst_term.lv_type:
                    dst_term.lv_type = src_term.lv_type
                    changed = True
                elif dst_term.lv_type and not src_term.lv_type:
                    src_term.lv_type = dst_term.lv_type
                    changed = True
                # Both have type but one has fields and the other doesn't:
                # enrich the incomplete side (same wire = same type)
                elif (src_term.lv_type and dst_term.lv_type
                      and src_term.lv_type.kind == dst_term.lv_type.kind == "cluster"):
                    if src_term.lv_type.fields and not dst_term.lv_type.fields:
                        dst_term.lv_type.fields = src_term.lv_type.fields
                        changed = True
                    elif dst_term.lv_type.fields and not src_term.lv_type.fields:
                        src_term.lv_type.fields = dst_term.lv_type.fields
                        changed = True

        # Re-match: for nodes with -1 index terminals, retry elimination
        # now that type propagation has filled in more lv_types
        for nid in vi_node_uids:
            gnode = g.nodes.get(nid, {}).get("node")
            if not gnode:
                continue
            if not any(t.index == -1 for t in gnode.terminals):
                continue

            prim_terminals = None
            if hasattr(gnode, 'prim_id') and gnode.prim_id:
                prim_resolved = resolve_primitive(prim_id=gnode.prim_id)
                if prim_resolved and prim_resolved.terminals:
                    prim_terminals = prim_resolved.terminals

            if not prim_terminals:
                continue

            # Build fake raw_terms for elimination
            fake_terms = []
            for t in gnode.terminals:
                fake_ti = SimpleNamespace(
                    index=t.index,
                    is_output=t.direction == 'output',
                )
                fake_terms.append(('', fake_ti, t.lv_type))

            self._resolve_terminal_indices(fake_terms, prim_terminals)

            # Apply resolved indices back
            for (_, fake_ti, _), t in zip(fake_terms, gnode.terminals):
                if t.index == -1 and fake_ti.index >= 0:
                    t.index = fake_ti.index

    _INPUT_TUNNEL_TYPES = frozenset({
        "lSR", "lMax", "lpTun", "caseSel", "seqTun", "flatSeqTun",
    })

    def _build_structure_terminals(
        self,
        bd: ParsedBlockDiagram,
        parser_tunnels: list,
        structure_uid: str,
        term_lookup: dict[str, WireEnd],
        vi_name: str = "",
        case_frames: list[CaseFrame] | None = None,
    ) -> list[Terminal]:
        """Build Terminal list for a StructureNode from its tunnels and sRN nodes.

        Each parser tunnel creates TWO Terminal objects:
        - Outer terminal (boundary="outer")
        - Inner terminal (boundary="inner")

        Also maps sRN-owned terminals to the structure and creates
        internal edges (self-loops) on the graph for:
        - Tunnel outer<->inner connections
        - sRN input->output pairings

        For case structures, a single outer tunnel has one inner tunnel
        PER FRAME (parser_tunnels lists them grouped by outer_terminal_uid,
        in frame order — see case.py::_extract_case_tunnels). When
        case_frames is provided, each inner TunnelTerminal is stamped with
        the selector_value of the frame it belongs to, correlated
        positionally: the Nth tunnel entry sharing an outer_terminal_uid
        belongs to case_frames[N]. This lets renderers exclude tunnel wires
        belonging to hidden frames.

        Returns the complete terminal list for the StructureNode.
        """
        g = self._graph
        structure_terminals: list[Terminal] = []
        seen_uids: set[str] = set()
        # Per-outer-tunnel-uid occurrence counter, used to correlate each
        # case-structure inner tunnel to its owning frame by position.
        outer_tunnel_position: dict[str, int] = {}

        # Collect known parser node UIDs for sRN detection
        known_node_uids = {n.uid for n in bd.nodes}

        # --- 1. Build terminals from tunnel mappings ---
        for tunnel in parser_tunnels:
            outer_uid = tunnel.outer_terminal_uid
            inner_uid = tunnel.inner_terminal_uid
            ttype = tunnel.tunnel_type

            if not outer_uid or not inner_uid:
                continue

            outer_ti = bd.terminal_info.get(outer_uid)
            inner_ti = bd.terminal_info.get(inner_uid)

            # Determine direction from terminal_info, not tunnel type.
            # selTun tunnels are bidirectional — direction depends on instance.
            # If outer is_output=False, data flows IN (outer receives from outside).
            # If outer is_output=True, data flows OUT (outer sends to outside).
            if outer_ti:
                is_input_tunnel = not outer_ti.is_output
            else:
                is_input_tunnel = ttype in self._INPUT_TUNNEL_TYPES

            q_outer_uid = self._qid(vi_name, outer_uid)
            q_inner_uid = self._qid(vi_name, inner_uid)

            # Direction-normalize the loop-tunnel mode now that direction is
            # known (the parser sees only the file's flags, not which way data
            # flows). An INPUT tunnel is auto-index (INDEXING) or no-indexing
            # (PASSTHROUGH) only -- it has no last-value/concatenate/conditional
            # semantics (those are output-only), so re-label its "indexing off"
            # LAST_VALUE as PASSTHROUGH and clear conditional.
            tun_mode = tunnel.mode
            tun_conditional = tunnel.conditional
            if is_input_tunnel and ttype == "lpTun":
                if tun_mode == TunnelMode.LAST_VALUE:
                    tun_mode = TunnelMode.PASSTHROUGH
                tun_conditional = False

            # Outer terminal
            outer_lv_type = None
            if outer_ti and outer_ti.parsed_type:
                outer_lv_type = self._enrich_type(outer_ti.parsed_type)

            outer_terminal = TunnelTerminal(
                id=q_outer_uid,
                index=outer_ti.index if outer_ti else 0,
                direction="input" if is_input_tunnel else "output",
                name=outer_ti.name if outer_ti else None,
                lv_type=outer_lv_type,
                tunnel_type=ttype,
                boundary="outer",
                paired_id=q_inner_uid,
                mode=tun_mode,
                conditional=tun_conditional,
                sr_initialized=tunnel.sr_initialized,
                sr_stack_depth=tunnel.sr_stack_depth,
            )
            if outer_uid not in seen_uids:
                structure_terminals.append(outer_terminal)
                seen_uids.add(outer_uid)

            # Inner terminal
            inner_lv_type = None
            if inner_ti and inner_ti.parsed_type:
                inner_lv_type = self._enrich_type(inner_ti.parsed_type)

            # Correlate this inner tunnel to its owning case frame by
            # position: the Nth inner tunnel for a given outer_uid belongs
            # to case_frames[N] (see docstring above).
            inner_frame: str | int | None = None
            if case_frames is not None:
                position = outer_tunnel_position.get(outer_uid, 0)
                outer_tunnel_position[outer_uid] = position + 1
                if position < len(case_frames):
                    inner_frame = case_frames[position].selector_value

            # Inner direction is opposite of outer for data flow
            inner_terminal = TunnelTerminal(
                id=q_inner_uid,
                index=inner_ti.index if inner_ti else 0,
                direction="output" if is_input_tunnel else "input",
                name=inner_ti.name if inner_ti else None,
                lv_type=inner_lv_type,
                tunnel_type=ttype,
                boundary="inner",
                paired_id=q_outer_uid,
                frame=inner_frame,
                mode=tun_mode,
                conditional=tun_conditional,
                sr_initialized=tunnel.sr_initialized,
                sr_stack_depth=tunnel.sr_stack_depth,
            )
            if inner_uid not in seen_uids:
                structure_terminals.append(inner_terminal)
                seen_uids.add(inner_uid)

            # Register both in term_lookup pointing to structure node
            outer_end = WireEnd(
                terminal_id=q_outer_uid,
                node_id=structure_uid,
                index=outer_ti.index if outer_ti else None,
                name=outer_ti.name if outer_ti else None,
            )
            inner_end = WireEnd(
                terminal_id=q_inner_uid,
                node_id=structure_uid,
                index=inner_ti.index if inner_ti else None,
                name=inner_ti.name if inner_ti else None,
            )
            term_lookup[outer_uid] = outer_end
            term_lookup[inner_uid] = inner_end

            # Create internal edge (self-loop) outer<->inner
            if is_input_tunnel:
                # Data flows in: outer -> inner
                g.add_edge(
                    structure_uid, structure_uid,
                    source=outer_end, dest=inner_end,
                    tunnel_type=ttype, vi=vi_name,
                )
            else:
                # Data flows out: inner -> outer
                g.add_edge(
                    structure_uid, structure_uid,
                    source=inner_end, dest=outer_end,
                    tunnel_type=ttype, vi=vi_name,
                )

        # --- 2. Register sRN-owned terminals on the structure ---
        # An sRN is an EXECUTION CLUMP (LabVIEW's scheduler grouping), not a
        # tunnel holder — it aggregates unrelated terminals (real tunnel sides,
        # FP control/indicator terminals, constants, event stubs) that merely
        # run together. Terminals not already registered are added so their
        # signal wiring resolves. We deliberately do NOT re-pair clump terminals
        # by index: the exact inner<->outer tunnel pass-throughs were already
        # built from parser_tunnels' explicit uid pairs in section 1, and an
        # sRN's index collisions are meaningless (they fabricated type-mismatched
        # edges — String->Boolean, Refnum->Boolean — that don't exist in the VI).

        # Extract raw UID from qualified UID for srn_to_structure lookup
        raw_structure_uid = (
            structure_uid.split("::")[-1]
            if "::" in structure_uid
            else structure_uid
        )

        all_srn_parents: set[str] = set()
        for uid, ti in bd.terminal_info.items():
            if ti.parent_uid and ti.parent_uid not in known_node_uids:
                # Scope to sRNs belonging to THIS structure
                srn_in_scope = (
                    not bd.srn_to_structure
                    or bd.srn_to_structure.get(ti.parent_uid) == raw_structure_uid
                )
                if srn_in_scope:
                    all_srn_parents.add(ti.parent_uid)

        for srn_uid in all_srn_parents:
            # Collect all terminals owned by this sRN
            srn_terms = [
                (uid, ti) for uid, ti in bd.terminal_info.items()
                if ti.parent_uid == srn_uid
            ]

            # Add sRN terminals to structure — but skip ones already
            # registered (constants, FP terminals have their own nodes)
            for uid, ti in srn_terms:
                if uid in seen_uids or uid in term_lookup:
                    continue
                seen_uids.add(uid)

                q_uid = self._qid(vi_name, uid)
                lv_type = None
                if ti.parsed_type:
                    lv_type = self._enrich_type(ti.parsed_type)

                structure_terminals.append(Terminal(
                    id=q_uid,
                    index=ti.index,
                    direction="output" if ti.is_output else "input",
                    name=ti.name,
                    lv_type=lv_type,
                ))

                term_lookup[uid] = WireEnd(
                    terminal_id=q_uid,
                    node_id=structure_uid,
                    index=ti.index,
                    name=ti.name,
                )

        return structure_terminals
