"""Front panel parsing - connector pane, controls, indicators."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from lvkit.models import LVType
from lvkit.parser.utils import clean_labview_string

from .conp_types import conp_sidecar_path, decode_conp_terminals
from .constants import FP_TERMINAL_CLASS
from .flags import get_wiring_rule
from .fp_heap_type import reconstruct_dco_lvtype
from .models import (
    ParsedConnectorPane,
    ParsedConnectorPaneSlot,
    ParsedFPTerminal,
    ParsedType,
)
from .type_resolution import resolve_type_rich


def _lvtype_to_parsed(lv_type: LVType) -> ParsedType:
    """Convert LVType to ParsedType for parser output.

    Parser outputs ParsedType (clean, no external resolution).
    Graph layer enriches to LVType with values/fields from vilib_resolver.
    """
    return ParsedType(
        kind=lv_type.kind,
        type_name=lv_type.underlying_type or "unknown",
        typedef_path=lv_type.typedef_path,
        typedef_name=lv_type.typedef_name,
        ref_type=lv_type.ref_type,
        classname=lv_type.classname,
        fields=lv_type.fields,
        enum_values=lv_type.values,
        element_type=(
            _lvtype_to_parsed(lv_type.element_type)
            if lv_type.element_type else None
        ),
        dimensions=lv_type.dimensions,
    )


# Bit 0 of an fPDCO's ``objFlags`` is LabVIEW's control/indicator designation:
# set => indicator (VI output), clear => control (VI input). It is stored on the
# node itself, so it is AUTHORITATIVE even for an UNWIRED terminal — unlike
# inferring direction from wire connectivity, which cannot classify a terminal
# with no wires (e.g. an error-out indicator left unconnected).
_FP_INDICATOR_FLAG = 0x1


@dataclass(frozen=True)
class FpDcoInfo:
    """The stored attributes of one front-panel DCO node, read straight off the
    node: its type descriptor and its control/indicator designation.
    ``is_indicator`` is None only when the node carries no ``objFlags``.

    ``heap_type`` is the type reconstructed from the control's FP-heap subtree —
    populated ONLY for structured controls (cluster/enum/array), as a clean-room
    fallback for pre-LV9 VIs whose VCTP is absent (see ``fp_heap_type``). None
    otherwise, so scalars stay on the ``control_type`` path."""

    type_desc: str | None = None
    is_indicator: bool | None = None
    heap_type: LVType | None = None


def extract_fp_dco_info(fp_xml_path: Path | str) -> dict[str, FpDcoInfo]:
    """Read each front-panel DCO node's stored attributes in a single pass,
    keyed by DCO uid: its ``typeDesc`` (the actual LabVIEW type) and its
    control/indicator flag (``objFlags`` bit 0).
    """
    root = ET.parse(fp_xml_path).getroot()

    info: dict[str, FpDcoInfo] = {}
    for dco in root.findall(".//*[@class='fPDCO']"):
        uid = dco.get("uid")
        if not uid:
            continue
        type_desc_elem = dco.find("typeDesc")
        type_desc = (
            type_desc_elem.text
            if type_desc_elem is not None and type_desc_elem.text
            else None
        )
        is_indicator: bool | None = None
        obj_flags = dco.find("objFlags")
        if obj_flags is not None and obj_flags.text:
            text = obj_flags.text.strip()
            if text.lstrip("-").isdigit():
                is_indicator = bool(int(text) & _FP_INDICATOR_FLAG)
        # Reconstruct the type from the control's heap subtree, but keep only
        # STRUCTURED results — scalars are handled faithfully by the existing
        # control_type path, and a heap "Numeric" would drop a control_type's
        # more specific I32/U16/… on the floor.
        heap_type = reconstruct_dco_lvtype(dco)
        if heap_type is not None and heap_type.kind not in (
            "cluster", "enum", "array"
        ):
            heap_type = None
        info[uid] = FpDcoInfo(
            type_desc=type_desc,
            is_indicator=is_indicator,
            heap_type=heap_type,
        )

    return info


def extract_fp_terminals(
    root: ET.Element,
    fp_xml_path: Path | str | None = None,
    type_map: dict[int, LVType] | None = None,
) -> list[ParsedFPTerminal]:
    """Extract front panel terminals (VI inputs and outputs) from block diagram.

    In LabVIEW, fPTerm elements on the block diagram represent connections to
    front panel controls (inputs) and indicators (outputs).

    We determine input vs output by analyzing signal (wire) directions:
    - If wires flow TO the fPTerm, it's an output (indicator)
    - If wires flow FROM the fPTerm, it's an input (control)

    Args:
        root: XML root element (BD XML)
        fp_xml_path: Optional path to FP XML for extracting typeDesc from DCOs
        type_map: Optional type map for resolving TypeID references to LVType

    Returns:
        List of ParsedFPTerminal with resolved types
    """
    # Read each DCO node's stored attributes (type + control/indicator flag).
    dco_info: dict[str, FpDcoInfo] = {}
    if fp_xml_path:
        dco_info = extract_fp_dco_info(fp_xml_path)

    # First, collect all fPTerm UIDs
    fp_term_uids = set()
    fp_term_data = {}

    for fp_term in root.findall(f".//*[@class='{FP_TERMINAL_CLASS}']"):
        uid = fp_term.get("uid")
        if not uid:
            continue
        fp_term_uids.add(uid)

        dco = fp_term.find("dco")
        fp_dco_uid = dco.get("uid") if dco is not None else None

        label_elem = fp_term.find(".//label/textRec/text")
        name = (
            clean_labview_string(label_elem.text)
            if label_elem is not None and label_elem.text
            else None
        )

        # The DCO node carries both the type and the control/indicator flag.
        info = dco_info.get(fp_dco_uid) if fp_dco_uid else None

        fp_term_data[uid] = {
            "fp_dco_uid": fp_dco_uid or "",
            "name": name,
            # Authoritative flag straight off the node: True/False when stored,
            # None when the node carried no objFlags (~8% of DCOs) — those fall
            # back to the wire-direction inference below.
            "is_indicator": info.is_indicator if info else None,
            "type_desc": info.type_desc if info else None,
            "heap_type": info.heap_type if info else None,
        }

    # Fallback for terminals whose DCO node stored no flag: a terminal that
    # RECEIVES a wire is an indicator (output). Only fills a None — it never
    # overrides a stored flag.
    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        terms = [t.get("uid") for t in sig.findall("termList/SL__arrayElement")]
        if len(terms) >= 2:
            destinations = terms[1:]
            for dest in destinations:
                if dest in fp_term_uids and fp_term_data[dest]["is_indicator"] is None:
                    fp_term_data[dest]["is_indicator"] = True

    # Build the result list with resolved types
    terminals = []
    for uid, data in fp_term_data.items():
        # Resolve TypeID string to ParsedType
        parsed_type = None
        type_desc_str = data["type_desc"]
        if type_desc_str and type_map:
            lv_type = resolve_type_rich(type_desc_str, type_map)
            # A bare ``TypeID(N)`` underlying_type means the id wasn't in the
            # map (no / partial VCTP) — a passthrough, not a real resolution.
            if not (lv_type.underlying_type or "").startswith("TypeID"):
                parsed_type = _lvtype_to_parsed(lv_type)
        # Clean-room fallback for VIs without a VCTP (pre-LV9): use the type
        # reconstructed from the control's FP-heap subtree. Only fires when the
        # VCTP path produced nothing, so LV9+ resolution is untouched.
        if parsed_type is None and data["heap_type"] is not None:
            parsed_type = _lvtype_to_parsed(data["heap_type"])

        # None here means no stored flag AND no incoming wire => a control.
        is_indicator = bool(data["is_indicator"])

        terminals.append(ParsedFPTerminal(
            uid=uid,
            fp_dco_uid=data["fp_dco_uid"],
            name=data["name"],
            is_indicator=is_indicator,
            parsed_type=parsed_type,
        ))

    if fp_xml_path:
        _apply_conp_types(terminals, fp_xml_path)
    return terminals


def _apply_conp_types(
    terminals: list[ParsedFPTerminal], fp_xml_path: Path | str,
) -> None:
    """Overlay pre-LV9 CONP types onto the connector-pane terminals.

    CONP is the AUTHORITATIVE, fully-named type source for a pre-LV9 VI's
    interface (field names, class names, enum labels — see ``conp_types``), so it
    OVERRIDES the structure-only FP-heap fallback the loop above set. It never
    fights VCTP: CONP is non-empty ONLY for pre-LV9 VIs, which have no VCTP at
    all, so a terminal already resolved from VCTP (LV9+) is never reached here
    (LV9+ CONP is an empty stub -> ``decode_conp_terminals`` returns ``[]``).
    Correlated by connector-pane SLOT: CONP terminal at slot S -> the conpane
    slot with the same index -> its ``fp_dco_uid`` -> the matching terminal.
    """
    fp_path = Path(fp_xml_path)
    conp_bin = conp_sidecar_path(fp_path)
    if not conp_bin.exists():
        return
    conp_terms = decode_conp_terminals(conp_bin.read_bytes())
    if not conp_terms:
        return
    cpane = parse_connector_pane(fp_path)
    if cpane is None:
        return
    slot_to_dco = {
        s.index: s.fp_dco_uid for s in cpane.slots if s.fp_dco_uid
    }
    by_dco = {t.fp_dco_uid: t for t in terminals}
    for ct in conp_terms:
        if ct.lv_type is None:
            continue
        dco = slot_to_dco.get(ct.slot)
        term = by_dco.get(dco) if dco else None
        if term is None:
            continue
        term.parsed_type = _lvtype_to_parsed(ct.lv_type)


def parse_connector_pane(fp_xml_path: Path | str) -> ParsedConnectorPane | None:
    """Parse the connector pane from a front panel XML file.

    The connector pane defines which front panel controls/indicators
    are exposed as VI terminals and their slot positions.

    Args:
        fp_xml_path: Path to the *_FPHb.xml file

    Returns:
        ParsedConnectorPane with slot assignments, or None if not found
    """
    tree = ET.parse(fp_xml_path)
    root = tree.getroot()

    con_pane = root.find(".//conPane[@class='conPane']")
    if con_pane is None:
        return None

    con_id_elem = con_pane.find("conId")
    pattern_id = (
        int(con_id_elem.text) if con_id_elem is not None and con_id_elem.text else 0
    )

    slots: list[ParsedConnectorPaneSlot] = []
    cons = con_pane.find("cons")
    if cons is not None:
        current_index = 0
        for elem in cons.findall("SL__arrayElement[@class='ConpaneConnection']"):
            index_attr = elem.get("index")
            if index_attr is not None:
                current_index = int(index_attr)

            conn_dco = elem.find("ConnectionDCO")
            fp_dco_uid = conn_dco.get("uid") if conn_dco is not None else None

            slots.append(ParsedConnectorPaneSlot(
                index=current_index,
                fp_dco_uid=fp_dco_uid,
            ))

            current_index += 1

    return ParsedConnectorPane(pattern_id=pattern_id, slots=slots)


def parse_connector_pane_types(
    main_xml_path: Path | str,
    fp_conpane: ParsedConnectorPane,
) -> dict[int, int]:
    """Get wiring rules for connected connector pane terminals.

    Finds the VI's connector pane Function TypeDesc by matching connected
    slot indices from the FPHb conpane, then extracts wiring rules.

    Wiring rule encoding in TypeDesc Flags bits 8-9:
    - 0 = Invalid Wire Rule
    - 1 = Required
    - 2 = Recommended
    - 3 = Optional
    - 4 = Dynamic Dispatch

    Args:
        main_xml_path: Path to the main .xml file (not BDHb/FPHb)
        fp_conpane: ParsedConnectorPane from FPHb with connected slot indices

    Returns:
        Dict mapping slot index -> wiring rule (0-4)
    """
    connected_indices = {s.index for s in fp_conpane.slots if s.fp_dco_uid}
    if not connected_indices:
        return {}

    max_index = max(connected_indices)

    tree = ET.parse(main_xml_path)
    root = tree.getroot()

    for func_td in root.findall(".//TypeDesc[@Type='Function']"):
        children = func_td.findall("TypeDesc")
        if len(children) <= max_index:
            continue

        matches = all(
            children[i].get("Flags", "0x0000") != "0x0000"
            for i in connected_indices
        )
        if not matches:
            continue

        rules: dict[int, int] = {}
        for idx in connected_indices:
            flags_str = children[idx].get("Flags", "0x0000")
            try:
                flags = int(flags_str, 16)
            except ValueError:
                flags = 0
            rules[idx] = get_wiring_rule(flags)

        return rules

    return {}


def _flat_type_table(root: ET.Element) -> list[ET.Element]:
    """The VCTP's flat type list, in document order == FlatTypeID (every
    reference to a "flat" type -- from a Cluster's own field list, a
    Function's own parameter list, etc. -- is a position index into this
    same list). This is every direct child of ``VCTP/Section`` EXCEPT the
    trailing ``<TopLevel>`` consolidated-id index, which isn't a type."""
    section = root.find(".//VCTP/Section")
    if section is None:
        return []
    return [child for child in section if child.tag != "TopLevel"]


