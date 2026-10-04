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

from ..models import LVType
from .layout import _rect
from .models import ParsedNode
from .nodes.base import extract_label
from .utils import clean_labview_string, decode_hex_ascii, extract_caption

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


@dataclass
class FeedbackNode(ParsedNode):
    """A LabVIEW Feedback Node (class="hiddenFBNode"/"slaveFBInputNode").

    A Feedback Node is a Gated-SSA mu (exactly like a shift register): it
    carries a value from one loop iteration -- or VI call -- to the next. It
    is serialized as a MASTER/SLAVE PAIR, even when drawn as a single glyph:

    - ``hiddenFBNode`` (MASTER, ``is_master=True``): owns the OUTPUT terminal
      (``leftFeedback`` dco -- the value READ this iteration) and the
      INITIALIZER terminal (``initFeedback`` dco -- used on the first
      iteration). Carries ``<feedbackNodeDelay>`` (``delay_depth``, the
      z^-N depth) and ``<SlaveFBInputNode uid>`` (``partner_uid``).
    - ``slaveFBInputNode`` (SLAVE, ``is_master=False``): owns the single
      INPUT terminal (``rightFeedback`` dco -- the value WRITTEN each
      iteration) and back-links via ``<HiddenFBNode uid>`` (``partner_uid``).

    Which terminal is output vs init is faithful from wire direction (the
    master has exactly one output = read, one input = init); the master/slave
    link and delay are both fully present in the block-diagram heap, so the
    cross-iteration recurrence is determinable from the file alone.

    ``output_bmps`` is the master's own ``leftFeedback`` dco's
    ``<termBMPs>`` bitmap-selector code -- LabVIEW's own per-terminal glyph
    variant id. Verified against two real corpus instances in FPGA_v1.vi
    cross-checked pixel-for-pixel against the issue's reference screenshot:
    209 draws a LEFT-pointing arrow, 211 a RIGHT-pointing one. Any other
    value is unverified (``None`` from the glyph's point of view -- see
    ``render.nodes._feedback_arrow_points_left``).
    """

    is_master: bool = True
    partner_uid: str | None = None  # the linked slave (master) / master (slave)
    output_bmps: int | None = None  # master only; see docstring above
    delay_depth: int | None = None  # z^-N depth from feedbackNodeDelay; master only


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
        return {
            "uid": elem.get("uid"),
            "node_type": self.xml_class,
            "name": name or self.display_name,
            "label": name,
            "caption": extract_caption(elem),
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


class FeedbackMasterHandler(NodeTypeHandler):
    """Handler for the Feedback Node master/read side (class="hiddenFBNode").

    Extracts the master->slave link (``<SlaveFBInputNode uid>``) and the
    z^-N delay depth (``<feedbackNodeDelay>``, a 2-digit hex byte -- "01" ->
    1). The output (leftFeedback) and initializer (initFeedback) terminals
    carry on the base ParsedNode terminal fields, distinguished by direction.
    Previously unregistered: fell through to GenericHandler, which leaked the
    raw XML class "hiddenFBNode" as the node's display name.
    """

    xml_class = "hiddenFBNode"
    display_name = "Feedback Node"

    def parse(self, elem: ET.Element) -> FeedbackNode:
        common = self._extract_common(elem)
        slave = elem.find("SlaveFBInputNode")
        partner_uid = slave.get("uid") if slave is not None else None
        delay_depth = None
        delay_text = elem.findtext("feedbackNodeDelay")
        if delay_text:
            try:
                delay_depth = int(delay_text, 16)
            except ValueError:
                delay_depth = None
        output_bmps = None
        bmps_text = elem.findtext(".//dco[@class='leftFeedback']/termBMPs")
        if bmps_text:
            try:
                output_bmps = int(bmps_text)
            except ValueError:
                output_bmps = None
        return FeedbackNode(
            **common,
            is_master=True,
            partner_uid=partner_uid,
            delay_depth=delay_depth,
            output_bmps=output_bmps,
        )


class FeedbackSlaveHandler(NodeTypeHandler):
    """Handler for the Feedback Node write side (class="slaveFBInputNode").

    Owns the single INPUT terminal (rightFeedback -- the value written each
    iteration) and back-links to its master via ``<HiddenFBNode uid>``. The
    netlist absorbs this side into the master's Feedback Node projection.
    Previously unregistered: fell through to GenericHandler, which leaked the
    raw XML class "slaveFBInputNode" as the node's display name.
    """

    xml_class = "slaveFBInputNode"
    display_name = "Feedback Node"

    def parse(self, elem: ET.Element) -> FeedbackNode:
        common = self._extract_common(elem)
        master = elem.find("HiddenFBNode")
        partner_uid = master.get("uid") if master is not None else None
        return FeedbackNode(
            **common,
            is_master=False,
            partner_uid=partner_uid,
        )


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


class WhileLoopHandler(NodeTypeHandler):
    """Handler for While Loop nodes (class="whileLoop")."""

    xml_class = "whileLoop"
    display_name = "While Loop"

    def parse(self, elem: ET.Element) -> LoopNode:
        return LoopNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
            loop_type="whileLoop",
        )


