"""Loop structure (while, for) parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vipy.constants import LOOP_NODE_CLASSES, TERMINAL_CLASS, TUNNEL_DCO_CLASSES
from vipy.graph_types import Tunnel

from ..models import LoopStructure
from .base import extract_tunnel_mapping


def extract_loops(root: ET.Element) -> list[LoopStructure]:
    """Extract loop structures (while, for) with tunnel mappings.

    Loops in LabVIEW have:
    - Boundary terminals on the loop border
    - Tunnels that connect outer terminals to inner terminals
    - An inner diagram containing operations

    The tunnel mappings are found in the terminal's dco:
    - dco class="lSR" (left shift register): input tunnel
    - dco class="rSR" (right shift register): output tunnel
    - dco class="lpTun" (loop tunnel): simple pass-through
    - dco class="lMax": accumulator output
    - The dco's termList contains [inner_uid, outer_uid]

    Args:
        root: XML root element

    Returns:
        List of LoopStructure with tunnel mappings
    """
    loops: list[LoopStructure] = []

    for loop_class in LOOP_NODE_CLASSES:
        for loop_elem in root.findall(f".//*[@class='{loop_class}']"):
            loop_uid = loop_elem.get("uid")
            if not loop_uid:
                continue

            boundary_terminals: list[str] = []
            tunnels: list[Tunnel] = []
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
                            tunnel = extract_tunnel_mapping(dco, dco_class)
                            if tunnel:
                                tunnels.append(tunnel)

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

            # caseSel tunnels are extracted by the case parser (case.py),
            # not the loop parser — they belong to the case structure.

            # Pair shift registers (lSR <-> rSR)
            _pair_shift_registers(tunnels)

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


def _pair_shift_registers(tunnels: list[Tunnel]) -> None:
    """Pair lSR and rSR tunnels that belong together.

    Shift registers in LabVIEW come in pairs:
    - lSR (left) receives initial value and provides value to loop body
    - rSR (right) receives updated value from loop body

    We pair them by matching inner terminal UIDs that appear to be related.
    In practice, the pairing is determined by position in the termList.

    Args:
        tunnels: List of tunnel mappings to modify in place
    """
    lsr_tunnels = [t for t in tunnels if t.tunnel_type == "lSR"]
    rsr_tunnels = [t for t in tunnels if t.tunnel_type == "rSR"]

    # Simple pairing by order (first lSR pairs with first rSR)
    for i, lsr in enumerate(lsr_tunnels):
        if i < len(rsr_tunnels):
            rsr = rsr_tunnels[i]
            lsr.paired_terminal_uid = rsr.outer_terminal_uid
            rsr.paired_terminal_uid = lsr.outer_terminal_uid
