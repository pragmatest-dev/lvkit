"""Node type subclasses and parsing handlers.

Each LabVIEW node type (prim, iUse, cpdArith, aBuild, etc.) has:
1. A Node subclass with type-specific fields
2. A handler that knows how to parse its XML
3. Registration in NODE_HANDLERS for factory lookup
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .models import ParsedNode
from .nodes.base import extract_label, extract_terminal_types
from .utils import clean_labview_string

# =============================================================================
# Node Subclasses
# =============================================================================


@dataclass
class PrimitiveNode(ParsedNode):
    """A LabVIEW primitive node (class="prim")."""

    prim_index: int | None = None
    prim_res_id: int | None = None


@dataclass
class SubVINode(ParsedNode):
    """A SubVI call node (class="iUse" or "polyIUse")."""

    vi_path: str | None = None
    poly_variant_name: str | None = None  # Resolved variant for polyIUse


@dataclass
class CpdArithNode(ParsedNode):
    """Compound Arithmetic node (class="cpdArith").

    Combines multiple inputs with a single operation (OR, AND, ADD, etc.).
    """

    operation: str = "add"  # "add", "or", "and", "multiply", "xor", "unsupported"


@dataclass
class ArrayBuildNode(ParsedNode):
    """Build Array node (class="aBuild").

    Collects multiple inputs into an array.
    """

    pass  # No extra fields yet


@dataclass
class ArrayInitNode(ParsedNode):
    """Initialize Array node (class="aInit").

    Builds an N-dimensional array from an element value and one size input per
    dimension. Terminals (element + dimension sizes → array) carry on the base
    ParsedNode; no extra fields.
    """

    pass


@dataclass
class FormulaNode(ParsedNode):
    """A Formula Node (class="fBox") with an embedded C-like script.

    The script is a restricted C-like language (not raw C): it uses the
    ``**`` power operator and LabVIEW type keywords (int8/int16/float32...).
    Terminal variables (names, types, directions) are carried on the base
    ParsedNode terminal fields; this subclass adds the script text.
    """

    script: str | None = None


@dataclass
class LoopNode(ParsedNode):
    """Loop structure node (class="whileLoop" or "forLoop")."""

    loop_type: str = ""  # "whileLoop" or "forLoop"


@dataclass
class SelectNode(ParsedNode):
    """Select/nMux node (class="select" or "nMux").

    nMux is a bundle/unbundle node at structure boundaries.
    dco_agg_uid identifies the aggregate (cluster) terminal's DCO.
    dco_list_uids identify the field value terminals' DCOs.
    poser_uid links decompose↔recompose pairs inside an IPES structure.
    """

    dco_agg_uid: str | None = None
    dco_list_uids: list[str] = field(default_factory=list)
    # Maps terminal UID → DCO UID for role matching
    term_to_dco: dict[str, str] = field(default_factory=dict)
    # Maps DCO UID → field index from <i> tag (nMux bundle/unbundle)
    dco_field_index: dict[str, int] = field(default_factory=dict)
    # Pairing UID for decompose/recompose nodes (from <poser uid="..."/>)
    poser_uid: str | None = None


@dataclass
class CtlRefConstNode(ParsedNode):
    """Control reference constant (class="ctlRefConst").

    ddo_uid set: references a specific FP control — modelled as a drawn
    ref node with a synthetic FP-value dataflow edge.
    ddo_uid None: built-in reference ("This Application", "This VI") — still
    modelled/drawn as a ref node, just without the FP-control edge.
    """

    ddo_uid: str | None = None


@dataclass
class GRefNode(ParsedNode):
    """Local Variable reference (class="gRef").

    Reads (or writes) the value of an FP control/indicator by connector-pane
    slot index rather than by direct wire. param_idx set: the output
    terminal is aliased to that connector-pane slot's FP terminal WireEnd
    in the graph (no new graph node — same pattern as ctlRefConst).
    param_idx None: unresolvable (e.g. a global VI's local var) — deferred.
    """

    param_idx: int | None = None


@dataclass
class StatVIRefNode(ParsedNode):
    """Static VI Reference constant (class="statVIRef").

    Compile-time reference to a specific VI. The VI name comes from
    the label text. Downstream callByRefNode/property/invoke nodes
    resolve it as a callable or object reference.
    """

    pass  # name comes from label via _extract_common


@dataclass
class CallByRefNode(ParsedNode):
    """Call By Reference node (class="callByRefNode").

    Calls a VI determined at runtime via a VI reference wire.
    frame_terminal_uids: UIDs of the 4 hGrowCItem terminals
    (error in/out, VI ref in/out) from permDCOList.
    """

    frame_terminal_uids: list[str] = field(default_factory=list)


# =============================================================================
# Node Type Handlers
# =============================================================================


class NodeTypeHandler(ABC):
    """Base class for node type handlers.

    Each handler knows:
    - What XML class it handles
    - What display name to use
    - How to parse its specific attributes
    """

    xml_class: str  # e.g., "cpdArith", "prim", "aBuild"
    display_name: str  # e.g., "Compound Arithmetic"

    @abstractmethod
    def parse(self, elem: ET.Element) -> ParsedNode:
        """Parse XML element into typed ParsedNode."""
        pass

    def _extract_common(self, elem: ET.Element) -> dict[str, Any]:
        """Extract common fields from XML element."""
        name = extract_label(elem)
        input_types, output_types = extract_terminal_types(elem)
        return {
            "uid": elem.get("uid"),
            "node_type": self.xml_class,
            "name": name or self.display_name,
            "input_types": input_types,
            "output_types": output_types,
        }


class PrimitiveHandler(NodeTypeHandler):
    """Handler for primitive nodes (class="prim")."""

    xml_class = "prim"
    display_name = "Primitive"

    def parse(self, elem: ET.Element) -> PrimitiveNode:
        common = self._extract_common(elem)

        prim_idx_elem = elem.find("primIndex")
        prim_res_elem = elem.find("primResID")

        prim_index = None
        if prim_idx_elem is not None and prim_idx_elem.text:
            prim_index = int(prim_idx_elem.text)
        prim_res_id = None
        if prim_res_elem is not None and prim_res_elem.text:
            prim_res_id = int(prim_res_elem.text)

        return PrimitiveNode(
            **common,
            prim_index=prim_index,
            prim_res_id=prim_res_id,
        )


class SubVIHandler(NodeTypeHandler):
    """Handler for SubVI nodes (class="iUse")."""

    xml_class = "iUse"
    display_name = "SubVI"

    def parse(self, elem: ET.Element) -> SubVINode:
        common = self._extract_common(elem)
        return SubVINode(**common)


class PolySubVIHandler(NodeTypeHandler):
    """Handler for polymorphic SubVI nodes (class="polyIUse")."""

    xml_class = "polyIUse"
    display_name = "Polymorphic SubVI"

    def parse(self, elem: ET.Element) -> SubVINode:
        common = self._extract_common(elem)
        variant_name = self._extract_poly_variant(elem)
        return SubVINode(**common, poly_variant_name=variant_name)

    def _extract_poly_variant(self, elem: ET.Element) -> str | None:
        """Extract the selected polymorphic variant name.

        The instanceSelector (class=polySelector) stores the edit-time
        resolved variant. menuInstanceUsed is the actual selection index
        (hex-encoded). The buf element has the variant name list.
        """
        # Find the instanceSelector polySelector (direct child of polyIUse)
        for selector in elem.iter():
            if selector.get("class") != "polySelector":
                continue

            # menuInstanceUsed = actual resolved variant index (hex)
            menu_elem = selector.find("menuInstanceUsed")
            if menu_elem is None or not menu_elem.text:
                continue
            try:
                menu_index = int(menu_elem.text.strip(), 16)
            except ValueError:
                continue

            # Index 0 = "Automatic" — no specific variant selected
            if menu_index == 0:
                return None

            # Look up name from buf (variant name list in polySelector)
            for child in selector.iter():
                if child.tag == "buf" and child.text:
                    items = re.findall(r'"([^"]+)"', child.text)
                    if 0 <= menu_index < len(items):
                        return items[menu_index]

            # No buf — return index for resolver to map
            return f"poly_index:{menu_index}"

        return None


class DynamicDispatchHandler(NodeTypeHandler):
    """Handler for dynamic dispatch VI nodes (class="dynIUse").

    Dynamic dispatch VIs are class methods that use runtime dispatch
    based on the class of the input object. In Python, this is just
    regular method calls - Python's MRO handles dispatch automatically.
    """

    xml_class = "dynIUse"
    display_name = "Dynamic Dispatch VI"

    def parse(self, elem: ET.Element) -> SubVINode:
        common = self._extract_common(elem)
        return SubVINode(**common)


class CallParentHandler(NodeTypeHandler):
    """Handler for Call Parent Method nodes (class="callParentDynIUse").

    Calls the parent class's implementation of a dynamic dispatch method.
    Structurally identical to dynIUse; codegen emits super().method(args).
    """

    xml_class = "callParentDynIUse"
    display_name = "Call Parent Method"

    def parse(self, elem: ET.Element) -> SubVINode:
        common = self._extract_common(elem)
        return SubVINode(**common)


class CallByRefHandler(NodeTypeHandler):
    """Handler for Call By Reference nodes (class="callByRefNode").

    Calls a VI determined at runtime via a VI reference wire. The
    permDCOList element identifies the 4 frame terminals (hGrowCItem DCOs:
    error in/out, VI ref in/out); remaining terminals are callee terminals.
    """

    xml_class = "callByRefNode"
    display_name = "Call By Reference"

    def parse(self, elem: ET.Element) -> CallByRefNode:
        common = self._extract_common(elem)
        # Collect frame DCO UIDs from permDCOList
        frame_dco_uids: set[str] = set()
        perm = elem.find("permDCOList")
        if perm is not None:
            for child in perm.findall("SL__arrayElement"):
                uid = child.get("uid")
                if uid:
                    frame_dco_uids.add(uid)
        # Map those DCO UIDs to their containing terminal UIDs
        frame_terminal_uids: list[str] = []
        term_list = elem.find("termList")
        if term_list is not None:
            for te in term_list.findall("SL__arrayElement"):
                dco = te.find("dco")
                if dco is not None and dco.get("uid") in frame_dco_uids:
                    t_uid = te.get("uid")
                    if t_uid:
                        frame_terminal_uids.append(t_uid)
        return CallByRefNode(**common, frame_terminal_uids=frame_terminal_uids)


class CpdArithHandler(NodeTypeHandler):
    """Handler for Compound Arithmetic nodes (class="cpdArith")."""

    xml_class = "cpdArith"
    display_name = "Compound Arithmetic"

    # LabVIEW stores the Compound Arithmetic OPERATION in the node's objFlags,
    # NOT in any dcoFiller. Bits 16-18 hold the mode enum; bit 19 is a separate
    # always-set marker. Corpus-verified across every cpdArith in the sample set
    # (objFlags only ever 0x8/0xA/0xB/0xC 0000) and cross-checked against known
    # dataflow -- this disproves the earlier "dcoFiller low byte" theory, which
    # collapsed the 72 boolean-AND "...Changed" detectors into add (-> OR):
    #   0 = add       (Trim Whitespace len sum; Reshape index math; MD5 F/G/I)
    #   1 = multiply  (no corpus instance; occupies LabVIEW's enum slot)
    #   2 = and       (every "X Changed" / "X Array Changed" detector; Trigger)
    #   3 = or        (Create Dir if Non-Existant; file / refnum / wait guards)
    #   4 = xor       (MD5 H function: H(x, y, z) = x XOR y XOR z)
    # dcoFiller is per-terminal invert/type data and does NOT select the op.
    # An unrecognised code (5-7) maps to the "unsupported" sentinel (rendered as
    # "?", failed loudly at codegen) rather than being guessed.
    OPERATIONS = {
        0: "add",
        1: "multiply",
        2: "and",
        3: "or",
        4: "xor",
    }
    UNSUPPORTED = "unsupported"
    _OP_SHIFT = 16
    _OP_MASK = 0x7

    def parse(self, elem: ET.Element) -> CpdArithNode:
        common = self._extract_common(elem)
        operation = self._extract_operation(elem)

        return CpdArithNode(
            **common,
            operation=operation,
        )

    def _extract_operation(self, elem: ET.Element) -> str:
        """Operation from objFlags bits 16-18 (the Compound Arithmetic mode enum).

        The parser never fails on an unknown code or a missing objFlags -- it
        returns the ``UNSUPPORTED`` sentinel so rendering degrades gracefully;
        codegen is the layer that fails loudly.
        """
        raw = elem.findtext("objFlags")
        if not raw:
            return self.UNSUPPORTED
        code = (int(raw) >> self._OP_SHIFT) & self._OP_MASK
        return self.OPERATIONS.get(code, self.UNSUPPORTED)


class ArrayBuildHandler(NodeTypeHandler):
    """Handler for Build Array nodes (class="aBuild")."""

    xml_class = "aBuild"
    display_name = "Build Array"

    def parse(self, elem: ET.Element) -> ArrayBuildNode:
        common = self._extract_common(elem)
        return ArrayBuildNode(**common)


class ArrayInitHandler(NodeTypeHandler):
    """Handler for Initialize Array nodes (class="aInit")."""

    xml_class = "aInit"
    display_name = "Initialize Array"

    def parse(self, elem: ET.Element) -> ArrayInitNode:
        common = self._extract_common(elem)
        return ArrayInitNode(**common)


class WhileLoopHandler(NodeTypeHandler):
    """Handler for While Loop nodes (class="whileLoop")."""

    xml_class = "whileLoop"
    display_name = "While Loop"

    def parse(self, elem: ET.Element) -> LoopNode:
        # Don't use extract_label for loops - it would find labels from inner nodes
        input_types, output_types = extract_terminal_types(elem)
        return LoopNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,  # Always use "While Loop"
            input_types=input_types,
            output_types=output_types,
            loop_type="whileLoop",
        )


class ForLoopHandler(NodeTypeHandler):
    """Handler for For Loop nodes (class="forLoop")."""

    xml_class = "forLoop"
    display_name = "For Loop"

    def parse(self, elem: ET.Element) -> LoopNode:
        # Don't use extract_label for loops - it would find labels from inner nodes
        input_types, output_types = extract_terminal_types(elem)
        return LoopNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
            input_types=input_types,
            output_types=output_types,
            loop_type="forLoop",
        )


class SelectHandler(NodeTypeHandler):
    """Handler for Select nodes (class="select").

    In this LV version, class="select" IS the Case Structure -- so this
    node's subtree contains a whole nested frame (subVI calls, primitives,
    etc.). Don't use extract_label here: its arbitrary-depth XPaths would
    grab the first descendant's label (e.g. a subVI named "addSkipped.vi"
    sitting in frame 0) and mis-name the WHOLE case structure with it.
    Same reasoning as WhileLoopHandler/ForLoopHandler above.
    """

    xml_class = "select"
    display_name = "Select"

    def parse(self, elem: ET.Element) -> SelectNode:
        input_types, output_types = extract_terminal_types(elem)
        return SelectNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
            input_types=input_types,
            output_types=output_types,
        )


@dataclass
class PropertyNode(ParsedNode):
    """A property node (class="propNode")."""

    object_name: str = ""
    object_method_id: str = ""
    properties: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InvokeNode(ParsedNode):
    """An invoke node (class="invokeNode").

    ``row_terminal_uids`` is the node's ``dcoList`` re-expressed as TERMINAL
    uids (one per dco, in heap order) -- 2 uids per row: row 0 is the
    METHOD (index 0 = the void method-select slot, never a data terminal;
    index 1 = the return value, present when the method returns something);
    rows 1..N are the method's PARAMETERS, each a left(input)/right(output)
    pass-through pair. A row-side terminal that resolves to a ``Void`` type
    is a reserved-but-unused heap slot -- LabVIEW always allocates both
    sides of a row, but only wireable sides get a real type. See the
    render layer (``render/nodes.py:_invoke_node_glyph``), which is where
    that Void check happens (type resolution isn't available at parse time).
    """

    object_name: str = ""
    object_method_id: str = ""
    method_name: str = ""
    method_code: int = 0
    row_terminal_uids: list[str] = field(default_factory=list)


class PropertyNodeHandler(NodeTypeHandler):
    """Handler for Property Node (class="propNode")."""

    xml_class = "propNode"
    display_name = "Property Node"

    def parse(self, elem: ET.Element) -> PropertyNode:
        common = self._extract_common(elem)
        object_name = clean_labview_string(elem.findtext("nodeName"))
        omid = elem.findtext("oMId") or ""

        properties: list[dict[str, Any]] = []
        for prop_info in elem.iter():
            if prop_info.get("class") != "propItemInfo":
                continue
            name = clean_labview_string(prop_info.findtext("PropItemName"))
            code_text = prop_info.findtext("PropItemCode") or "0"
            try:
                code = int(code_text)
            except ValueError:
                code = 0
            properties.append({"name": name, "code": code})

        return PropertyNode(
            **common,
            object_name=object_name,
            object_method_id=omid,
            properties=properties,
        )


class InvokeNodeHandler(NodeTypeHandler):
    """Handler for Invoke Node (class="invokeNode")."""

    xml_class = "invokeNode"
    display_name = "Invoke Node"

    def parse(self, elem: ET.Element) -> InvokeNode:
        common = self._extract_common(elem)
        meth_code_text = elem.findtext("methCode") or "0"
        try:
            meth_code = int(meth_code_text)
        except ValueError:
            meth_code = 0
        return InvokeNode(
            **common,
            object_name=clean_labview_string(elem.findtext("nodeName")),
            object_method_id=elem.findtext("oMId") or "",
            method_name=clean_labview_string(elem.findtext("methName")),
            method_code=meth_code,
            row_terminal_uids=self._extract_row_terminal_uids(elem),
        )

    def _extract_row_terminal_uids(self, elem: ET.Element) -> list[str]:
        """Map ``dcoList`` (dco uids, 2 per row: method then params) to the
        containing TERMINAL uids, preserving dcoList order. Mirrors
        ``CallByRefHandler``'s dco-uid -> terminal-uid mapping pattern."""
        dco_list_elem = elem.find("dcoList")
        dco_list_uids: list[str] = []
        if dco_list_elem is not None:
            for child in dco_list_elem.findall("SL__arrayElement"):
                uid = child.get("uid")
                if uid:
                    dco_list_uids.append(uid)
        if not dco_list_uids:
            return []

        dco_to_term: dict[str, str] = {}
        term_list = elem.find("termList")
        if term_list is not None:
            for term_elem in term_list.findall("SL__arrayElement"):
                t_uid = term_elem.get("uid")
                dco_elem = term_elem.find("dco")
                if t_uid and dco_elem is not None:
                    d_uid = dco_elem.get("uid")
                    if d_uid:
                        dco_to_term[d_uid] = t_uid

        return [
            dco_to_term[d_uid] for d_uid in dco_list_uids if d_uid in dco_to_term
        ]


class FlatSequenceHandler(NodeTypeHandler):
    """Handler for Flat Sequence structures (class="flatSequence")."""

    xml_class = "flatSequence"
    display_name = "Flat Sequence"

    def parse(self, elem: ET.Element) -> ParsedNode:
        input_types, output_types = extract_terminal_types(elem)
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
            input_types=input_types,
            output_types=output_types,
        )


