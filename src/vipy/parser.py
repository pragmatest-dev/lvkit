"""Parse pylabview XML output into a structured graph representation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    CONSTANT_DCO_CLASS,
    FP_TERMINAL_CLASS,
    LOOP_NODE_CLASSES,
    MULTI_LABEL_CLASS,
    OPERATION_NODE_CLASSES,
    TERMINAL_CLASS,
    TERMINAL_CONTAINER_CLASSES,
    TUNNEL_DCO_CLASSES,
)


@dataclass
class Node:
    """A node in the block diagram (SubVI call, primitive, or terminal)."""
    uid: str
    node_type: str  # "iUse" (SubVI), "prim" (primitive), "term" (terminal)
    name: str | None = None
    prim_index: int | None = None
    prim_res_id: int | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    input_types: list[str] = field(default_factory=list)   # typeDesc for inputs
    output_types: list[str] = field(default_factory=list)  # typeDesc for outputs


@dataclass
class Constant:
    """A constant value on the block diagram."""
    uid: str
    type_desc: str
    value: str
    label: str | None = None


@dataclass
class Wire:
    """A wire connecting terminals."""
    uid: str
    from_term: str
    to_term: str


@dataclass
class FPTerminal:
    """A front panel terminal (VI input or output)."""
    uid: str
    fp_dco_uid: str  # Links to front panel control/indicator
    name: str | None = None
    is_indicator: bool = False  # True = output, False = input (control)


@dataclass
class TerminalInfo:
    """Detailed info about a terminal for graph-native representation."""
    uid: str
    parent_uid: str
    index: int  # Position in parent's termList
    is_output: bool  # True if output terminal (data flows out)
    type_id: str | None = None  # e.g., "TypeID(5)" or resolved type name
    name: str | None = None  # Terminal name (from FP, primitive ref, or SubVI)


class WiringRule:
    """Terminal wiring rule - controls required/recommended/optional status."""
    INVALID = 0
    REQUIRED = 1
    RECOMMENDED = 2
    OPTIONAL = 3
    DYNAMIC_DISPATCH = 4


@dataclass
class TunnelMapping:
    """Maps outer loop terminal to inner terminal.

    In LabVIEW loops, data enters/exits via tunnels:
    - lSR (left shift register): Input tunnel, value persists across iterations
    - rSR (right shift register): Output tunnel, value persists across iterations
    - lpTun (loop tunnel): Simple pass-through, same value each iteration
    - lMax: Accumulator/max output
    """
    outer_terminal_uid: str  # Terminal on loop boundary (outside)
    inner_terminal_uid: str  # Terminal inside the loop diagram
    tunnel_type: str  # "lSR", "rSR", "lpTun", "lMax"
    paired_terminal_uid: str | None = None  # For shift registers: the other side


@dataclass
class LoopStructure:
    """A loop structure (while or for) on the block diagram.

    Contains:
    - Loop boundary terminals that connect to tunnels
    - Tunnel mappings linking outer↔inner terminals
    - Reference to inner diagram containing loop body operations
    - Stop condition terminal (for while loops)
    """
    uid: str
    loop_type: str  # "whileLoop" or "forLoop"
    boundary_terminal_uids: list[str] = field(default_factory=list)  # Terminals on loop border
    tunnels: list[TunnelMapping] = field(default_factory=list)  # Outer↔inner mappings
    inner_diagram_uid: str | None = None  # UID of the inner diagram (diag element)
    inner_node_uids: list[str] = field(default_factory=list)  # Operations inside this loop
    stop_condition_terminal_uid: str | None = None  # While loop stop condition input (lTst)


@dataclass
class ConnectorPaneSlot:
    """A slot on the connector pane."""
    index: int  # Slot position (0-based)
    fp_dco_uid: str | None = None  # UID of the connected fPDCO (control/indicator)
    is_output: bool = False  # True if output terminal
    wiring_rule: int = 0  # WiringRule value (0-4)
    type_id: str | None = None  # TypeID reference


@dataclass
class ConnectorPane:
    """The VI's connector pane - defines its external interface."""
    pattern_id: int  # conId - identifies the connector pane pattern
    slots: list[ConnectorPaneSlot] = field(default_factory=list)

    def get_connected_uids(self) -> list[str]:
        """Get UIDs of all controls/indicators connected to the pane."""
        return [s.fp_dco_uid for s in self.slots if s.fp_dco_uid]


@dataclass
class BlockDiagram:
    """Parsed block diagram representation."""
    nodes: list[Node]
    constants: list[Constant]
    wires: list[Wire]
    fp_terminals: list[FPTerminal] = field(default_factory=list)
    enum_labels: dict[str, list[str]] = field(default_factory=dict)  # uid -> labels
    terminal_info: dict[str, TerminalInfo] = field(default_factory=dict)  # terminal uid -> info
    loops: list[LoopStructure] = field(default_factory=list)  # Loop structures with tunnels

    def get_node(self, uid: str) -> Node | None:
        """Get a node by UID."""
        for node in self.nodes:
            if node.uid == uid:
                return node
        return None

    def get_parent_uid(self, terminal_uid: str) -> str | None:
        """Get parent node UID for a terminal."""
        info = self.terminal_info.get(terminal_uid)
        return info.parent_uid if info else None

    def get_loop(self, uid: str) -> LoopStructure | None:
        """Get a loop by UID."""
        for loop in self.loops:
            if loop.uid == uid:
                return loop
        return None

    def get_tunnel_mapping(self, terminal_uid: str) -> TunnelMapping | None:
        """Find tunnel mapping for a terminal (either outer or inner)."""
        for loop in self.loops:
            for tunnel in loop.tunnels:
                if tunnel.outer_terminal_uid == terminal_uid:
                    return tunnel
                if tunnel.inner_terminal_uid == terminal_uid:
                    return tunnel
        return None


