"""Parse pylabview XML output into a structured graph representation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    CONSTANT_DCO_CLASS,
    FP_TERMINAL_CLASS,
    MULTI_LABEL_CLASS,
    OPERATION_NODE_CLASSES,
    TERMINAL_CLASS,
    TERMINAL_OUTPUT_FLAG,
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
class BlockDiagram:
    """Parsed block diagram representation."""
    nodes: list[Node]
    constants: list[Constant]
    wires: list[Wire]
    fp_terminals: list[FPTerminal] = field(default_factory=list)
    enum_labels: dict[str, list[str]] = field(default_factory=dict)  # uid -> labels
    term_to_parent: dict[str, str] = field(default_factory=dict)  # terminal uid -> parent uid

    def get_node(self, uid: str) -> Node | None:
        """Get a node by UID."""
        for node in self.nodes:
            if node.uid == uid:
                return node
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
    term_to_parent = _build_terminal_map(root, constants)

    return BlockDiagram(
        nodes=nodes,
        constants=constants,
        wires=wires,
        fp_terminals=fp_terminals,
        enum_labels=enum_labels,
        term_to_parent=term_to_parent,
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
                    if flags & TERMINAL_OUTPUT_FLAG:
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
        Dict with version info, SubVI names, type descriptors, etc.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    metadata: dict[str, Any] = {}

    # Get VI name from LVSR section
    lvsr = root.find(".//LVSR/Section")
    if lvsr is not None:
        metadata["name"] = lvsr.get("Name", "unknown")

    # Get SubVI references from LIds section
    subvi_refs = []
    for dsds in root.findall(".//LIds//DSDS"):
        name_elem = dsds.find("LinkSaveQualName/String")
        if name_elem is not None and name_elem.text:
            subvi_refs.append(name_elem.text)
    metadata["subvi_refs"] = subvi_refs

    return metadata


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