class StackedSequenceHandler(NodeTypeHandler):
    """Handler for Stacked Sequence structures (class="seq" or "sequence").

    LabVIEW serializes stacked sequences as class="seq" in most versions and
    class="sequence" in older versions. Both are structurally identical.
    """

    xml_class = "seq"
    display_name = "Stacked Sequence"

    def parse(self, elem: ET.Element) -> ParsedNode:
        input_types, output_types = extract_terminal_types(elem)
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
            input_types=input_types,
            output_types=output_types,
        )


class _SequenceAliasHandler(StackedSequenceHandler):
    """Handles class="sequence" — older LV versions use this instead of "seq"."""

    xml_class = "sequence"


class DisableStructureHandler(NodeTypeHandler):
    """Handler for Diagram/Conditional Disable structures.

    Serialized as class="commentNode" -- the same class a plain free-text
    comment might use, but every commentNode this parser reaches here IS a
    real Disable structure: _extract_nodes only calls parse_node() on
    commentNode elements that already passed
    parser.nodes.disable.is_disable_structure (a plain comment never has
    subdiagrams, so it never reaches this handler). Frame content itself
    lives in ParsedDisableStructure (parser.nodes.disable), mirroring how
    case-frame content lives in ParsedCaseStructure separately from the bare
    SelectNode.
    """

    xml_class = "commentNode"
    display_name = "Disable Structure"

    def parse(self, elem: ET.Element) -> ParsedNode:
        input_types, output_types = extract_terminal_types(elem)
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=self.display_name,
            input_types=input_types,
            output_types=output_types,
        )