def parse_block_diagram(xml_path: Path | str) -> BlockDiagram:
    """Parse a pylabview block diagram XML file.

    Args:
        xml_path: Path to the *_BDHb.xml file

    Returns:
        BlockDiagram with extracted nodes, constants, and wires
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    nodes = _extract_nodes(root)
    constants = _extract_constants(root)
    wires = _extract_wires(root)
    fp_terminals = _extract_fp_terminals(root)
    enum_labels = _extract_enum_labels(root)
    terminal_info = _extract_terminal_info(root, constants, fp_terminals)
    loops = _extract_loops(root)

    return BlockDiagram(
        nodes=nodes,
        constants=constants,
        wires=wires,
        fp_terminals=fp_terminals,
        enum_labels=enum_labels,
        terminal_info=terminal_info,
        loops=loops,
    )


def _extract_nodes(root: ET.Element) -> list[Node]:
    """Extract nodes from the block diagram."""
    nodes = []

    # Search everywhere in the document for operation node types
    for cls in OPERATION_NODE_CLASSES:
        for elem in root.findall(f".//*[@class='{cls}']"):
            uid = elem.get("uid")

            # Get name from direct label child (not nested)
            label = elem.find("label/textRec/text")
            name = label.text.strip('"') if label is not None and label.text else None

            # Get primitive info
            prim_idx_elem = elem.find("primIndex")
            prim_res_elem = elem.find("primResID")

            # Extract terminal types (inputs and outputs)
            input_types: list[str] = []
            output_types: list[str] = []
            for term in elem.findall(f".//termList/SL__arrayElement[@class='{TERMINAL_CLASS}']"):
                type_desc = term.find(".//typeDesc")
                obj_flags = term.find("objFlags")

                type_str = type_desc.text if type_desc is not None and type_desc.text else None
                if type_str:
                    flags = int(obj_flags.text) if obj_flags is not None and obj_flags.text else 0
                    # Bit 0 (isIndicator) = output, bit 0 clear = input
                    if flags & 0x1:
                        output_types.append(type_str)
                    else:
                        input_types.append(type_str)

            node = Node(
                uid=uid,
                node_type=cls,
                name=name,
                prim_index=int(prim_idx_elem.text) if prim_idx_elem is not None else None,
                prim_res_id=int(prim_res_elem.text) if prim_res_elem is not None else None,
                input_types=input_types,
                output_types=output_types,
            )
            nodes.append(node)

    return nodes


def _extract_constants(root: ET.Element) -> list[Constant]:
    """Extract constants from the block diagram."""
    constants = []

    for term in root.findall(f".//nodeList//SL__arrayElement[@class='{TERMINAL_CLASS}']"):
        dco = term.find(f"dco[@class='{CONSTANT_DCO_CLASS}']")
        if dco is None:
            continue

        uid = term.get("uid")
        type_desc = dco.find("typeDesc")
        const_val = dco.find("ConstValue")

        # Try to get label from nested ddo
        label_elem = dco.find(".//multiLabel/buf")
        label = label_elem.text if label_elem is not None else None

        # Also check for regular labels
        if label is None:
            label_elem = dco.find(".//label/textRec/text")
            label = label_elem.text.strip('"') if label_elem is not None and label_elem.text else None

        if const_val is not None:
            constants.append(Constant(
                uid=uid,
                type_desc=type_desc.text if type_desc is not None else "unknown",
                value=const_val.text,
                label=label,
            ))

    return constants


def _extract_wires(root: ET.Element) -> list[Wire]:
    """Extract wires (signals) from the block diagram.

    In LabVIEW, a single signal can connect one source to multiple destinations.
    We create separate Wire objects for each destination.
    """
    wires = []

    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        uid = sig.get("uid")
        terms = [t.get("uid") for t in sig.findall("termList/SL__arrayElement")]

        if len(terms) >= 2:
            # First terminal is the source, all others are destinations
            source = terms[0]
            for i, dest in enumerate(terms[1:]):
                wires.append(Wire(
                    uid=f"{uid}_{i}" if i > 0 else uid,
                    from_term=source,
                    to_term=dest,
                ))

    return wires


def _extract_loops(root: ET.Element) -> list[LoopStructure]:
    """Extract loop structures (while, for) with tunnel mappings.

    Loops in LabVIEW have:
    - Boundary terminals on the loop border
    - Tunnels that connect outer terminals to inner terminals
    - An inner diagram containing operations

    The tunnel mappings are found in the terminal's dco (data connection object):
    - dco class="lSR" (left shift register): input tunnel
    - dco class="rSR" (right shift register): output tunnel
    - dco class="lpTun" (loop tunnel): simple pass-through
    - The dco's termList contains [inner_uid, outer_uid]

    Args:
        root: XML root element

    Returns:
        List of LoopStructure with tunnel mappings
    """
    loops: list[LoopStructure] = []

    # Find all loop elements in zPlaneList
    for loop_class in LOOP_NODE_CLASSES:
        for loop_elem in root.findall(f".//*[@class='{loop_class}']"):
            loop_uid = loop_elem.get("uid")
            if not loop_uid:
                continue

            boundary_terminals: list[str] = []
            tunnels: list[TunnelMapping] = []
            inner_diagram_uid: str | None = None
            inner_node_uids: list[str] = []

            # Find boundary terminals in the loop's termList
            term_list_elem = loop_elem.find("termList")
            if term_list_elem is not None:
                for term_elem in term_list_elem.findall(
                    f"SL__arrayElement[@class='{TERMINAL_CLASS}']"
                ):
                    term_uid = term_elem.get("uid")
                    if term_uid:
                        boundary_terminals.append(term_uid)

                    # Check for tunnel dco inside this terminal
                    dco = term_elem.find("dco")
                    if dco is not None:
                        dco_class = dco.get("class", "")
                        if dco_class in TUNNEL_DCO_CLASSES:
                            # Extract termList to get inner/outer mapping
                            dco_term_list = dco.find("termList")
                            if dco_term_list is not None:
                                term_refs = [
                                    e.get("uid")
                                    for e in dco_term_list.findall("SL__arrayElement")
                                    if e.get("uid")
                                ]
                                # Format is [inner_uid, outer_uid]
                                if len(term_refs) >= 2:
                                    inner_uid = term_refs[0]
                                    outer_uid = term_refs[1]
                                    tunnels.append(TunnelMapping(
                                        outer_terminal_uid=outer_uid,
                                        inner_terminal_uid=inner_uid,
                                        tunnel_type=dco_class,
                                    ))

            # Find inner diagram
            diag_list = loop_elem.find("diagramList")
            if diag_list is not None:
                inner_diag = diag_list.find("SL__arrayElement[@class='diag']")
                if inner_diag is not None:
                    inner_diagram_uid = inner_diag.get("uid")

                    # Find operations inside the inner diagram
                    for node_list in inner_diag.findall(".//nodeList"):
                        for node_elem in node_list.findall("SL__arrayElement"):
                            node_uid = node_elem.get("uid")
                            if node_uid:
                                inner_node_uids.append(node_uid)

            # Find stop condition terminal for while loops (loopTestDCO class="lTst")
            stop_condition_uid: str | None = None
            loop_test_dco = loop_elem.find("loopTestDCO[@class='lTst']")
            if loop_test_dco is not None:
                # The termList inside contains the terminal that receives the stop boolean
                term_list = loop_test_dco.find("termList")
                if term_list is not None:
                    first_term = term_list.find("SL__arrayElement")
                    if first_term is not None:
                        stop_condition_uid = first_term.get("uid")

            loops.append(LoopStructure(
                uid=loop_uid,
                loop_type=loop_class,
                boundary_terminal_uids=boundary_terminals,
                tunnels=tunnels,
                inner_diagram_uid=inner_diagram_uid,
                inner_node_uids=inner_node_uids,
                stop_condition_terminal_uid=stop_condition_uid,
            ))

    return loops


def _extract_fp_terminals(root: ET.Element) -> list[FPTerminal]:
    """Extract front panel terminals (VI inputs and outputs) from the block diagram.

    In LabVIEW, fPTerm elements on the block diagram represent connections to
    front panel controls (inputs) and indicators (outputs).

    We determine input vs output by analyzing signal (wire) directions:
    - If wires flow TO the fPTerm, it's an output (indicator)
    - If wires flow FROM the fPTerm, it's an input (control)
    """
    # First, collect all fPTerm UIDs
    fp_term_uids = set()
    fp_term_data = {}

    for fp_term in root.findall(f".//*[@class='{FP_TERMINAL_CLASS}']"):
        uid = fp_term.get("uid")
        if not uid:
            continue
        fp_term_uids.add(uid)

        # Get the linked front panel DCO uid
        dco = fp_term.find("dco")
        fp_dco_uid = dco.get("uid") if dco is not None else None

        # Get the label/name
        label_elem = fp_term.find(".//label/textRec/text")
        name = label_elem.text.strip('"') if label_elem is not None and label_elem.text else None

        fp_term_data[uid] = {
            "fp_dco_uid": fp_dco_uid or "",
            "name": name,
            "is_indicator": False,  # Will be determined by wire analysis
        }

    # Analyze signals to determine input vs output
    # In signals, the first terminal is the source, others are destinations
    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        terms = [t.get("uid") for t in sig.findall("termList/SL__arrayElement")]
        if len(terms) >= 2:
            destinations = terms[1:]

            # If an fPTerm is a destination, it's an output (indicator)
            for dest in destinations:
                if dest in fp_term_uids:
                    fp_term_data[dest]["is_indicator"] = True

    # Build the result list
    terminals = []
    for uid, data in fp_term_data.items():
        terminals.append(FPTerminal(
            uid=uid,
            fp_dco_uid=data["fp_dco_uid"],
            name=data["name"],
            is_indicator=data["is_indicator"],
        ))

    return terminals


def parse_connector_pane(fp_xml_path: Path | str) -> ConnectorPane | None:
    """Parse the connector pane from a front panel XML file.

    The connector pane defines which front panel controls/indicators
    are exposed as VI terminals and their slot positions.

    Args:
        fp_xml_path: Path to the *_FPHb.xml file

    Returns:
        ConnectorPane with slot assignments, or None if not found
    """
    tree = ET.parse(fp_xml_path)
    root = tree.getroot()

    # Find the conPane element
    con_pane = root.find(".//conPane[@class='conPane']")
    if con_pane is None:
        return None

    # Get the pattern ID
    con_id_elem = con_pane.find("conId")
    pattern_id = int(con_id_elem.text) if con_id_elem is not None and con_id_elem.text else 0

    # Parse the slots (cons array)
    slots: list[ConnectorPaneSlot] = []
    cons = con_pane.find("cons")
    if cons is not None:
        current_index = 0
        for elem in cons.findall("SL__arrayElement[@class='ConpaneConnection']"):
            # Check for explicit index attribute (sparse array)
            index_attr = elem.get("index")
            if index_attr is not None:
                current_index = int(index_attr)

            # Get the connected fPDCO UID
            conn_dco = elem.find("ConnectionDCO")
            fp_dco_uid = conn_dco.get("uid") if conn_dco is not None else None

            slots.append(ConnectorPaneSlot(
                index=current_index,
                fp_dco_uid=fp_dco_uid,
            ))

            current_index += 1

    return ConnectorPane(pattern_id=pattern_id, slots=slots)


def parse_connector_pane_types(
    main_xml_path: Path | str,
    fp_conpane: ConnectorPane,
) -> dict[int, int]:
    """Get wiring rules for connected connector pane terminals.

    Finds the VI's connector pane Function TypeDesc by matching connected
    slot indices from the FPHb conpane, then extracts wiring rules.

    Wiring rule encoding in TypeDesc Flags bits 8-9:
    - 0 = Invalid Wire Rule (default/unset)
    - 1 = Required
    - 2 = Recommended
    - 3 = Optional
    (Value 4 = Dynamic Dispatch, may use additional bits)

    Args:
        main_xml_path: Path to the main .xml file (not BDHb/FPHb)
        fp_conpane: ConnectorPane from FPHb with connected slot indices

    Returns:
        Dict mapping slot index → wiring rule (0-3)
    """
    # Get connected slot indices from FP conpane
    connected_indices = {s.index for s in fp_conpane.slots if s.fp_dco_uid}
    if not connected_indices:
        return {}

    max_index = max(connected_indices)

    tree = ET.parse(main_xml_path)
    root = tree.getroot()

    # Find the Function TypeDesc that covers all connected indices
    # and has non-void flags at those positions
    for func_td in root.findall(".//TypeDesc[@Type='Function']"):
        children = func_td.findall("TypeDesc")
        if len(children) <= max_index:
            continue

        # Check if connected indices have non-void flags
        matches = all(
            children[i].get("Flags", "0x0000") != "0x0000"
            for i in connected_indices
        )
        if not matches:
            continue

        # Extract wiring rules for connected slots
        rules: dict[int, int] = {}
        for idx in connected_indices:
            flags_str = children[idx].get("Flags", "0x0000")
            try:
                flags = int(flags_str, 16)
            except ValueError:
                flags = 0
            rules[idx] = (flags >> 8) & 0x03

        return rules

    return {}


def parse_type_map(xml_path: Path | str) -> dict[int, str]:
    """Parse TypeID mappings from main XML comments.

    Looks for both basic and consolidated type mappings:
    - <!-- TypeID N: TypeName -->
    - <!-- Heap TypeID N = Consolidated TypeID M: TypeName -->

    Args:
        xml_path: Path to main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping TypeID -> type name (e.g., 37 -> "NumInt64")
    """
    import re
    type_map = {}

    with open(xml_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            # Match <!-- Heap TypeID N = Consolidated TypeID M: TypeName -->
            # These are more specific, so check first
            match = re.search(r'Heap TypeID\s+(\d+)\s*=\s*Consolidated TypeID\s+\d+:\s*(\w+)', line)
            if match:
                type_id = int(match.group(1))
                type_name = match.group(2)
                type_map[type_id] = type_name
                continue

            # Match <!-- TypeID N: TypeName -->
            match = re.search(r'<!--\s*TypeID\s+(\d+):\s*(\w+)', line)
            if match:
                type_id = int(match.group(1))
                type_name = match.group(2)
                # Only use basic mapping if we don't have a consolidated one
                if type_id not in type_map:
                    type_map[type_id] = type_name

    return type_map


def resolve_type(type_ref: str, type_map: dict[int, str]) -> str:
    """Resolve TypeID(N) reference to type name.

    Args:
        type_ref: String like "TypeID(37)"
        type_map: Mapping from TypeID -> type name

    Returns:
        Resolved type name or original string if not resolvable
    """
    import re
    match = re.match(r'TypeID\((\d+)\)', type_ref)
    if match:
        type_id = int(match.group(1))
        return type_map.get(type_id, type_ref)
    return type_ref


def parse_vi_metadata(xml_path: Path | str) -> dict[str, Any]:
    """Parse the main VI XML file for metadata and SubVI references.

    Args:
        xml_path: Path to the main .xml file (not BDHb)

    Returns:
        Dict with version info, SubVI names, type descriptors, library info, etc.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    metadata: dict[str, Any] = {}

    # Get VI name from LVSR section
    lvsr = root.find(".//LVSR/Section")
    if lvsr is not None:
        metadata["name"] = lvsr.get("Name", "unknown")

    # Get library name from LIBN section
    lib_elem = root.find(".//LIBN/Section/Library")
    if lib_elem is not None and lib_elem.text:
        metadata["library"] = lib_elem.text

    # Get qualified name from LIvi section
    # The LVIN element has Unk1 attribute with "Library.lvlib:VI.vi" format
    lvin = root.find(".//LIvi/Section/LVIN")
    if lvin is not None:
        qualified = lvin.get("Unk1")
        if qualified:
            metadata["qualified_name"] = qualified

    # Get SubVI references from LIvi section - these have the qualified names
    subvi_refs = []
    for vivi in root.findall(".//LIvi//VIVI/LinkSaveQualName/String"):
        if vivi.text:
            subvi_refs.append(vivi.text)
    metadata["subvi_refs"] = subvi_refs

    # Fall back to name if no qualified_name found
    if "qualified_name" not in metadata and "name" in metadata:
        metadata["qualified_name"] = metadata["name"]

    # Get help/documentation data (STRG, HLPP, HLPT sections)
    # STRG contains VI description
    strg = root.find(".//STRG/Section/String")
    if strg is not None and strg.text:
        metadata["description"] = strg.text

    # DSTM may contain description strings
    dstm = root.find(".//DSTM/Section/String")
    if dstm is not None and dstm.text:
        metadata["description"] = dstm.text

    # HLPT contains help tags
    hlpt = root.find(".//HLPT/Section/String")
    if hlpt is not None and hlpt.text:
        metadata["help_tag"] = hlpt.text

    # Parse typedef references from VICC elements
    metadata["typedef_refs"] = parse_typedef_refs(root)

    # Check if this is a polymorphic VI
    poly_info = parse_polymorphic_info(root)
    if poly_info["is_polymorphic"]:
        metadata["is_polymorphic"] = True
        metadata["poly_variants"] = poly_info["variants"]
        metadata["poly_selectors"] = poly_info["selectors"]

    return metadata


