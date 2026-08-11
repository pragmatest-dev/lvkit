"""Loop structure (while, for) parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import Tunnel

from ..constants import (
    LOOP_NODE_CLASSES,
    TERMINAL_CLASS,
    TUNNEL_CLASS_LEFT_SR,
    TUNNEL_CLASS_RIGHT_SR,
    TUNNEL_DCO_CLASSES,
)
from ..flags import is_inverted_terminal
from ..models import ParsedLoopStructure
from ..utils import safe_int, safe_text
from .base import extract_tunnel_mapping


def _wired_terminal_uids(root: ET.Element) -> set[str]:
    """Every terminal uid that appears as a ``<signal>`` endpoint anywhere on
    this VI's block diagram -- i.e. carries an external wire.

    Used to detect an INITIALIZED shift register: an lSR tunnel's outer
    terminal wired from outside the loop (a value feeding the register
    before the loop starts) vs. an uninitialized one (no such wire -- the
    register starts at its data type's default). Terminal uids are unique
    within a VI's BD, so a whole-document scan (rather than tracking the
    loop's specific enclosing diagram) is sufficient and simpler.
    """
    uids: set[str] = set()
    for elem in root.iter("SL__arrayElement"):
        if elem.get("class") != "signal":
            continue
        term_list = elem.find("termList")
        if term_list is None:
            continue
        for e in term_list.findall("SL__arrayElement"):
            u = e.get("uid")
            if u:
                uids.add(u)
    return uids


def _parse_hex_int(text: str | None) -> int | None:
    """Parse a 2-digit hex byte string (e.g. ``ParForNumStaticWorkers``'s
    ``"08"`` -> 8). None when absent, unparseable, or 0 (LabVIEW's "not
    configured" sentinel -- see LoopOperation.parallel_static_workers)."""
    if not text:
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return value or None


def _annotate_sr_tunnels(
    tunnels: list[Tunnel],
    dco_class: str,
    dco: ET.Element,
    wired_uids: set[str],
) -> None:
    """Populate the structural (no-bits) shift-register fields on freshly
    extracted lSR/rSR tunnels in place -- see Tunnel.sr_initialized /
    Tunnel.sr_stack_depth. No-op for any other dco_class."""
    if dco_class == TUNNEL_CLASS_LEFT_SR:
        for t in tunnels:
            t.sr_initialized = t.outer_terminal_uid in wired_uids
    elif dco_class == TUNNEL_CLASS_RIGHT_SR:
        lsr_list = dco.find("lsrDCOList")
        depth = (
            len(lsr_list.findall("SL__arrayElement"))
            if lsr_list is not None else None
        )
        for t in tunnels:
            t.sr_stack_depth = depth


def extract_loops(root: ET.Element) -> list[ParsedLoopStructure]:
    """Extract loop structures (while, for) with tunnel mappings.

    Loops in LabVIEW have:
    - Boundary terminals on the loop border
    - Tunnels that connect outer terminals to inner terminals
    - An inner diagram containing operations

    The tunnel mappings are found in the terminal's dco:
    - dco class="lSR" (left shift register): input tunnel
    - dco class="rSR" (right shift register): output tunnel
    - dco class="lpTun" (loop tunnel): simple pass-through
    - dco class="lMax": the For-loop N (iteration-count) INPUT terminal
      (loopLimitDCO) — NOT an aggregation output; the indexing/accumulator
      output is an lpTun (see Tunnel.mode)
    - The dco's termList contains [inner_uid, outer_uid]

    Args:
        root: XML root element

    Returns:
        List of ParsedLoopStructure with tunnel mappings
    """
    loops: list[ParsedLoopStructure] = []
    # Computed once per VI (terminal uids are unique within a BD) -- see
    # _wired_terminal_uids docstring.
    wired_uids = _wired_terminal_uids(root)

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
                            new_tunnels = extract_tunnel_mapping(dco, dco_class)
                            _annotate_sr_tunnels(
                                new_tunnels, dco_class, dco, wired_uids
                            )
                            tunnels.extend(new_tunnels)
                        # A while-loop serializes the RIGHT shift register
                        # NESTED inside the LEFT one (``<rsrDCO class="rSR">``),
                        # not as its own term the way a for-loop does. Extract
                        # the nested rSR too, else the right register's
                        # terminal, border glyph, and wire type never exist —
                        # only the left border renders (task #96). A for-loop's
                        # lSR carries an EMPTY ``<rsrDCO uid=.../>`` ref (no
                        # class, no termList) whose rSR is a standalone term, so
                        # this adds nothing there and never double-counts.
                        if dco_class == TUNNEL_CLASS_LEFT_SR:
                            rsr = dco.find("rsrDCO")
                            if rsr is not None and (
                                rsr.get("class") == TUNNEL_CLASS_RIGHT_SR
                            ):
                                new_rsr_tunnels = extract_tunnel_mapping(
                                    rsr, TUNNEL_CLASS_RIGHT_SR
                                )
                                _annotate_sr_tunnels(
                                    new_rsr_tunnels,
                                    TUNNEL_CLASS_RIGHT_SR,
                                    rsr,
                                    wired_uids,
                                )
                                tunnels.extend(new_rsr_tunnels)

            # Find inner diagram
            diag_list = loop_elem.find("diagramList")
            if diag_list is not None:
                inner_diag = diag_list.find("SL__arrayElement[@class='diag']")
                if inner_diag is not None:
                    inner_diagram_uid = inner_diag.get("uid")

                    # Find operations inside the inner diagram (direct only,
                    # not recursing into nested case/loop nodeLists)
                    node_list = inner_diag.find("nodeList")
                    if node_list is not None:
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
            stop_condition_inverted = False
            loop_test_dco = loop_elem.find("loopTestDCO[@class='lTst']")
            if loop_test_dco is not None:
                # The termList inside has the terminal receiving the stop boolean
                term_list = loop_test_dco.find("termList")
                if term_list is not None:
                    first_term = term_list.find("SL__arrayElement")
                    if first_term is not None:
                        stop_condition_uid = first_term.get("uid")

                # loopTestDCO's own <objFlags> bit 16 encodes the conditional
                # terminal's polarity (Stop-if-True vs Continue-if-True).
                # Data evidence (.tmp/task19_findings.md): bit 16 SET means
                # Stop-if-True (the common case: e.g. a standard "Stop
                # Button" control wired straight in), bit 16 CLEAR means
                # Continue-if-True. This is the opposite sense of
                # TERMINAL_DCO_INVERTED's meaning for cpdArith terminals.
                dco_flags_elem = loop_test_dco.find("objFlags")
                stop_condition_inverted = not is_inverted_terminal(
                    safe_int(dco_flags_elem)
                )

            # For-loop parallelism ("Configure Parallelism..."): a direct
            # child <ParForWorkers uid=.../> of the forLoop element itself
            # (never present on a while loop). <ParForIndexDistribution> is
            # deliberately not modeled -- "00" in every corpus occurrence.
            parallel = loop_elem.find("ParForWorkers") is not None
            parallel_static_workers = _parse_hex_int(
                safe_text(loop_elem.find("ParForNumStaticWorkers"))
            )

            loops.append(ParsedLoopStructure(
                uid=loop_uid,
                loop_type=loop_class,
                boundary_terminal_uids=boundary_terminals,
                tunnels=tunnels,
                inner_diagram_uid=inner_diagram_uid,
                inner_node_uids=inner_node_uids,
                stop_condition_terminal_uid=stop_condition_uid,
                stop_condition_inverted=stop_condition_inverted,
                parallel=parallel,
                parallel_static_workers=parallel_static_workers,
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