class PrintfHandler(NodeTypeHandler):
    """Handler for Format String nodes (class="printf").

    LabVIEW's printf node takes a format string and arguments,
    producing a formatted string output. Treated as a primitive.
    """

    xml_class = "printf"
    display_name = "Format String"

    def parse(self, elem: ET.Element) -> PrimitiveNode:
        common = self._extract_common(elem)
        return PrimitiveNode(**common)


class ScanfHandler(NodeTypeHandler):
    """Handler for Scan From String nodes (class="scanf").

    LabVIEW's scanf node takes a format string and an input string, producing
    scanned values as outputs. Like ``printf`` it has a variable number of
    terminals; ``_extract_common`` walks the termList generically. Treated as a
    primitive so its output wires resolve (otherwise every wire from a scanf
    output is dropped in graph construction).
    """

    xml_class = "scanf"
    display_name = "Scan From String"

    def parse(self, elem: ET.Element) -> PrimitiveNode:
        common = self._extract_common(elem)
        return PrimitiveNode(**common)


class NMuxHandler(NodeTypeHandler):
    """Handler for Node Multiplexer (class="nMux").

    nMux is a bundle/unbundle node at structure boundaries.
    dcoAgg is the aggregate (cluster) terminal, dcoList are field terminals.
    """

    xml_class = "nMux"
    display_name = "Bundle/Unbundle By Name"

    def parse(self, elem: ET.Element) -> SelectNode:
        common = self._extract_common(elem)

        # Extract dcoAgg (aggregate/cluster terminal DCO uid)
        dco_agg_elem = elem.find("dcoAgg")
        dco_agg_uid = dco_agg_elem.get("uid") if dco_agg_elem is not None else None

        # Extract dcoList (field terminal DCO uids)
        dco_list_elem = elem.find("dcoList")
        dco_list_uids: list[str] = []
        if dco_list_elem is not None:
            for child in dco_list_elem.findall("SL__arrayElement"):
                uid = child.get("uid")
                if uid:
                    dco_list_uids.append(uid)

        # Map terminal UID → DCO UID, and extract field index from <i> tag
        term_to_dco: dict[str, str] = {}
        dco_field_index: dict[str, int] = {}
        list_dco_set = set(dco_list_uids)
        term_list = elem.find("termList")
        if term_list is not None:
            for term_elem in term_list.findall("SL__arrayElement"):
                t_uid = term_elem.get("uid")
                dco_elem = term_elem.find("dco")
                if t_uid and dco_elem is not None:
                    d_uid = dco_elem.get("uid")
                    if d_uid:
                        term_to_dco[t_uid] = d_uid
                        # Extract <i> field index from LIST DCOs
                        if d_uid in list_dco_set:
                            i_elem = dco_elem.find("i")
                            idx = (
                                int(i_elem.text)
                                if i_elem is not None
                                and i_elem.text
                                else dco_list_uids.index(d_uid)
                            )
                            dco_field_index[d_uid] = idx

        return SelectNode(
            **common,
            dco_agg_uid=dco_agg_uid,
            dco_list_uids=dco_list_uids,
            term_to_dco=term_to_dco,
            dco_field_index=dco_field_index,
        )