def parse_polymorphic_info(root: ET.Element) -> dict[str, Any]:
    """Parse polymorphic VI information from VCTP and CPST sections.

    A polymorphic VI has:
    - Type="PolyVI" in VCTP section
    - CPST section with variant selector strings
    - Multiple SubVI references (variants) in LIvi section

    Args:
        root: Root element of the main VI XML

    Returns:
        Dict with:
        - is_polymorphic: bool
        - variants: list of variant VI names
        - selectors: list of selector strings (e.g., "Scalar:String", "1D Array:All:Path")
    """
    result: dict[str, Any] = {
        "is_polymorphic": False,
        "variants": [],
        "selectors": [],
    }

    # Check for PolyVI type in VCTP section
    poly_type = root.find(".//VCTP//TypeDesc[@Type='PolyVI']")
    if poly_type is None:
        return result

    result["is_polymorphic"] = True

    # Extract selector strings from CPST section
    cpst_section = root.find(".//CPST/Section")
    if cpst_section is not None:
        for string_elem in cpst_section.findall("String"):
            if string_elem.text and string_elem.text.strip():
                result["selectors"].append(string_elem.text.strip())

    # Extract variant VI names from LIvi VIVI elements
    for vivi in root.findall(".//LIvi//VIVI/LinkSaveQualName/String"):
        if vivi.text:
            result["variants"].append(vivi.text)

    return result


