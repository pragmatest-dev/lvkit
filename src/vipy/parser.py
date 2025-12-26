"""Parse pylabview XML output into a structured graph representation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    return BlockDiagram(
        nodes=nodes,
        constants=constants,
        wires=wires,
        fp_terminals=fp_terminals,
    )


def _extract_nodes(root: ET.Element) -> list[Node]:
    """Extract nodes from the block diagram."""
    nodes = []

    # Node types we care about
    node_classes = {
        "prim",      # Primitive functions
        "iUse",      # SubVI calls
        "whileLoop", # While loop
        "forLoop",   # For loop
        "select",    # Case/select structure
        "seq",       # Sequence structure
        "caseStruct",# Case structure
        "eventStruct", # Event structure
        "propNode",  # Property node
    }

    # Search everywhere in the document for these node types
    for cls in node_classes:
        for elem in root.findall(f".//*[@class='{cls}']"):
            uid = elem.get("uid")

            # Get name from direct label child (not nested)
            label = elem.find("label/textRec/text")
            name = label.text.strip('"') if label is not None and label.text else None

            # If no direct label, structure nodes don't have names
            if name is None and cls in ("whileLoop", "forLoop", "select", "seq", "caseStruct", "eventStruct"):
                name = None  # These are structural, no name needed

            # Get primitive info
            prim_idx_elem = elem.find("primIndex")
            prim_res_elem = elem.find("primResID")

            node = Node(
                uid=uid,
                node_type=cls,
                name=name,
                prim_index=int(prim_idx_elem.text) if prim_idx_elem is not None else None,
                prim_res_id=int(prim_res_elem.text) if prim_res_elem is not None else None,
            )
            nodes.append(node)

    return nodes


def _extract_constants(root: ET.Element) -> list[Constant]:
    """Extract constants from the block diagram."""
    constants = []

    for term in root.findall(".//nodeList//SL__arrayElement[@class='term']"):
        dco = term.find("dco[@class='bDConstDCO']")
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

    for fp_term in root.findall(".//*[@class='fPTerm']"):
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
            source = terms[0]
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