class _MuxHandler(NMuxHandler):
    """Handles class="mux" — bundle at structure boundaries.

    Structurally identical to nMux but uses positional field indices
    (no <i> tags on mxDCO elements).
    """

    xml_class = "mux"
    display_name = "Bundle"


class _DemuxHandler(NMuxHandler):
    """Handles class="demux" — unbundle at structure boundaries.

    Structurally identical to nMux but uses positional field indices
    (no <i> tags on dmxDCO elements).
    """

    xml_class = "demux"
    display_name = "Unbundle"


class _EventDataNodeHandler(NMuxHandler):
    """Handles class="eventDataNode" — an Event Structure's data/filter node.

    A frame's Event Data Node (event-data fields, inner-left edge) AND its
    Event Filter Node (filterable fields, inner-right edge) share this SAME
    heap class — pylabview/LabVIEW distinguish them only by which per-frame
    list references the uid (``dataNodeList`` vs ``filterNodeList`` on the
    owning ``eventStruct``, see parser/nodes/event.py), not by a separate XML
    class. Structurally IDENTICAL to ``nMux`` (``dcoAgg`` aggregate + named
    ``dcoList``/``<i>`` fields via ``nmxDCO`` terminal DCOs) — the parser
    reuses NMuxHandler's parsing wholesale so field NAMES resolve through the
    exact same VCTP cluster-field pipeline as a real Bundle/Unbundle By Name.
    The render layer draws it with its OWN bespoke named-rows glyph though
    (``render.glyph.EventDataGlyph``, resolved in ``render/nodes.py``'s
    ``_CLUSTER_MUX_TYPES`` handling) — a white box with type-colored field
    names and a side accent band — never the tan Bundle/Unbundle-By-Name look
    (this isn't a real cluster assemble/disassemble).
    """

    xml_class = "eventDataNode"
    display_name = "Event Data Node"