class ForLoopHandler(NodeTypeHandler):
    """Handler for For Loop nodes (class="forLoop")."""

    xml_class = "forLoop"
    display_name = "For Loop"

    def parse(self, elem: ET.Element) -> LoopNode:
        return LoopNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
            loop_type="forLoop",
        )


class SelectHandler(NodeTypeHandler):
    """Handler for Select nodes (class="select").

    In this LV version, class="select" IS the Case Structure -- so this
    node's subtree contains a whole nested frame (subVI calls, primitives,
    etc.). extract_label is object-scoped: it returns this structure's OWN
    label, never a descendant's (an inner "addSkipped.vi" in frame 0), so we
    can use it directly and fall back to the display name.
    """

    xml_class = "select"
    display_name = "Select"

    def parse(self, elem: ET.Element) -> SelectNode:
        return SelectNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
        )


class CaseStructHandler(NodeTypeHandler):
    """Handler for Case Structure nodes serialized as class="caseStruct".

    Some LabVIEW versions serialize the Case Structure directly under this
    class instead of "select" (see SelectHandler) -- structurally identical.
    Frame content lives in ParsedCaseStructure, not on this bare node.
    Previously unregistered: fell through to GenericHandler, which used the
    raw XML class itself (not a real display word) as ``display_name``/
    ``name`` fallback.
    """

    xml_class = "caseStruct"
    display_name = "Case Structure"

    def parse(self, elem: ET.Element) -> ParsedNode:
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
        )


def _dco_list_terminal_uids(elem: ET.Element) -> list[str]:
    """Map an XML node's ``dcoList`` (dco uids, in heap/row order) to the
    TERMINAL uids that contain them, preserving ``dcoList`` order.

    ``dcoList`` is a node's real per-row/per-property terminal list --
    structurally DISTINCT from the fixed ``permDCOList`` (object reference
    in/out, error in/out on a Property/Invoke Node), which never appears in
    ``dcoList`` regardless of its own resolved TYPE. This matters because a
    property's VALUE can itself be Refnum-typed (e.g. a "Library:Project"
    property returns a Project reference) -- a type-based filter (Refnum vs.
    not) can't tell that apart from the object reference terminal, but this
    structural dcoList/permDCOList split always can. Shared by
    ``InvokeNodeHandler`` (``row_terminal_uids``) and ``PropertyNodeHandler``
    (``dco_terminal_uids``).
    """
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

    return [dco_to_term[d_uid] for d_uid in dco_list_uids if d_uid in dco_to_term]