@dataclass
class TypeDefRef:
    """A reference to a vilib TypeDef/custom control."""
    type_id: int
    name: str  # e.g., "System Directory Type.ctl"
    vilib_path: str  # e.g., "Utility/sysdir.llb"


def parse_typedef_refs(root: ET.Element) -> list[TypeDefRef]:
    """Parse VICC elements to find typedef references.

    VICC elements reference custom controls (.ctl files) from vilib.
    These define enums and other type definitions.

    Args:
        root: Root element of the main VI XML

    Returns:
        List of TypeDefRef with vilib path and control name
    """
    refs = []

    for vicc in root.findall(".//LIvi//VICC"):
        # Get TypeID
        type_desc = vicc.find("TypeDesc")
        if type_desc is None:
            continue
        type_id_str = type_desc.get("TypeID")
        if not type_id_str:
            continue
        type_id = int(type_id_str)

        # Get control name
        qual_name = vicc.find("LinkSaveQualName/String")
        if qual_name is None or not qual_name.text:
            continue
        name = qual_name.text

        # Get vilib path from LinkSavePathRef
        path_ref = vicc.find("LinkSavePathRef")
        if path_ref is None:
            continue

        # Check if it's from vilib
        path_parts = [s.text for s in path_ref.findall("String") if s.text]
        if not path_parts or path_parts[0] != "<vilib>":
            continue

        # Build vilib path (skip the <vilib> prefix)
        vilib_path = "/".join(path_parts[1:-1])  # Exclude control name at end

        refs.append(TypeDefRef(type_id=type_id, name=name, vilib_path=vilib_path))

    return refs