class CtlRefConstHandler(NodeTypeHandler):
    """Handler for Control Reference Constant (class="ctlRefConst")."""

    xml_class = "ctlRefConst"
    display_name = "Control Reference Constant"

    def parse(self, elem: ET.Element) -> CtlRefConstNode:
        common = self._extract_common(elem)
        ddo_elem = elem.find("ddo")
        ddo_uid = ddo_elem.get("uid") if ddo_elem is not None else None
        return CtlRefConstNode(**common, ddo_uid=ddo_uid)


class GRefHandler(NodeTypeHandler):
    """Handler for Local Variable references (class="gRef").

    The single termList entry's <dco class="gRefDCO"> carries <paramIdx>,
    the connector-pane slot index of the referenced FP control.
    """

    xml_class = "gRef"
    display_name = "Local Variable"

    def parse(self, elem: ET.Element) -> GRefNode:
        common = self._extract_common(elem)
        param_idx = None
        term_list = elem.find("termList")
        if term_list is not None:
            term_elem = term_list.find("SL__arrayElement")
            if term_elem is not None:
                dco_elem = term_elem.find("dco")
                if dco_elem is not None:
                    idx_elem = dco_elem.find("paramIdx")
                    if idx_elem is not None and idx_elem.text:
                        param_idx = int(idx_elem.text)
        return GRefNode(**common, param_idx=param_idx)