@dataclass
class PropertyNode(ParsedNode):
    """A property node (class="propNode").

    LabVIEW draws a property node in one of two forms, distinguished by
    whether it is permanently bound to a specific front-panel control
    (IMPLICIT — created by dragging a control's icon onto the diagram, or
    Right-click control -> Create -> Property Node) or takes its identity
    from a wired reference (EXPLICIT). The heap tells them apart cleanly: an
    IMPLICIT node carries a direct ``<ddo uid="...">`` CHILD (a sibling of
    its own ``<termList>``, never a part of it) naming the BOUND control's
    own ddo in the front-panel heap, plus a ``<label>`` showing that
    control's NAME (e.g. "Abort") instead of the generic node name; an
    EXPLICIT node has NEITHER (verified on GTR's "Abort" boolean property
    node, uid 16295, bound-control ddo uid 10449, vs. "Set Front Panel
    Object Control Value.vi"'s explicit VI-reference property node, uid
    1110, which has no ``<ddo>`` and no ``<label>`` at all). Independently
    corroborated by wiring: the implicit node's reference-IN terminal is
    UNWIRED (its identity needs no wire) while the explicit node's is wired.
    """

    object_name: str = ""
    object_method_id: str = ""
    properties: list[dict[str, Any]] = field(default_factory=list)
    # ``dcoList`` re-expressed as TERMINAL uids, one per accessed property,
    # in heap order -- the FAITHFUL structural correlation between
    # ``properties[i]`` and its VALUE terminal (see
    # ``_dco_list_terminal_uids``). Exact even when a property's value is
    # itself Refnum-typed, unlike a type-based (Refnum/error-cluster) filter.
    dco_terminal_uids: list[str] = field(default_factory=list)
    # The BOUND control's own ddo uid (see class docstring) -- "" for an
    # EXPLICIT property node (no such binding). The sole discriminator: never
    # inferred from the label text or from wiring (those only corroborate).
    bound_control_uid: str = ""
    # The bound control's reconstructed LVType (via
    # ``fp_heap_type.reconstruct_control_lvtype`` on ``bound_control_uid``'s
    # ddo in the FRONT-PANEL heap -- resolved by ``_parse_block_diagram``,
    # which has both heaps, never by ``render/`` reading heap XML itself).
    # None for an explicit node, OR an implicit one whose control class this
    # reconstructor doesn't model (see that function's own docstring) --
    # never a guessed color in either case.
    bound_control_type: LVType | None = None


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

    Like ``PropertyNode``, an Invoke Node draws in one of two forms
    depending on whether it is permanently bound to a specific front-panel
    control (IMPLICIT) or takes its identity from a wired reference
    (EXPLICIT) -- the SAME heap discriminator: an IMPLICIT node carries a
    direct ``<ddo uid="...">`` CHILD (a sibling of its own ``<termList>``,
    never a part of it) naming the bound control, plus a ``<label>``
    showing that control's NAME instead of the generic node name (verified
    on GTR's "Test Hierarchy Tree" invoke node, uid 11387, bound-control ddo
    uid 12, ``<methName>"Custom Item Symbols.Revert Symbols"``). An EXPLICIT
    node has neither.
    """

    object_name: str = ""
    object_method_id: str = ""
    method_name: str = ""
    method_code: int = 0
    row_terminal_uids: list[str] = field(default_factory=list)
    # The BOUND control's own ddo uid (see class docstring) -- "" for an
    # EXPLICIT invoke node (no such binding). The sole discriminator: never
    # inferred from the label text or from wiring (those only corroborate).
    bound_control_uid: str = ""
    # The bound control's reconstructed LVType (via
    # ``fp_heap_type.reconstruct_control_lvtype`` on ``bound_control_uid``'s
    # ddo in the FRONT-PANEL heap -- resolved by ``_parse_block_diagram``,
    # which has both heaps, never by ``render/`` reading heap XML itself).
    # None for an explicit node, OR an implicit one whose control class this
    # reconstructor doesn't model (see that function's own docstring) --
    # never a guessed color in either case.
    bound_control_type: LVType | None = None


@dataclass
class EventRegNode(ParsedNode):
    """A Register-For-Events node (class="eventRegNode", task #56).

    A property-node-style box with a GROWABLE drawer: one row per event
    source registered on it ("event 1", "event 2", ... always an INPUT --
    the source refnum to register events on -- unlike a property row, which
    can be read or write). Verified on GTR's "Main UI" node (heap uid
    11756, ``<nodeName>"Reg Events"</nodeName>``) and a 5-row real corpus
    example (DCAF-DAQModule's "Register For Events.vi", uid 110): the
    node's own ``<dcoList>`` lists each registered row's ``eventRegItem``
    dco uid, in heap order -- the EXACT SAME structural convention
    ``PropertyNode``/``InvokeNode`` already use for their own growable
    parts (see ``_dco_list_terminal_uids``), so the row COUNT is fully
    data-driven, never a hard-coded guess.

    The header shows this node's own heap-recorded name -- ``object_name``,
    from ``<nodeName>`` -- NEVER a hard-coded "Register For Events"/
    "Unregister For Events" guess: every real corpus instance (23 across 21
    files) records ``"Reg Events"``, and reading the field directly (the
    SAME mechanism ``PropertyNode``/``InvokeNode`` already use) means a
    differently-named variant (e.g. an "Unregister For Events" node, if one
    ever turns up with a different recorded name) renders correctly with
    ZERO special-casing.

    The event-registration-refnum (in/out, ``LVType.ref_type == "EventReg"``)
    and error (in/out, the standard status/code/source cluster) terminals
    thread the box edges at the header level, placed by the scene from the
    node's real heap terminal geometry -- not drawn here, same as
    ``PropertyNode``'s reference/error terminals.
    """

    object_name: str = ""
    object_method_id: str = ""
    # <dcoList> re-expressed as TERMINAL uids, one per registered event
    # source, in heap order -- each is a growable row's own INPUT terminal.
    event_row_terminal_uids: list[str] = field(default_factory=list)


@dataclass
class XNodeNode(ParsedNode):
    """An FPGA Interface XNode (class="xNode", #107) -- the family behind
    every FPGA Interface palette function: Open/Close FPGA VI Reference,
    Read/Write Control, Invoke Method, Wait/Acknowledge on IRQ, etc.

    A property-node-style drawer, but keyed by its OWN two hex-encoded text
    fields rather than ``<nodeName>``/``<methName>``:

    - ``class_name`` -- the node's own real kind, from its direct
      ``<displayName>`` child (e.g. ``"Invoke Method"``, ``"Read/Write
      Control"``, ``"Open FPGA VI Reference"``) -- confirmed corpus-wide:
      every real instance across two real projects decoded to one of these
      exact FPGA Interface palette names.
    - ``method_name`` -- for an "Invoke Method" node ONLY, the specific
      invoked method (e.g. ``"Run"``, ``"Wait on IRQ"``,
      ``"Raw data to RT.Configure"``) -- the FIRST 4-byte-length-prefixed
      ASCII string in its ``<StateData>`` blob. Verified against 6 real
      "Invoke Method" instances: in every case this exact string is the
      method shown in LabVIEW's own drawer. ``StateData`` is otherwise an
      opaque, node-kind-specific binary blob -- nothing else in it is
      decoded, and a "Read/Write Control"/"Open FPGA VI Reference" node's
      leading bytes here are NOT a string (empty/non-printable), so
      ``method_name`` is "" for those, never a garbled guess.

    Each terminal's own name USUALLY comes from its ``xTunnel`` dco's
    ``<englishName>`` (``extract_xtunnel_name``, already applied generically
    in ``_process_element_terminals``) -- including the reference
    (``FPGA VI Reference In``/``Out``) and error (``error in``/``out``) pass-
    through pair, which the render layer identifies by TYPE (a refnum / the
    standard Error cluster), never by this name text, and excludes from the
    drawer -- same convention as ``PropertyNode``/``InvokeNode``'s
    permDCOList pair.

    BUT some XNode classes ("FPGA I/O Node", "FPGA I/O Property Node") never
    set ``<englishName>`` at all (it's a literal unset null byte on every
    terminal) even though LabVIEW's own drawer shows real per-row names --
    e.g. "Antenna Status"/"Satellites Available"/"UTC Offset"/"UTC Offset
    Valid". For these, the real names live in ``<StateData>`` instead, each
    recorded TWICE in close succession (bare, then -- for a few properties --
    repeated with a unit suffix, e.g. "Longitude" then "Longitude (°)"):
    ``state_row_names`` holds them, termList order, richer/longer copy kept,
    with the method name (when present) excluded (it's recorded there too,
    but it's the header/method row, not a param). The render layer
    (``_xnode_glyph``) uses a terminal's own ``englishName``-decoded name
    when present, else the next unused ``state_row_names`` entry in order --
    verified against 3 real instances spanning both node families."""

    class_name: str = ""
    method_name: str = ""
    state_row_names: list[str] = field(default_factory=list)
    # The bound resource/module identifier (e.g. "Mod4"), LabVIEW's own real
    # header text for "FPGA I/O Property Node" -- see
    # ``_xnode_state_resource_name``. "" draws a BLANK header band (an "FPGA
    # I/O Node" reading raw channels), never the generic class name.
    resource_name: str = ""
    # Each real terminal's own (y1, y2) vertical span, as a fraction of the
    # node's own height, in termList order -- from the heap's own
    # termBounds (see ``_xnode_terminal_y_fracs``). Real row heights are NOT
    # uniform (verified: FIFO Write's Element/Timeout/Timed Out? rows are
    # 15/15/14 units, with a much taller 35-unit gap before them for the
    # header) -- equal-dividing the drawer box, as the glyph used to,
    # visibly misaligns every row against its own real wire. (0.0, 0.0) for
    # a terminal whose termBounds couldn't be read.
    terminal_y_fracs: list[tuple[float, float]] = field(default_factory=list)


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

        # An IMPLICIT property node's DIRECT <ddo> CHILD (a sibling of its
        # own <termList>, never a part of it) names the front-panel control
        # it's permanently bound to (see PropertyNode's class docstring) --
        # absent entirely for an EXPLICIT one.
        bound_ddo = elem.find("ddo")
        bound_control_uid = bound_ddo.get("uid", "") if bound_ddo is not None else ""

        return PropertyNode(
            **common,
            object_name=object_name,
            object_method_id=omid,
            properties=properties,
            dco_terminal_uids=_dco_list_terminal_uids(elem),
            bound_control_uid=bound_control_uid,
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
        # An IMPLICIT invoke node's DIRECT <ddo> CHILD (a sibling of its own
        # <termList>, never a part of it) names the front-panel control it's
        # permanently bound to (see InvokeNode's class docstring) -- absent
        # entirely for an EXPLICIT one.
        bound_ddo = elem.find("ddo")
        bound_control_uid = bound_ddo.get("uid", "") if bound_ddo is not None else ""
        return InvokeNode(
            **common,
            object_name=clean_labview_string(elem.findtext("nodeName")),
            object_method_id=elem.findtext("oMId") or "",
            method_name=clean_labview_string(elem.findtext("methName")),
            method_code=meth_code,
            row_terminal_uids=_dco_list_terminal_uids(elem),
            bound_control_uid=bound_control_uid,
        )


class EventRegNodeHandler(NodeTypeHandler):
    """Handler for Register-For-Events node (class="eventRegNode", task
    #56). ``display_name`` is a generic last-resort fallback (matching the
    convention "Property Node"/"Invoke Node" already use above) -- the REAL
    per-instance name always comes from ``<nodeName>`` (see EventRegNode's
    class docstring), never this hard-coded string."""

    xml_class = "eventRegNode"
    display_name = "Reg Events"

    def parse(self, elem: ET.Element) -> EventRegNode:
        common = self._extract_common(elem)
        return EventRegNode(
            **common,
            object_name=clean_labview_string(elem.findtext("nodeName")),
            object_method_id=elem.findtext("oMId") or "",
            event_row_terminal_uids=_dco_list_terminal_uids(elem),
        )


def _xnode_state_string(raw: bytes, offset: int) -> str | None:
    """A single 4-byte-big-endian-length-prefixed string at ``offset`` in an
    XNode's decoded ``<StateData>`` blob, or ``None`` when the length prefix
    doesn't fit or the bytes aren't printable text (UTF-8 -- a coordinate
    property's unit suffix carries a real ``°``, see ``XNodeNode``'s
    docstring)."""
    if offset + 4 > len(raw):
        return None
    n = int.from_bytes(raw[offset : offset + 4], "big")
    if n <= 1 or offset + 4 + n > len(raw):
        return None
    try:
        s = raw[offset + 4 : offset + 4 + n].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return s if s.isprintable() else None


def _xnode_state_strings(raw: bytes) -> list[str]:
    """Every length-prefixed text string findable at ANY byte offset in an
    XNode's ``<StateData>`` blob, in offset order -- a sliding scan, since
    the surrounding binary (type descriptors, enum item lists, a resource
    GUID) carries no reliable record boundaries of its own."""
    return [s for i in range(len(raw) - 4) if (s := _xnode_state_string(raw, i))]


def _xnode_state_method_name(state_data_hex: str | None) -> str:
    """The FIRST string in an "Invoke Method" XNode's ``<StateData>`` blob --
    empirically the exact invoked method name (see ``XNodeNode``'s own
    docstring for the verification). Returns "" when absent/undecodable."""
    if not state_data_hex:
        return ""
    try:
        raw = bytes.fromhex(state_data_hex.strip())
    except ValueError:
        return ""
    return _xnode_state_string(raw, 0) or ""


_RESOURCE_GUID_SUFFIX_RE = re.compile(r"\.\{[0-9A-Fa-f-]+\}$")

# A reference terminal's own generic descriptor -- a FIXED LabVIEW-internal
# label for ANY reference-typed xNode terminal, never instance data (verified
# real corpus: FPGA_v1.vi's "FIFO Write" node records "ref in"/"ref out"
# beside its FIFO In/FIFO Out reference pair).
_GENERIC_REF_LABELS = frozenset({"ref in", "ref out"})
# The literal protocol name that always introduces a container-interface
# descriptor triplet -- see _xnode_state_filtered_strings.
_CONTAINER_INTERFACE_MARKER = "ContainerInterface"


def _strip_xnode_resource_guid(name: str) -> str:
    """Strips a resource identifier's trailing ``.{GUID}`` (e.g.
    ``"Mod4.{305F45FE-...}"`` -> ``"Mod4"``, ``"Mod1/AI0.{A117026A-...}"`` ->
    ``"Mod1/AI0"``) -- LabVIEW's own drawer never shows the GUID, only the
    resource path (verified against the real corpus: FPGA_v1.vi's "Antenna
    Status" node's header reads "Mod4", its "FPGA I/O Node" reads
    "Mod1/AI0"/"Mod1/AI2", never with a GUID suffix)."""
    return _RESOURCE_GUID_SUFFIX_RE.sub("", name)


def _xnode_state_filtered_strings(raw: bytes) -> list[str]:
    """``_xnode_state_strings`` with known non-label metadata stripped --
    verified against FPGA_v1.vi's real "FIFO Write" node (cross-checked
    against raph's own screenshot of this exact VI):

    * A CONTAINER-INTERFACE descriptor TRIPLET: the literal protocol name
      ``"ContainerInterface"``, the XML ``<Interface>...</Interface>``
      descriptor that always immediately follows it, and the general
      container-TYPE name right after that (e.g. ``"FPGA FIFO"``) -- one
      such triplet is recorded per reference terminal (ref in, then ref
      out), describing what kind of container the reference points to, never
      a row's own display label. None of the three ever appears in LabVIEW's
      own drawer.
    * The generic reference-protocol labels ``"ref in"``/``"ref out"`` (see
      ``_GENERIC_REF_LABELS``).
    """
    strings = _xnode_state_strings(raw)
    out: list[str] = []
    i = 0
    while i < len(strings):
        s = strings[i]
        if (
            s == _CONTAINER_INTERFACE_MARKER
            and i + 1 < len(strings)
            and strings[i + 1].startswith("<")
        ):
            i += 3 if i + 2 < len(strings) else 2  # marker, xml, [type name]
            continue
        if s in _GENERIC_REF_LABELS:
            i += 1
            continue
        out.append(s)
        i += 1
    return out


def _xnode_state_filtered_strings_from_hex(state_data_hex: str | None) -> list[str]:
    if not state_data_hex:
        return []
    try:
        raw = bytes.fromhex(state_data_hex.strip())
    except ValueError:
        return []
    return _xnode_state_filtered_strings(raw)


def _xnode_state_header(strings: list[str]) -> str:
    """The leading string, when it is NOT an ordinary paired row.

    Verified across three real instances: a resource header with no echo
    occurs exactly ONCE total (FPGA_v1.vi's "Antenna Status" node:
    ``"Mod4.{GUID}"``); an instance-name header CAN echo again inside each
    reference terminal's own (now-stripped) metadata, so it occurs MORE than
    twice total (FPGA_v1.vi's "FIFO Write" node: ``"Raw data to RT"`` occurs
    3 times -- once leading, once inside each of the two reference blocks);
    an ORDINARY paired row occurs exactly TWICE total and leads only because
    it happens to be termList-first (FPGA_v1.vi's "FPGA I/O Node" reading raw
    channels: ``"Mod1/AI0"`` occurs exactly twice, and real LabVIEW draws a
    BLANK header for this node -- never a header). So: count != 2 -> header;
    count == 2 -> an ordinary row, "" (no header). A row's own pair can be a
    PREFIX extension rather than an exact duplicate (e.g. "Longitude" /
    "Longitude (°)"), so matches are counted by the SAME predicate the row
    pairing below uses, not exact equality alone."""
    if not strings:
        return ""
    first = strings[0]
    matches = sum(1 for s in strings if s == first or s.startswith(first))
    return first if matches != 2 else ""


def _xnode_state_row_names(state_data_hex: str | None) -> list[str]:
    """Every per-row PARAMETER/PROPERTY name in an XNode's ``<StateData>``
    blob, in termList order (see ``XNodeNode``'s own docstring for the
    verification against real instances across "Invoke Method", "FPGA I/O
    Property Node", "FPGA I/O Node" AND "FIFO Write"). Each real row name is
    recorded TWICE -- once bare, once (for "FPGA I/O Property Node") repeated
    with a unit suffix appended (e.g. "Longitude" then "Longitude (°)") --
    so two extracted strings where the second equals or extends the first is
    the row-name signal. The two copies are NOT always adjacent: an "FPGA
    I/O Node" reading raw channels (verified real instance: "Mod1/AI0"/
    "Mod1/AI2") records ALL first copies, then ALL second copies, so the
    pairing scans the REST of the list for each string's own match rather
    than only the next entry. The leading HEADER string (see
    ``_xnode_state_header``) is excluded first, whatever its own occurrence
    count, so it never gets mistaken for a row. A string with no match
    anywhere (an enum item list, a lone occurrence) is incidental metadata
    and skipped. The richer (second-occurrence) copy is kept, GUID-suffix
    stripped, in FIRST-occurrence order. Returns [] when the blob is
    absent/undecodable -- never a partial/garbled list."""
    strings = _xnode_state_filtered_strings_from_hex(state_data_hex)
    header = _xnode_state_header(strings)
    if header:
        strings = [s for s in strings if s != header]
    used = [False] * len(strings)
    names: list[str] = []
    for i, a in enumerate(strings):
        if used[i]:
            continue
        for j in range(i + 1, len(strings)):
            if used[j]:
                continue
            b = strings[j]
            if b == a or b.startswith(a):
                used[i] = used[j] = True
                names.append(_strip_xnode_resource_guid(b))
                break
    return names


def _xnode_state_resource_name(state_data_hex: str | None) -> str:
    """The XNode's own bound resource/instance identifier (e.g. ``"Mod4"``,
    ``"Raw data to RT"``) -- LabVIEW's own real header text. See
    ``_xnode_state_header`` for the occurrence-count rule. "" when the
    leading string is an ordinary paired row instead (an "FPGA I/O Node"
    reading raw channels has no separate resource header; its row names ARE
    the channel paths, e.g. "Mod1/AI0"/"Mod1/AI2" -- real LabVIEW draws
    these with a BLANK header band, never the generic class name)."""
    strings = _xnode_state_filtered_strings_from_hex(state_data_hex)
    header = _xnode_state_header(strings)
    return _strip_xnode_resource_guid(header) if header else ""


def _xnode_class_method_name(class_name: str, state_data_hex: str | None) -> str:
    """A non-"Invoke Method" XNode's own operation sub-type (e.g. "FIFO
    Write"/"FIFO Read" -> "Write"/"Read", verified against FPGA_v1.vi's real
    "FIFO Write" node, cross-checked against raph's own screenshot of it) --
    the class name's own LAST WORD, drawn as its own row directly under the
    header, same convention as "Invoke Method"'s method row. Only when that
    exact word ALSO appears in the (filtered) ``<StateData>`` -- confirms a
    real recorded row rather than guessing from the class name's own English
    wording alone (e.g. "Open FPGA VI Reference" has no such row: "Reference"
    never appears in its StateData, so this correctly returns "")."""
    words = class_name.split()
    if len(words) < 2:
        return ""
    candidate = words[-1]
    strings = _xnode_state_filtered_strings_from_hex(state_data_hex)
    return candidate if candidate in strings else ""


def _xnode_terminal_y_fracs(elem: ET.Element) -> list[tuple[float, float]]:
    """Each real terminal's own ``(y1, y2)`` vertical span, as a fraction of
    the node's own VISUAL height, in ``termList`` order -- the same order
    ``node.terminals`` is built in.

    The scaling height is the UNION of every terminal's own ``termBounds``
    extent (min top to max bottom) -- NOT the xNode element's own
    ``<bounds>`` tag, which can record a TALLER box than what's actually
    drawn (verified bug: FPGA_v1.vi's "FPGA I/O Node" reading "Mod1/AI0"/
    "Mod1/AI2", whose own ``<bounds>`` is 55 units tall but whose real
    terminals only span 18-52 = 34 units -- using the raw 55 put every row
    well below where the real wire attaches, which ``layout._map_terms``
    computes from this SAME union-of-terminals convention and therefore
    disagreed with). A terminal's ``termBounds`` is already relative to the
    node's own (0, 0) origin (a heap-format fact), so each fraction is
    ``(ty - union_top) / union_height``. ``(0.0, 0.0)`` for a terminal whose
    termBounds can't be read (falls back to equal division at the glyph)."""
    term_list = elem.find("termList")
    if term_list is None:
        return []
    terms = term_list.findall("SL__arrayElement")
    term_bounds = [_rect(term, ".//termBounds") for term in terms]
    real_tops = [tb[1] for tb in term_bounds if tb is not None]
    real_bottoms = [tb[3] for tb in term_bounds if tb is not None]
    if not real_tops:
        return []
    top, height = min(real_tops), max(real_bottoms) - min(real_tops)
    if height <= 0:
        return []
    fracs: list[tuple[float, float]] = []
    for tb in term_bounds:
        if tb is None:
            fracs.append((0.0, 0.0))
            continue
        _, ty1, _, ty2 = tb
        ty1, ty2 = ty1 - top, ty2 - top
        fracs.append((ty1 / height, ty2 / height))
    return fracs


class XNodeHandler(NodeTypeHandler):
    """Handler for an FPGA Interface XNode (class="xNode", #107). See
    ``XNodeNode``'s own docstring for the field decoding."""

    xml_class = "xNode"
    display_name = "xNode"

    def parse(self, elem: ET.Element) -> XNodeNode:
        common = self._extract_common(elem)
        class_name = decode_hex_ascii(elem.findtext("displayName")) or ""
        state_data = elem.findtext("StateData")
        if class_name == "Invoke Method":
            method_name = _xnode_state_method_name(state_data)
        else:
            method_name = _xnode_class_method_name(class_name, state_data)
        # The method name (when present) is ALSO recorded as a row-name pair
        # elsewhere in the same blob -- exclude it, it's the header/method
        # row (drawn separately), never one of the node's own param rows.
        row_names = [
            n for n in _xnode_state_row_names(state_data) if n != method_name
        ]
        resource_name = _xnode_state_resource_name(state_data)
        # An "Invoke Method" node's leading StateData string IS its method
        # name (unpaired, read above) -- it is ALSO, by construction, the one
        # ``_xnode_state_resource_name`` would read as a header (same string,
        # same position). Verified against every real "Invoke Method"
        # instance in RT_v1.vi (e.g. "Raw data to RT.Configure",
        # "Raw data to RT.Stop", "Run"): without this check the identical
        # text drew TWICE -- once as the header, once as the method row --
        # splitting the box's real header gap in half for nothing.
        if resource_name == method_name:
            resource_name = ""
        return XNodeNode(
            **common,
            class_name=class_name,
            method_name=method_name,
            state_row_names=row_names,
            resource_name=resource_name,
            terminal_y_fracs=_xnode_terminal_y_fracs(elem),
        )


class FlatSequenceHandler(NodeTypeHandler):
    """Handler for Flat Sequence structures (class="flatSequence")."""

    xml_class = "flatSequence"
    display_name = "Flat Sequence"

    def parse(self, elem: ET.Element) -> ParsedNode:
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
        )


class StackedSequenceHandler(NodeTypeHandler):
    """Handler for Stacked Sequence structures (class="seq" or "sequence").

    LabVIEW serializes stacked sequences as class="seq" in most versions and
    class="sequence" in older versions. Both are structurally identical.
    """

    xml_class = "seq"
    display_name = "Stacked Sequence"

    def parse(self, elem: ET.Element) -> ParsedNode:
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
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
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
        )