def load_enum_reference() -> dict:
    """Load the labview-enums.json reference file.

    Returns:
        Dict with typedef definitions, or empty dict if not found
    """
    import json

    # Find the data directory relative to this module
    data_dir = Path(__file__).parent.parent.parent / "data"
    enums_path = data_dir / "labview-enums.json"

    if not enums_path.exists():
        return {}

    with open(enums_path) as f:
        return json.load(f)


@dataclass
class ResolvedTypeDefValue:
    """A resolved typedef enum value with OS paths."""
    name: str
    description: str
    windows_path: str | None = None
    unix_path: str | None = None


def resolve_typedef_value(typedef_ref: TypeDefRef, value: int) -> ResolvedTypeDefValue | None:
    """Resolve a typedef enum value to its description and OS paths.

    Args:
        typedef_ref: The typedef reference (from parse_typedef_refs)
        value: The integer enum value

    Returns:
        ResolvedTypeDefValue with name, description, and OS paths, or None if not found
    """
    enums = load_enum_reference()
    typedefs = enums.get("typedefs", {})

    # Build lookup key
    key = f"{typedef_ref.vilib_path}:{typedef_ref.name}"

    typedef_info = typedefs.get(key)
    if not typedef_info:
        return None

    values = typedef_info.get("values", {})
    # JSON keys are strings, so convert
    value_info = values.get(str(value)) or values.get(value)
    if value_info:
        return ResolvedTypeDefValue(
            name=value_info.get("name", ""),
            description=value_info.get("description", ""),
            windows_path=value_info.get("windows"),
            unix_path=value_info.get("unix"),
        )

    return None