class StatVIRefHandler(NodeTypeHandler):
    """Handler for Static VI Reference (class="statVIRef")."""

    xml_class = "statVIRef"
    display_name = "Static VI Reference"

    def parse(self, elem: ET.Element) -> StatVIRefNode:
        common = self._extract_common(elem)
        return StatVIRefNode(**common)


class DecomposeClusterHandler(NodeTypeHandler):
    """Handler for decompose cluster nodes (class="decomposeClusterNode").

    Decomposes a cluster into its fields inside an In Place Element Structure.
    Same dcoAgg/dcoList pattern as nMux, but uses <index> for field indices
    and <poser uid="..."/> to link to the paired recompose node.
    """

    xml_class = "decomposeClusterNode"
    # No "decompose" jargon in a user-facing name — this is LabVIEW's
    # Bundle/Unbundle BY NAME at an In Place Element Structure boundary (same
    # by-name field access as nMux). This static default is only a fallback:
    # the graph layer overrides it with the direction-correct "Bundle By
    # Name"/"Unbundle By Name" once terminal roles are known — see
    # graph/construction.py's decomposeClusterNode rename and
    # render/nodes.py::mux_display_name.
    display_name = "Bundle/Unbundle By Name"

    def parse(self, elem: ET.Element) -> SelectNode:
        common = self._extract_common(elem)

        dco_agg_elem = elem.find("dcoAgg")
        dco_agg_uid = dco_agg_elem.get("uid") if dco_agg_elem is not None else None

        dco_list_elem = elem.find("dcoList")
        dco_list_uids: list[str] = []
        if dco_list_elem is not None:
            for child in dco_list_elem.findall("SL__arrayElement"):
                uid = child.get("uid")
                if uid:
                    dco_list_uids.append(uid)

        term_to_dco: dict[str, str] = {}
        dco_field_index: dict[str, int] = {}
        list_dco_set = set(dco_list_uids)
        term_list = elem.find("termList")
        if term_list is not None:
            for term_elem in term_list.findall("SL__arrayElement"):
                t_uid = term_elem.get("uid")
                dco_elem = term_elem.find("dco")
                if t_uid and dco_elem is not None:
                    d_uid = dco_elem.get("uid")
                    if d_uid:
                        term_to_dco[t_uid] = d_uid
                        if d_uid in list_dco_set:
                            index_elem = dco_elem.find("index")
                            idx = (
                                int(index_elem.text)
                                if index_elem is not None and index_elem.text
                                else dco_list_uids.index(d_uid)
                            )
                            dco_field_index[d_uid] = idx

        poser_elem = elem.find("poser")
        poser_uid = poser_elem.get("uid") if poser_elem is not None else None

        return SelectNode(
            **common,
            dco_agg_uid=dco_agg_uid,
            dco_list_uids=dco_list_uids,
            term_to_dco=term_to_dco,
            dco_field_index=dco_field_index,
            poser_uid=poser_uid,
        )