class EventStructHandler(NodeTypeHandler):
    """Handler for Event Structure nodes (class="eventStruct").

    Frame content (registered events, data/filter nodes) lives in
    ParsedEventStructure (parser/nodes/event.py), not on this bare node --
    mirrors FlatSequenceHandler/DisableStructureHandler. Previously
    unregistered: fell through to GenericHandler, which used the raw XML
    class itself ("eventStruct") as ``display_name``/``name`` fallback --
    leaking it verbatim for an unlabeled Event Structure.
    """

    xml_class = "eventStruct"
    display_name = "Event Structure"

    def parse(self, elem: ET.Element) -> ParsedNode:
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
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


def _build_nmux_dco_maps(
    elem: ET.Element,
    dco_list_uids: list[str],
    index_tag: str,
    positional: bool = False,
) -> tuple[dict[str, str], dict[str, int]]:
    """``(term_to_dco, dco_field_index)`` for a bundle/unbundle node's
    ``termList`` — shared by the nMux, positional mux/demux, and decompose (IPE)
    handlers.

    Every terminal maps to its DCO uid; each LIST DCO (a drawer) also gets the
    cluster-field index it selects. Two families differ in how that index is
    determined:

    - BY NAME (``positional=False`` — nMux, decompose): the index is read from
      the drawer's ``<index_tag>`` element (``"i"`` for nMux, ``"index"`` for
      decompose). LabVIEW OMITS that element when the selected field is index 0
      (the default), so an ABSENT element means field 0 — NOT the drawer's list
      position (that mislabeled e.g. a "Name" drawer sitting at list slot 2,
      #36). A non-zero index is always serialized, so the ``0`` fallback only
      ever fills in that one case.
    - POSITIONAL (``positional=True`` — classic Bundle/Unbundle): drawers carry
      no per-field index element at all; the k-th LIST drawer selects the k-th
      cluster field. The index is the drawer's position in ``dco_list_uids``.
    """
    term_to_dco: dict[str, str] = {}
    dco_field_index: dict[str, int] = {}
    list_dco_pos = {uid: pos for pos, uid in enumerate(dco_list_uids)}
    term_list = elem.find("termList")
    if term_list is not None:
        for term_elem in term_list.findall("SL__arrayElement"):
            t_uid = term_elem.get("uid")
            dco_elem = term_elem.find("dco")
            if t_uid and dco_elem is not None:
                d_uid = dco_elem.get("uid")
                if d_uid:
                    term_to_dco[t_uid] = d_uid
                    if d_uid in list_dco_pos:
                        if positional:
                            dco_field_index[d_uid] = list_dco_pos[d_uid]
                        else:
                            i_elem = dco_elem.find(index_tag)
                            dco_field_index[d_uid] = (
                                int(i_elem.text)
                                if i_elem is not None and i_elem.text
                                else 0
                            )
    return term_to_dco, dco_field_index