def _consolidated_to_flat(root: ET.Element) -> dict[int, int]:
    """VCTP's own TopLevel table: Consolidated TypeID (the numbering CONP,
    CPC2, and BD/FP heap ``typeDesc`` references use) -> FlatTypeID
    (position in ``_flat_type_table``)."""
    mapping: dict[int, int] = {}
    for td in root.findall(".//VCTP//TopLevel/TypeDesc"):
        index = td.get("Index")
        flat_id = td.get("FlatTypeID")
        if index is not None and flat_id is not None:
            mapping[int(index)] = int(flat_id)
    return mapping


def _resolve_own_connector_pane_function(root: ET.Element) -> ET.Element | None:
    """The VI's OWN connector-pane Function type descriptor, resolved
    authoritatively via the CPC2 ("Connector Pane Content Type v2", newer
    saves) or CONP ("Connector Pane Type Map", older saves) section -- never
    by scanning for a Function-shaped TypeDesc that merely happens to match
    the wired slot count/pattern. A VI's VCTP lists EVERY type used anywhere
    in the VI, including the parameter types of other VIs it calls, and more
    than one of those can be a same-shaped ``Type="Function"`` entry
    (verified on the JKI-VI-Tester corpus this was written against: one VI
    had 4 distinct Function TypeDescs in its VCTP, only one of which was its
    own connector pane).

    CONP/CPC2 store a Consolidated TypeID directly -- one hop through VCTP's
    own TopLevel table to the FlatTypeID -- unlike BD/FP heap ``typeDesc``
    references, which are Heap TypeIDs needing an extra Heap->Consolidated
    hop first (see the DTHP section's comments). Confirmed empirically: for
    every VI checked, resolving CONP/CPC2 this way lands on a Function
    descriptor whose wired slots (non-``Void`` children) line up exactly
    with the front panel's own connector-pane wiring (FPHb ``conPane``);
    going through the Heap->Consolidated chain does not.
    """
    pointer = root.find(".//CPC2/Section/TypeDesc")
    if pointer is None:
        pointer = root.find(".//CONP/Section/TypeDesc")
    consolidated_id = pointer.get("TypeID") if pointer is not None else None
    if consolidated_id is None:
        return None

    flat_id = _consolidated_to_flat(root).get(int(consolidated_id))
    if flat_id is None:
        return None

    flats = _flat_type_table(root)
    if not (0 <= flat_id < len(flats)):
        return None

    func = flats[flat_id]
    return func if func.get("Type") == "Function" else None