class DecomposeArrayHandler(DecomposeClusterHandler):
    """Handler for decompose array nodes (class="decomposeArrayNode").

    Structurally identical to DecomposeClusterHandler — same dcoAgg/dcoList
    pattern with <index> and <poser> pairing.
    """

    xml_class = "decomposeArrayNode"
    # No "decompose" jargon in a user-facing name. Unlike decomposeClusterNode
    # this does NOT get the Bundle/Unbundle-By-Name glyph treatment (out of
    # scope — see render/nodes.py's ``_CLUSTER_MUX_TYPES``), so it stays the
    # generic labeled-box fallback under the faithful In Place Element name.
    display_name = "In Place Element"


class _DecomposeDataValRefHandler(NodeTypeHandler):
    """Stub handler for DVR decompose nodes (class="decomposeDataValRefNode").

    DVR decompose has a different structure — terminals are parsed so the graph
    stays consistent, but codegen is deferred to V2.
    """

    xml_class = "decomposeDataValRefNode"
    # Generic FALLBACK label. The faithful per-tile name ("DVR Read" on the
    # deref/read half, "DVR Write" on the store-back/write half) is set in graph
    # construction from the DataValueRef terminal side — see inplace_border_name.
    display_name = "In Place Element"

    def parse(self, elem: ET.Element) -> ParsedNode:
        common = self._extract_common(elem)
        return ParsedNode(**common)


class _DecomposeMatchHandler(NodeTypeHandler):
    """Stub handler for variant match nodes (class="decomposeMatchNode").

    Variant decompose — terminals are parsed but codegen is deferred to V2.
    """

    xml_class = "decomposeMatchNode"
    # No "decompose" jargon in a user-facing name. This is the IPES's generic
    # whole-value pass-through border node (confirmed: 2 terminals, no
    # dcoAgg/dcoList field shape at all — not a real bundle/unbundle), drawn
    # as a right-arrow glyph on both halves — see render/nodes.py /
    # render/glyph.py::InPlaceElementGlyph.
    display_name = "In Place Element"

    def parse(self, elem: ET.Element) -> ParsedNode:
        common = self._extract_common(elem)
        return ParsedNode(**common)


class FormulaNodeHandler(NodeTypeHandler):
    """Handler for Formula Nodes (class="fBox")."""

    xml_class = "fBox"
    display_name = "Formula Node"

    def parse(self, elem: ET.Element) -> FormulaNode:
        common = self._extract_common(elem)
        # The script lives in <formula class="textHair"><text>"..."</text>.
        # ElementTree already decodes XML entities (&lt; -> <); the text is
        # quote-wrapped, which clean_labview_string strips. Newlines/tabs are
        # preserved. Do NOT confuse with the sibling <lineNumbers>/<text>.
        text_elem = elem.find("formula/text")
        script = None
        if text_elem is not None and text_elem.text is not None:
            script = clean_labview_string(text_elem.text)
        return FormulaNode(**common, script=script)


class GenericHandler(NodeTypeHandler):
    """Fallback handler for unknown node types."""

    def __init__(self, xml_class: str, display_name: str | None = None):
        self.xml_class = xml_class
        self.display_name = display_name or xml_class

    def parse(self, elem: ET.Element) -> ParsedNode:
        common = self._extract_common(elem)
        return ParsedNode(**common)


# =============================================================================
# Registry and Factory
# =============================================================================