@dataclass
class DefaultValue:
    """A default value from the DFDS section."""
    type_id: int
    values: list[Any]  # Parsed values (bool, int, float, str, etc.)
    structure: str  # "Cluster", "Array", "scalar", etc.


def parse_dfds(xml_path: Path | str) -> dict[int, DefaultValue]:
    """Parse the DFDS (Default Fill of Data Space) section for default values.

    Args:
        xml_path: Path to the main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping TypeID to DefaultValue with parsed values
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    defaults: dict[int, DefaultValue] = {}

    for data_fill in root.findall(".//DFDS//DataFill"):
        type_id_str = data_fill.get("TypeID")
        if not type_id_str:
            continue
        type_id = int(type_id_str)

        values, structure = _parse_data_fill(data_fill)
        if values is not None:
            defaults[type_id] = DefaultValue(
                type_id=type_id,
                values=values,
                structure=structure,
            )

    return defaults


def _parse_data_fill(elem: ET.Element) -> tuple[list[Any] | None, str]:
    """Parse a DataFill element and extract values."""
    # Check for Cluster
    cluster = elem.find("Cluster") or elem.find("SpecialDSTMCluster/Cluster")
    if cluster is not None:
        values = []
        for child in cluster:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values, "Cluster"

    # Check for Array
    array = elem.find("Array") or elem.find("SpecialDSTMCluster/Array")
    if array is not None:
        dim = array.find("dim")
        dim_val = int(dim.text) if dim is not None and dim.text else 0
        values = []
        for child in array:
            if child.tag != "dim":
                val = _parse_value_element(child)
                if val is not None:
                    values.append(val)
        return values, f"Array[{dim_val}]"

    # Single value
    for child in elem:
        val = _parse_value_element(child)
        if val is not None:
            return [val], "scalar"

    return None, "unknown"


def _parse_value_element(elem: ET.Element) -> Any:
    """Parse a single value element (Boolean, I32, DBL, String, etc.)."""
    tag = elem.tag

    if tag == "Boolean":
        return elem.text == "1" if elem.text else False
    elif tag in ("I32", "I16", "I8", "U32", "U16", "U8"):
        return int(elem.text) if elem.text else 0
    elif tag in ("DBL", "SGL", "EXT"):
        return float(elem.text) if elem.text else 0.0
    elif tag == "String":
        return elem.text or ""
    elif tag == "Path":
        # Path elements may have nested String
        path_str = elem.find("String")
        return path_str.text if path_str is not None and path_str.text else ""
    elif tag == "Cluster":
        # Nested cluster
        values = []
        for child in elem:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values
    elif tag == "Array":
        values = []
        for child in elem:
            if child.tag != "dim":
                val = _parse_value_element(child)
                if val is not None:
                    values.append(val)
        return values
    elif tag == "RepeatedBlock":
        # Internal structure, parse children
        values = []
        for child in elem:
            val = _parse_value_element(child)
            if val is not None:
                values.append(val)
        return values
    elif tag == "Block":
        # Hex block - return as-is
        return elem.text or ""

    return None


def _extract_enum_labels(root: ET.Element) -> dict[str, list[str]]:
    """Extract enum/ring labels from the XML.

    Returns:
        Dict mapping UID to list of enum labels
    """
    enums: dict[str, list[str]] = {}
    for multi_label in root.findall(f".//*[@class='{MULTI_LABEL_CLASS}']"):
        buf = multi_label.find("buf")
        if buf is not None and buf.text:
            # Format: (count)"label1""label2"...
            text = buf.text
            labels = []
            i = 0
            # Skip the count prefix like "(13)"
            if text.startswith("("):
                i = text.find(")") + 1
            # Parse quoted strings
            while i < len(text):
                if text[i] == '"':
                    end = text.find('"', i + 1)
                    if end > i:
                        labels.append(text[i + 1:end])
                        i = end + 1
                    else:
                        break
                else:
                    i += 1
            if labels:
                # Find parent UID by walking up the tree
                # Since ElementTree doesn't have parent refs, search for uid in ancestors
                parent = multi_label
                while parent is not None:
                    uid = parent.get("uid")
                    if uid:
                        enums[uid] = labels
                        break
                    # ElementTree doesn't support parent navigation, so we store at multiLabel level
                    # The caller will need to match by proximity
                    break
    return enums


def _build_terminal_map(root: ET.Element, constants: list[Constant]) -> dict[str, str]:
    """Map terminal UIDs to their parent node/constant UID.

    Args:
        root: XML root element
        constants: List of parsed constants (to identify constant terminals)

    Returns:
        Dict mapping terminal UID to parent node UID
    """
    term_map: dict[str, str] = {}
    const_uids = {c.uid for c in constants}

    for elem in root.iter():
        elem_uid = elem.get("uid")
        elem_class = elem.get("class", "")

        if elem_uid:
            # For operation nodes, map their terminals
            if elem_class in OPERATION_NODE_CLASSES:
                xpath = f"./termList/SL__arrayElement[@class='{TERMINAL_CLASS}']"
                for term in elem.findall(xpath):
                    term_uid = term.get("uid")
                    if term_uid:
                        term_map[term_uid] = elem_uid

            # For constant terminals (the constant IS the terminal)
            if elem_class == TERMINAL_CLASS and elem_uid in const_uids:
                term_map[elem_uid] = elem_uid

            # For front panel terminals
            if elem_class == FP_TERMINAL_CLASS:
                term_map[elem_uid] = elem_uid

    return term_map


def _extract_terminal_info(
    root: ET.Element,
    constants: list[Constant],
    fp_terminals: list[FPTerminal],
) -> dict[str, TerminalInfo]:
    """Extract detailed terminal info for graph-native representation.

    Captures:
    - Terminal position (index) in parent's termList
    - Input vs output direction (from objFlags)
    - Type information

    Args:
        root: XML root element
        constants: List of parsed constants
        fp_terminals: List of front panel terminals

    Returns:
        Dict mapping terminal UID to TerminalInfo
    """
    terminal_info: dict[str, TerminalInfo] = {}
    const_uids = {c.uid for c in constants}
    fp_term_map = {fp.uid: fp for fp in fp_terminals}

    # Extract terminals from operation nodes (primitives, SubVIs)
    for elem in root.iter():
        elem_uid = elem.get("uid")
        elem_class = elem.get("class", "")

        if not elem_uid:
            continue

        # Terminal container nodes have termList with indexed terminals
        # This includes operations (prims, SubVIs) and shift register nodes
        if elem_class in TERMINAL_CONTAINER_CLASSES:
            term_list = elem.findall(f"./termList/SL__arrayElement[@class='{TERMINAL_CLASS}']")

            for list_position, term in enumerate(term_list):
                term_uid = term.get("uid")
                if not term_uid:
                    continue

                # Get nested dco element (contains parmIndex and sometimes objFlags)
                dco = term.find("dco")

                # Get parmIndex from dco if present, otherwise use list position
                # parmIndex is the actual parameter index from LabVIEW
                parm_index = list_position
                if dco is not None:
                    parm_index_elem = dco.find("parmIndex")
                    if parm_index_elem is not None and parm_index_elem.text:
                        parm_index = int(parm_index_elem.text)

                # Get objFlags to determine input vs output
                # Bit 0 (isIndicator) = output terminal (from pylabview LVparts.py OBJ_FLAGS)
                # Combine flags from both term element and dco element
                term_flags_elem = term.find("objFlags")
                term_flags = int(term_flags_elem.text) if term_flags_elem is not None and term_flags_elem.text else 0
                dco_flags = 0
                if dco is not None:
                    dco_flags_elem = dco.find("objFlags")
                    dco_flags = int(dco_flags_elem.text) if dco_flags_elem is not None and dco_flags_elem.text else 0

                # Bit 0 set = output (isIndicator), bit 0 clear = input
                combined_flags = term_flags | dco_flags
                is_output = bool(combined_flags & 0x1)

                # Get type from typeDesc
                type_desc_elem = term.find(".//typeDesc")
                type_id = type_desc_elem.text if type_desc_elem is not None else None

                terminal_info[term_uid] = TerminalInfo(
                    uid=term_uid,
                    parent_uid=elem_uid,
                    index=parm_index,
                    is_output=is_output,
                    type_id=type_id,
                )

    # Constants have a single output terminal (the constant itself)
    for const in constants:
        if const.uid not in terminal_info:
            terminal_info[const.uid] = TerminalInfo(
                uid=const.uid,
                parent_uid=const.uid,  # Constant is its own parent
                index=0,
                is_output=True,  # Constants output their value
                type_id=const.type_desc,
            )

    # Front panel terminals
    for fp_term in fp_terminals:
        if fp_term.uid not in terminal_info:
            terminal_info[fp_term.uid] = TerminalInfo(
                uid=fp_term.uid,
                parent_uid=fp_term.uid,  # FP terminal is its own parent for now
                index=0,
                is_output=not fp_term.is_indicator,  # Controls output to diagram, indicators receive
                type_id=None,  # Type comes from front panel XML
                name=fp_term.name,  # Use FP control/indicator name
            )

    return terminal_info


def parse_type_chain(xml_path: Path | str) -> dict:
    """Parse the complete type resolution chain from main XML.
    
    Builds mappings to resolve TypeID(N) to actual type info including:
    - Heap TypeID -> Consolidated TypeID
    - Consolidated TypeID -> FlatTypeID
    - FlatTypeID -> Type descriptor (name, underlying type, typedef name)
    
    Returns:
        Dict with 'heap_to_consolidated', 'consolidated_to_flat', 'flat_types'
    """
    import re
    
    heap_to_consolidated: dict[int, tuple[int, str]] = {}  # heap_id -> (consolidated_id, type_name)
    consolidated_to_flat: dict[int, int] = {}  # consolidated_id -> flat_id
    flat_types: dict[int, dict] = {}  # flat_id -> {type, label, typedef_name, ...}
    
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    # Parse Heap TypeID comments
    # Format: <!-- Heap TypeID N = Consolidated TypeID M: TypeName -->
    xml_text = Path(xml_path).read_text(encoding='utf-8', errors='replace')
    for match in re.finditer(r'Heap TypeID\s+(\d+)\s*=\s*Consolidated TypeID\s+(\d+):\s*(\w+)', xml_text):
        heap_id = int(match.group(1))
        consolidated_id = int(match.group(2))
        type_name = match.group(3)
        heap_to_consolidated[heap_id] = (consolidated_id, type_name)
    
    # Parse VCTP section for Consolidated -> FlatTypeID mapping
    for type_desc in root.findall(".//VCTP//TopLevel/TypeDesc"):
        index = type_desc.get("Index")
        flat_id = type_desc.get("FlatTypeID")
        if index and flat_id:
            consolidated_to_flat[int(index)] = int(flat_id)
    
    # Parse FlatTypeID comments and TypeDesc elements
    for match in re.finditer(r'FlatTypeID (\d+):\s*([^\n<]+)', xml_text):
        flat_id = int(match.group(1))
        description = match.group(2).strip()
        flat_types[flat_id] = {"description": description}
    
    # Find actual TypeDef definitions with labels
    for type_desc in root.findall(".//VCTP//TypeDesc[@Type='TypeDef']"):
        # Find the Label child with typedef name
        label = type_desc.find("Label")
        if label is not None:
            typedef_name = label.get("Text", "")
            # Find parent index to get flat_id
            # This is tricky - we need to match by position
            # For now, store by typedef name
            flat_types[0] = flat_types.get(0, {})
            flat_types[0]["typedef_name"] = typedef_name
            flat_types[0]["type"] = "TypeDef"
            # Get nested type
            nested = type_desc.find("TypeDesc[@Nested='True']")
            if nested is not None:
                flat_types[0]["underlying_type"] = nested.get("Type", "")
                flat_types[0]["label"] = nested.get("Label", "")
    
    return {
        "heap_to_consolidated": heap_to_consolidated,
        "consolidated_to_flat": consolidated_to_flat,
        "flat_types": flat_types,
    }


@dataclass
class SubVIPathRef:
    """A SubVI reference with path hints from the XML."""
    name: str  # VI name, e.g., "Create Dir if Non-Existant__ogtk.vi"
    path_tokens: list[str]  # Path components, e.g., ["<userlib>", "_OpenG.lib", "file", "file.llb"]
    is_vilib: bool = False  # True if from <vilib>
    is_userlib: bool = False  # True if from <userlib>

    def get_relative_path(self) -> str:
        """Get the relative path under vilib/userlib.

        Returns path like "_OpenG.lib/file/file.llb/Create Dir if Non-Existant__ogtk.vi"
        """
        # Skip the first token (<vilib> or <userlib>)
        if self.path_tokens and self.path_tokens[0] in ("<vilib>", "<userlib>"):
            return "/".join(self.path_tokens[1:])
        return "/".join(self.path_tokens)


def parse_subvi_paths(xml_path: Path | str) -> list[SubVIPathRef]:
    """Parse SubVI path references from the main VI XML.

    The LIvi section contains VIVI elements with LinkSavePathRef that
    specify where to find SubVIs relative to vilib or userlib.

    Args:
        xml_path: Path to the main .xml file (not BDHb)

    Returns:
        List of SubVIPathRef with path hints for each SubVI
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    refs: list[SubVIPathRef] = []

    # Find VIVI elements (SubVI references)
    for vivi in root.findall(".//LIvi//VIVI"):
        # Get SubVI name from LinkSaveQualName
        qual_name = vivi.find("LinkSaveQualName/String")
        if qual_name is None or not qual_name.text:
            continue
        name = qual_name.text

        # Get path from LinkSavePathRef
        path_ref = vivi.find("LinkSavePathRef")
        if path_ref is None:
            continue

        # Extract path components
        path_parts = [s.text for s in path_ref.findall("String") if s.text]
        if not path_parts:
            continue

        # Determine path type
        is_vilib = len(path_parts) > 0 and path_parts[0] == "<vilib>"
        is_userlib = len(path_parts) > 0 and path_parts[0] == "<userlib>"

        refs.append(SubVIPathRef(
            name=name,
            path_tokens=path_parts,
            is_vilib=is_vilib,
            is_userlib=is_userlib,
        ))

    return refs


def resolve_type_to_typedef(type_ref: str, type_chain: dict) -> str | None:
    """Resolve a TypeID reference to its typedef name if applicable.
    
    Args:
        type_ref: String like "TypeID(36)"
        type_chain: Result from parse_type_chain()
        
    Returns:
        TypeDef name like "System Directory Type.ctl" or None
    """
    import re
    match = re.match(r'TypeID\((\d+)\)', type_ref)
    if not match:
        return None
    
    heap_id = int(match.group(1))
    
    # Step 1: Heap -> Consolidated
    if heap_id not in type_chain["heap_to_consolidated"]:
        return None
    consolidated_id, type_name = type_chain["heap_to_consolidated"][heap_id]
    
    if type_name != "TypeDef":
        return None  # Not a typedef
    
    # Step 2: Consolidated -> Flat
    flat_id = type_chain["consolidated_to_flat"].get(consolidated_id)
    if flat_id is None:
        return None
    
    # Step 3: Flat -> TypeDef name
    flat_info = type_chain["flat_types"].get(flat_id, {})
    return flat_info.get("typedef_name")