class NMuxHandler(NodeTypeHandler):
    """Handler for Node Multiplexer (class="nMux").

    nMux is a bundle/unbundle node at structure boundaries.
    dcoAgg is the aggregate (cluster) terminal, dcoList are field terminals.
    """

    xml_class = "nMux"
    display_name = "Bundle/Unbundle By Name"
    # By-name (nMux): each drawer's field is read from its <i> element.
    # Positional (classic mux/demux): the k-th drawer selects the k-th field.
    positional_fields = False

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

        term_to_dco, dco_field_index = _build_nmux_dco_maps(
            elem, dco_list_uids, "i", positional=self.positional_fields
        )

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
    positional_fields = True


class _DemuxHandler(NMuxHandler):
    """Handles class="demux" — unbundle at structure boundaries.

    Structurally identical to nMux but uses positional field indices
    (no <i> tags on dmxDCO elements).
    """

    xml_class = "demux"
    display_name = "Unbundle"
    positional_fields = True


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

        # The decompose (IPE) structure stores each drawer's field index in an
        # <index> element (nMux uses <i>) — otherwise identical.
        term_to_dco, dco_field_index = _build_nmux_dco_maps(
            elem, dco_list_uids, "index"
        )

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


class DecomposeRecomposeStructureHandler(NodeTypeHandler):
    """Handler for the In Place Element Structure (IPES) container itself
    (class="decomposeRecomposeStructure") -- the outer border, distinct from
    its inner decompose/recompose border tiles (decomposeClusterNode /
    decomposeArrayNode / decomposeDataValRefNode / decomposeMatchNode, which
    stay primitive-like operations parsed via ``_extract_common`` above).

    Frame content lives in ParsedDecomposeRecomposeStructure, not on this
    bare node -- mirrors FlatSequenceHandler/DisableStructureHandler.
    Previously unregistered: fell through to GenericHandler, which used the
    raw XML class itself ("decomposeRecomposeStructure") as
    ``display_name``/``name`` fallback -- leaking it verbatim for an
    unlabeled IPES structure.
    """

    xml_class = "decomposeRecomposeStructure"
    display_name = "In Place Element"

    def parse(self, elem: ET.Element) -> ParsedNode:
        return ParsedNode(
            uid=elem.get("uid", ""),
            node_type=self.xml_class,
            name=None,
            label=extract_label(elem),
            caption=extract_caption(elem),
        )


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
    FeedbackMasterHandler(),
    FeedbackSlaveHandler(),
    CpdArithHandler(),
    ArrayBuildHandler(),
    WhileLoopHandler(),
    ForLoopHandler(),
    SelectHandler(),
    CaseStructHandler(),
    PropertyNodeHandler(),
    InvokeNodeHandler(),
    EventRegNodeHandler(),
    XNodeHandler(),
    FlatSequenceHandler(),
    StackedSequenceHandler(),
    _SequenceAliasHandler(),
    DisableStructureHandler(),
    EventStructHandler(),
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
    DecomposeRecomposeStructureHandler(),
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
    # resolution path (and is not counter-indicated: 2147 really is Merge
    # Errors -- confirmed by nodes.json's VI-Scripting export, whose pane for
    # 2147 is error_cluster out x1 / error_cluster in x2, matching the
    # expandable Merge Errors doc; 2401 is a different primitive, Swap Values
    # (#59). 8069 has no competing prim-class entry). Keep them until/unless
    # they get a node_types entry.
    _BuiltinPrimitiveHandler("mergeErrors", "Merge Errors", 2147),
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