# Built-in array/string operations with specialized XML classes.
# These are block diagram primitives but use different XML class names
# than "prim" because they have expandable/polymorphic terminals.
# Parsed identically to PrimitiveHandler (they ARE primitives).
class _BuiltinPrimitiveHandler(NodeTypeHandler):
    """Handler for built-in primitives with non-standard XML classes.

    These are block diagram primitives that LabVIEW stores with their own
    XML class (aDelete, aIndx, etc.) instead of "prim". They don't have
    primResID in the XML, so we assign it here based on the known mapping.
    """

    def __init__(self, xml_class: str, display_name: str, prim_res_id: int | None):
        self.xml_class = xml_class
        self.display_name = display_name
        self.prim_res_id = prim_res_id

    def parse(self, elem: ET.Element) -> PrimitiveNode:
        common = self._extract_common(elem)
        return PrimitiveNode(
            **common,
            prim_index=None,
            prim_res_id=self.prim_res_id,
        )


# All known handlers
_HANDLERS: list[NodeTypeHandler] = [
    PrimitiveHandler(),
    SubVIHandler(),
    PolySubVIHandler(),
    DynamicDispatchHandler(),
    CallParentHandler(),
    CallByRefHandler(),
    CpdArithHandler(),
    ArrayBuildHandler(),
    ArrayInitHandler(),
    WhileLoopHandler(),
    ForLoopHandler(),
    SelectHandler(),
    PropertyNodeHandler(),
    InvokeNodeHandler(),
    FlatSequenceHandler(),
    StackedSequenceHandler(),
    _SequenceAliasHandler(),
    DisableStructureHandler(),
    PrintfHandler(),
    ScanfHandler(),
    NMuxHandler(),
    _MuxHandler(),
    _DemuxHandler(),
    _EventDataNodeHandler(),
    CtlRefConstHandler(),
    GRefHandler(),
    StatVIRefHandler(),
    FormulaNodeHandler(),
    DecomposeClusterHandler(),
    DecomposeArrayHandler(),
    _DecomposeDataValRefHandler(),
    _DecomposeMatchHandler(),
    # Built-in primitives with specialized XML classes.
    # aDelete/aIndx/subset resolve by XML class via the node_types section of
    # primitives.json, so they carry NO primResID. Their old numeric IDs were
    # *counter-indicated* — each belongs to a DIFFERENT plain-`prim` function
    # (1901=Search 1D Array, 1809=Array Size, 1516=Select). Since codegen
    # resolves node_type before primResID, keeping both was a latent trap: node
    # type wins, so the borrowed ID only ever caused wrong doc links / fallbacks.
    _BuiltinPrimitiveHandler("aDelete", "Delete From Array", None),
    _BuiltinPrimitiveHandler("aIndx", "Index Array", None),
    _BuiltinPrimitiveHandler("subset", "Array Subset", None),
    # mergeErrors/oHExt have NO node_types entry, so their primResID IS the
    # resolution path (and is not counter-indicated: 2401 really is Merge Errors;
    # 8069 has no competing prim-class entry). Keep them until/unless they get a
    # node_types entry.
    _BuiltinPrimitiveHandler("mergeErrors", "Merge Errors", 2401),
    _BuiltinPrimitiveHandler("oHExt", "Obtain/Release Semaphore", 8069),
    # Class-resolved primitives — no numeric primResID. These resolve via the
    # node_types section of primitives.json by XML class. Do NOT borrow a numeric
    # arithmetic resID: "concat" once used 1051, which is Subtract, so every
    # Concatenate Strings node rendered/generated as a subtraction.
    _BuiltinPrimitiveHandler("aInit", "Initialize Array", None),
    _BuiltinPrimitiveHandler("aReplace", "Replace Array Subset", None),
    _BuiltinPrimitiveHandler("aInsert", "Insert Into Array", None),
    _BuiltinPrimitiveHandler("aReshape", "Reshape Array", None),
    _BuiltinPrimitiveHandler("concat", "Concatenate Strings", None),
]

# Build registry from handlers
NODE_HANDLERS: dict[str, NodeTypeHandler] = {h.xml_class: h for h in _HANDLERS}


def parse_node(elem: ET.Element) -> ParsedNode:
    """Factory function - parse XML element into appropriate ParsedNode subclass.

    Args:
        elem: XML element with class attribute

    Returns:
        Appropriate ParsedNode subclass instance
    """
    xml_class = elem.get("class", "")
    handler = NODE_HANDLERS.get(xml_class)

    if handler:
        return handler.parse(elem)

    # Fallback for unknown types
    return GenericHandler(xml_class).parse(elem)


def get_display_name(node_type: str) -> str:
    """Get display name for a node type.

    Args:
        node_type: The XML class name (e.g., "cpdArith")

    Returns:
        Human-readable display name (e.g., "Compound Arithmetic")
    """
    handler = NODE_HANDLERS.get(node_type)
    return handler.display_name if handler else node_type