def parse_connector_pane_labels(main_xml_path: Path | str) -> dict[int, str]:
    """Best-effort recovery of a connector-pane terminal's name from the
    VI's flat type table (VCTP), for when the front-panel object's own
    label (partID=16 in the FP heap) is empty or absent -- see
    docs/_internal/design/error-indicator-handoff.md.

    LabVIEW's VCTP ("VI Consolidated Data Types") independently records a
    ``Label=`` attribute on a type descriptor when that type was named (a
    cluster's own name, e.g.); that copy is a second, separate encoding of
    the name from the FP heap's own partID=16 label, and can survive even
    when the FP heap copy doesn't (confirmed on VITester_Item_NotifyChanged
    .vi's un-stripped sibling copy, where the connector pane's flat type
    carries ``Label="error out"``/``"error in"`` independently of the FP
    object's own label). It can ALSO be absent in the same copy where the FP
    label is gone (confirmed on the corpus this was written against) --
    this is a best-effort second source, not a guaranteed recovery.

    Resolves the VI's OWN connector-pane Function type (see
    ``_resolve_own_connector_pane_function`` -- never a heuristic slot-count
    match), then reads each slot's referenced type's ``Label`` directly. A
    slot whose referenced type has no ``Label`` is simply absent from the
    result. Never raises -- a missing/malformed resource section just means
    an empty result.

    Args:
        main_xml_path: Path to the main .xml file (not BDHb/FPHb)

    Returns:
        Dict mapping connector-pane slot index -> label, for slots whose
        referenced type carries a non-empty Label.
    """
    try:
        root = ET.parse(main_xml_path).getroot()
    except (ET.ParseError, OSError):
        return {}

    func = _resolve_own_connector_pane_function(root)
    if func is None:
        return {}

    flats = _flat_type_table(root)
    labels: dict[int, str] = {}
    for slot_index, child in enumerate(func.findall("TypeDesc")):
        type_id = child.get("TypeID")
        if type_id is None:
            continue
        flat_id = int(type_id)
        if not (0 <= flat_id < len(flats)):
            continue
        label = flats[flat_id].get("Label")
        if label:
            labels[slot_index] = label
    return labels
