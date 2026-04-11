"""Construction mixin for InMemoryVIGraph.

Methods: _add_vi_to_graph, _build_structure_terminals, _format_lv_type_for_display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import networkx as nx

from ..graph_types import (
    AnyGraphNode,
    CaseFrame,
    CaseStructureNode,
    ConstantNode,
    FPTerminal,
    LoopNode,
    LVType,
    PropertyDef,
    SequenceFrame,
    SequenceNode,
    StructureNode,
    Terminal,
    TunnelTerminal,
    VINode,
    WireEnd,
    control_type_to_lvtype,
)
from ..graph_types import (
    PrimitiveNode as GraphPrimitiveNode,
)
from ..parser import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedFrontPanel,
)
from ..parser.models import ParsedConstant, ParsedType
from ..parser.node_types import (
    CpdArithNode,
    InvokeNode,
    PropertyNode,
    SelectNode,
    SubVINode,
)
from ..parser.node_types import (
    PrimitiveNode as ParserPrimitiveNode,
)
from ..primitive_resolver import get_resolver as get_prim_resolver
from ..primitive_resolver import resolve_primitive
from ..type_defaults import get_default_for_type
from ..vilib_resolver import get_resolver as get_vilib_resolver


def decode_constant(
    const: ParsedConstant,
    lv_type: LVType | None = None,
) -> tuple[str, str]:
    """Decode a constant value to (python_type, human_readable_value).

    Args:
        const: The constant to decode
        lv_type: LVType from the graph (authoritative type info)
    """
    from ..parser.vi import _decode_element

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

        term_lookup is a LOCAL dict used during construction only.

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

        # Build connector pane lookup: fp_dco_uid -> slot index
        conpane_slots: dict[str, int] = {}
        if conpane:
            for slot in conpane.slots:
                if slot.fp_dco_uid:
                    conpane_slots[slot.fp_dco_uid] = slot.index

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
                control_type=ctrl.control_type if ctrl else None,
                default_value=ctrl.default_value if ctrl else None,
                enum_values=ctrl.enum_values if ctrl else [],
            )
            vi_terminals.append(terminal)

            # Register in term_lookup for wire resolution
            term_lookup[fp_term.uid] = WireEnd(
                terminal_id=q_term_uid,
                node_id=vi_name,
                index=slot_index,
                name=ctrl.name if ctrl else fp_term.name,
            )

        # Create the VINode
        vi_node = VINode(
            id=vi_name,
            vi=vi_name,
            name=vi_name,
            terminals=vi_terminals,
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
                terminals=[const_terminal],
            )
            g.add_node(q_const_uid, node=const_node)
            vi_node_uids.add(q_const_uid)

            term_lookup[const.uid] = WireEnd(
                terminal_id=q_const_uid,
                node_id=q_const_uid,
                index=0,
                name=const.label,
            )

        # === 3. Add operations (SubVIs, primitives, structures) ===

        # Collect structure info indexed by UID for later use
        loop_by_uid = {loop.uid: loop for loop in bd.loops}
        case_by_uid = {cs.uid: cs for cs in bd.case_structures}
        flatseq_by_uid = {fs.uid: fs for fs in bd.flat_sequences}

        for node in bd.nodes:
            q_node_uid = self._qid(vi_name, node.uid)

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
                self._resolve_terminal_indices(raw_terms, known_terminals)

            node_terminals: list[Terminal] = []
            for term_uid, t_info, lv_type in raw_terms:
                q_term_uid = self._qid(vi_name, term_uid)
                terminal = Terminal(
                    id=q_term_uid,
                    index=t_info.index,
                    direction="output" if t_info.is_output else "input",
                    name=t_info.name,
                    lv_type=lv_type,
                )
                node_terminals.append(terminal)

                term_lookup[term_uid] = WireEnd(
                    terminal_id=q_term_uid,
                    node_id=q_node_uid,
                    index=t_info.index,
                    name=t_info.name,
                )

            node_terminals.sort(key=lambda t: t.index)

            # Resolve node name
            node_name = node.name
            if isinstance(node, ParserPrimitiveNode) and node.prim_res_id:
                resolved = resolve_primitive(prim_id=node.prim_res_id)
                if resolved:
                    node_name = resolved.name

            if not node_name and node.node_type:
                resolved_nt = get_prim_resolver().resolve_by_node_type(node.node_type)
                if resolved_nt:
                    node_name = resolved_nt.name

            # Get description for SubVIs from vilib
            description = None
            if node.node_type in ("iUse", "polyIUse", "dynIUse") and node_name:
                vilib_r = get_vilib_resolver()
                vi_entry = vilib_r.resolve_by_name(node_name)
                if vi_entry and vi_entry.description:
                    description = vi_entry.description

            # Determine what kind of graph node to create
            if node.node_type in ("iUse", "polyIUse", "dynIUse"):
                # SubVI call — stored as VINode
                poly_variant = None
                if isinstance(node, SubVINode) and node.poly_variant_name:
                    poly_variant = node.poly_variant_name
                graph_node: AnyGraphNode = VINode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=node_terminals,
                    description=description,
                    poly_variant_name=poly_variant,
                    qualified_path=iuse_to_qpath.get(node.uid),
                )
            elif node.node_type in ("whileLoop", "forLoop"):
                # Loop structure
                loop_struct = loop_by_uid.get(node.uid)
                stop_cond: str | None = None

                parser_tunnels: list = []
                if loop_struct:
                    parser_tunnels = loop_struct.tunnels
                    if loop_struct.stop_condition_terminal_uid:
                        stop_cond = self._qid(
                            vi_name, loop_struct.stop_condition_terminal_uid
                        )

                # Build terminals from tunnels + sRN terminals
                structure_terminals = self._build_structure_terminals(
                    bd, parser_tunnels, q_node_uid, term_lookup, vi_name,
                )

                graph_node = LoopNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=structure_terminals,
                    loop_type=node.node_type,
                    stop_condition_terminal=stop_cond,
                )
            elif node.node_type in ("caseStruct", "select"):
                # Case structure
                case_struct = case_by_uid.get(node.uid)
                case_frames: list[CaseFrame] = []
                selector_term: str | None = None

                parser_tunnels = []
                if case_struct:
                    parser_tunnels = case_struct.tunnels
                    if case_struct.selector_terminal_uid:
                        selector_term = self._qid(
                            vi_name, case_struct.selector_terminal_uid
                        )
                    case_frames = list(case_struct.frames)

                # Build terminals from tunnels + sRN terminals
                structure_terminals = self._build_structure_terminals(
                    bd, parser_tunnels, q_node_uid, term_lookup, vi_name,
                )

                # Mark the selector terminal. The caseSel tunnel already
                # created a TunnelTerminal for this UID — find it and
                # tag it as the selector. Only create a new Terminal if
                # the tunnel wasn't found (shouldn't happen normally).
                sel_uid = (
                    case_struct.selector_terminal_uid if case_struct else None
                )
                if selector_term and sel_uid:
                    existing = next(
                        (t for t in structure_terminals
                         if t.id == selector_term),
                        None,
                    )
                    if existing:
                        existing.name = "selector"
                        sel_index = existing.index
                    else:
                        sel_ti = bd.terminal_info.get(sel_uid)
                        sel_index = sel_ti.index if sel_ti else 0
                        sel_terminal = Terminal(
                            id=selector_term,
                            index=sel_index,
                            direction="input",
                            name="selector",
                        )
                        structure_terminals.append(sel_terminal)
                    # Register in term_lookup so wire edges resolve
                    if sel_uid not in term_lookup:
                        term_lookup[sel_uid] = WireEnd(
                            terminal_id=selector_term,
                            node_id=q_node_uid,
                            index=sel_index,
                            name="selector",
                        )

                graph_node = CaseStructureNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=structure_terminals,
                    frames=case_frames,
                    selector_terminal=selector_term,
                )
            elif node.node_type in ("flatSequence", "seq"):
                # Flat sequence
                flat_seq = flatseq_by_uid.get(node.uid)
                seq_frames: list[SequenceFrame] = []

                parser_tunnels = []
                if flat_seq:
                    parser_tunnels = flat_seq.tunnels
                    seq_frames = list(flat_seq.frames)

                # Build terminals from tunnels + sRN terminals
                structure_terminals = self._build_structure_terminals(
                    bd, parser_tunnels, q_node_uid, term_lookup, vi_name,
                )

                graph_node = SequenceNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=structure_terminals,
                    frames=seq_frames,
                )
            elif isinstance(node, ParserPrimitiveNode):
                # Primitive node
                prim_kwargs: dict[str, Any] = {
                    "prim_id": node.prim_res_id,
                    "prim_index": node.prim_index,
                }
                if isinstance(node, CpdArithNode):
                    prim_kwargs["operation"] = node.operation
                if isinstance(node, PropertyNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["properties"] = [
                        PropertyDef(name=p.get("name", ""))
                        if isinstance(p, dict) else p
                        for p in node.properties
                    ]
                if isinstance(node, InvokeNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["method_name"] = node.method_name
                    prim_kwargs["method_code"] = node.method_code

                graph_node = GraphPrimitiveNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=node_terminals,
                    description=description,
                    **prim_kwargs,
                )
            else:
                # Generic primitive / operation
                prim_kwargs = {}
                if isinstance(node, CpdArithNode):
                    prim_kwargs["operation"] = node.operation
                if isinstance(node, PropertyNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["properties"] = [
                        PropertyDef(name=p.get("name", ""))
                        if isinstance(p, dict) else p
                        for p in node.properties
                    ]
                if isinstance(node, InvokeNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["method_name"] = node.method_name
                    prim_kwargs["method_code"] = node.method_code

                graph_node = GraphPrimitiveNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=node_terminals,
                    description=description,
                    **prim_kwargs,
                )

            # Mark nMux terminal roles (agg vs list) and field indices
            if isinstance(node, SelectNode) and node.dco_agg_uid:
                agg_dco = node.dco_agg_uid
                list_dcos = set(node.dco_list_uids)
                for term in graph_node.terminals:
                    raw_tid = (
                        term.id.split("::")[-1]
                        if "::" in term.id else term.id
                    )
                    dco_uid = node.term_to_dco.get(raw_tid)
                    if dco_uid == agg_dco:
                        term.nmux_role = "agg"
                    elif dco_uid in list_dcos:
                        term.nmux_role = "list"
                        # Set field index from <i> tag in XML
                        if dco_uid in node.dco_field_index:
                            term.nmux_field_index = node.dco_field_index[dco_uid]

            g.add_node(q_node_uid, node=graph_node)
            vi_node_uids.add(q_node_uid)

        # === 4. Set parent/frame on inner operation nodes ===
        # After all nodes are created, walk parser structures and stamp
        # containment info on the graph nodes they own.

        for loop in bd.loops:
            q_loop_uid = self._qid(vi_name, loop.uid)
            for uid in loop.inner_node_uids:
                q_uid = self._qid(vi_name, uid)
                if q_uid in g and "node" in g.nodes[q_uid]:
                    inner_node = g.nodes[q_uid]["node"]
                    inner_node.parent = q_loop_uid
                    inner_node.frame = None

        for cs in bd.case_structures:
            q_cs_uid = self._qid(vi_name, cs.uid)
            for frame in cs.frames:
                for uid in frame.inner_node_uids:
                    q_uid = self._qid(vi_name, uid)
                    if q_uid in g and "node" in g.nodes[q_uid]:
                        inner_node = g.nodes[q_uid]["node"]
                        inner_node.parent = q_cs_uid
                        inner_node.frame = frame.selector_value

        for fs in bd.flat_sequences:
            q_fs_uid = self._qid(vi_name, fs.uid)
            for idx, frame in enumerate(fs.frames):
                for uid in frame.inner_node_uids:
                    q_uid = self._qid(vi_name, uid)
                    if q_uid in g and "node" in g.nodes[q_uid]:
                        inner_node = g.nodes[q_uid]["node"]
                        inner_node.parent = q_fs_uid
                        inner_node.frame = str(idx)

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

                term_lookup[term_uid] = WireEnd(
                    terminal_id=q_term_uid,
                    node_id=effective_parent or q_term_uid,
                    index=t_info.index,
                    name=t_info.name,
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
        # terminals are in the graph. Create edges so types propagate.
        if iuse_to_qname:
            self._connect_subvi_calls(vi_name, vi_node_uids, iuse_to_qname)

        # === 8. Propagate types through wires and re-match indices ===
        # Now follows edges ACROSS VI boundaries too.
        self._propagate_types_and_rematch(g, vi_node_uids)

        # Store per-VI node index
        self._vi_nodes[vi_name] = vi_node_uids

        # Populate terminal ownership from term_lookup
        for _raw_tid, wire_end in term_lookup.items():
            self._term_to_node[wire_end.terminal_id] = wire_end.node_id

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
            if gnode.node_type not in ("iUse", "polyIUse", "dynIUse"):
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
                                 index=call_term.index, name=call_term.name)
                dst_we = WireEnd(terminal_id=callee_t.id, node_id=callee_name,
                                 index=call_term.index, name=callee_t.name)
                if call_term.direction == "input":
                    g.add_edge(nid, callee_name, source=src_we, dest=dst_we)
                else:
                    g.add_edge(callee_name, nid, source=dst_we, dest=src_we)

    # Tunnel types where the outer terminal is an input (data flows IN)
    @staticmethod
    def _resolve_terminal_indices(
        raw_terms: list[tuple[str, Any, LVType | None]],
        known_terminals: list,
    ) -> None:
        """Resolve -1 indices. Direct match by type+direction or bust.

        For each unresolved parser terminal: find the ONE resolver
        terminal with matching direction AND type category. If exactly
        one match, assign. Otherwise leave -1.
        """
        assigned_indices: set[int] = set()
        for _, t_info, _ in raw_terms:
            if t_info.index >= 0:
                assigned_indices.add(t_info.index)

        for _, t_info, lv_type in raw_terms:
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
        # Propagate: if one side of a wire has lv_type and the other doesn't
        changed = True
        while changed:
            changed = False
            for _u, _v, _k, d in g.edges(data=True, keys=True):
                src = d.get("source")
                dst = d.get("dest")
                if not src or not dst:
                    continue
                src_node = g.nodes.get(src.node_id, {}).get("node")
                dst_node = g.nodes.get(dst.node_id, {}).get("node")
                if not src_node or not dst_node:
                    continue
                src_term = next(
                    (t for t in src_node.terminals if t.id == src.terminal_id), None
                )
                dst_term = next(
                    (t for t in dst_node.terminals if t.id == dst.terminal_id), None
                )
                if not src_term or not dst_term:
                    continue
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
                fake_ti = type('', (), {
                    'index': t.index,
                    'is_output': t.direction == 'output',
                })()
                fake_terms.append(('', fake_ti, t.lv_type))

            self._resolve_terminal_indices(fake_terms, prim_terminals)

            # Apply resolved indices back
            for (_, fake_ti, _), t in zip(fake_terms, gnode.terminals):
                if t.index == -1 and fake_ti.index >= 0:
                    t.index = fake_ti.index

    _INPUT_TUNNEL_TYPES = frozenset({
        "lSR", "lpTun", "caseSel", "seqTun", "flatSeqTun",
    })

    def _build_structure_terminals(
        self,
        bd: ParsedBlockDiagram,
        parser_tunnels: list,
        structure_uid: str,
        term_lookup: dict[str, WireEnd],
        vi_name: str = "",
    ) -> list[Terminal]:
        """Build Terminal list for a StructureNode from its tunnels and sRN nodes.

        Each parser tunnel creates TWO Terminal objects:
        - Outer terminal (boundary="outer")
        - Inner terminal (boundary="inner")

        Also maps sRN-owned terminals to the structure and creates
        internal edges (self-loops) on the graph for:
        - Tunnel outer<->inner connections
        - sRN input->output pairings

        Returns the complete terminal list for the StructureNode.
        """
        g = self._graph
        structure_terminals: list[Terminal] = []
        seen_uids: set[str] = set()

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
            )
            if outer_uid not in seen_uids:
                structure_terminals.append(outer_terminal)
                seen_uids.add(outer_uid)

            # Inner terminal
            inner_lv_type = None
            if inner_ti and inner_ti.parsed_type:
                inner_lv_type = self._enrich_type(inner_ti.parsed_type)

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

        # --- 2. Find ALL sRN parent UIDs ---
        # Tunnel-referenced sRNs get input->output pairing edges.
        # Non-tunnel sRNs just get mapped — wires handle routing.
        tunnel_srn_parents: set[str] = set()
        for tunnel in parser_tunnels:
            for uid in (tunnel.outer_terminal_uid, tunnel.inner_terminal_uid):
                if not uid:
                    continue
                ti = bd.terminal_info.get(uid)
                if ti and ti.parent_uid and ti.parent_uid not in known_node_uids:
                    tunnel_srn_parents.add(ti.parent_uid)

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
                if not bd.srn_to_structure or bd.srn_to_structure.get(
                    ti.parent_uid,
                ) == raw_structure_uid:
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

            # Pair by matching index (same position on structure border)
            # — same as VI connector pane pairing
            input_by_idx = {
                ti.index: (uid, ti)
                for uid, ti in srn_terms
                if not ti.is_output
            }
            output_by_idx = {
                ti.index: (uid, ti)
                for uid, ti in srn_terms
                if ti.is_output
            }
            paired = [
                (input_by_idx[idx], output_by_idx[idx])
                for idx in input_by_idx
                if idx in output_by_idx
            ]
            for (in_uid, _in_ti), (out_uid, _out_ti) in paired:
                q_in_uid = self._qid(vi_name, in_uid)
                q_out_uid = self._qid(vi_name, out_uid)
                in_end = term_lookup.get(in_uid, WireEnd(
                    terminal_id=q_in_uid, node_id=structure_uid,
                ))
                out_end = term_lookup.get(out_uid, WireEnd(
                    terminal_id=q_out_uid, node_id=structure_uid,
                ))
                g.add_edge(
                    structure_uid, structure_uid,
                    source=in_end, dest=out_end,
                    tunnel_type="sRN", vi=vi_name,
                )

        return structure_terminals
