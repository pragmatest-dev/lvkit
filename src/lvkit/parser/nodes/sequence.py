"""Sequence structure parsing (flat and stacked)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import SequenceFrame, Tunnel

from ..constants import TERMINAL_CLASS, TUNNEL_DCO_CLASSES
from ..models import ParsedFlatSequenceStructure
from .base import (
    extract_tunnel_mapping,
    frame_inner_node_uids,
    parse_displayed_frame,
)

# Both flat and stacked sequences enforce sequential execution.
# Flat: class="flatSequence", frames under <sequenceList>
# Stacked: class="seq", frames under <diagramList>
_SEQ_CLASSES = ("flatSequence", "seq", "sequence")


def extract_flat_sequences(
    root: ET.Element,
) -> list[ParsedFlatSequenceStructure]:
    """Extract sequence structures (flat and stacked).

    Both types enforce execution order:
    - Frame 0 executes first, then frame 1, etc.
    - Each frame has tunnels (seqTun, flatSeqTun) for data flow
    - flatSeqTun tunnels have a mate linking frames

    Args:
        root: XML root element

    Returns:
        List of ParsedFlatSequenceStructure with frame and tunnel info
    """
    result: list[ParsedFlatSequenceStructure] = []

    for seq_class in _SEQ_CLASSES:
        for seq_elem in root.findall(f".//*[@class='{seq_class}']"):
            seq = _extract_one_sequence(seq_elem, seq_class)
            if seq:
                result.append(seq)

    return result


def _extract_one_sequence(
    seq_elem: ET.Element,
    seq_class: str,
) -> ParsedFlatSequenceStructure | None:
    """Extract a single sequence structure."""
    seq_uid = seq_elem.get("uid")
    if not seq_uid:
        return None

    all_tunnels: list[Tunnel] = []
    frames: list[SequenceFrame] = []

    # Find frame container:
    # flatSequence uses <sequenceList> with sequenceFrame children
    # stacked seq uses <diagramList> with diag children
    if seq_class == "flatSequence":
        frame_container = seq_elem.find("sequenceList")
        frame_class = "sequenceFrame"
    else:
        frame_container = seq_elem.find("diagramList")
        frame_class = "diag"

    if frame_container is None:
        return None

    # Also extract tunnels from the sequence's own termList
    _extract_tunnels_from_termlist(seq_elem, all_tunnels)

    for i, frame_elem in enumerate(
        frame_container.findall(f"SL__arrayElement[@class='{frame_class}']")
    ):
        frame_uid = frame_elem.get("uid")
        if not frame_uid:
            continue

        # Extract tunnels from frame's termList
        _extract_tunnels_from_termlist(frame_elem, all_tunnels)

        # Find inner nodes from the frame's diagram
        inner_node_uids = _extract_inner_node_uids(frame_elem)

        frames.append(
            SequenceFrame(
                index=i,
                uid=frame_uid,
                inner_node_uids=inner_node_uids,
            )
        )

    # Only a STACKED sequence stacks its frames and remembers a displayed one;
    # a flat sequence shows every frame side by side (no single initial view).
    displayed_frame = (
        parse_displayed_frame(seq_elem, len(frames))
        if seq_class != "flatSequence"
        else None
    )

    return ParsedFlatSequenceStructure(
        uid=seq_uid,
        tunnels=all_tunnels,
        frames=frames,
        displayed_frame=displayed_frame,
    )


def _extract_tunnels_from_termlist(
    elem: ET.Element,
    tunnels: list[Tunnel],
) -> None:
    """Extract tunnel mappings from an element's termList."""
    term_list_elem = elem.find("termList")
    if term_list_elem is None:
        return

    for term_elem in term_list_elem.findall(
        f"SL__arrayElement[@class='{TERMINAL_CLASS}']"
    ):
        dco = term_elem.find("dco")
        if dco is not None:
            dco_class = dco.get("class", "")
            if dco_class in TUNNEL_DCO_CLASSES:
                tunnels.extend(extract_tunnel_mapping(dco, dco_class))


def _extract_inner_node_uids(frame_elem: ET.Element) -> list[str]:
    """Extract inner node UIDs from a frame element.

    For flatSequence frames, the diagramList is inside the frame.
    For stacked seq, the frame IS the diagram.
    """
    inner_node_uids: list[str] = []

    # For sequenceFrame: look inside diagramList. For stacked seq diag frames:
    # the frame element IS the diagram. Either way, frame_inner_node_uids reads
    # nodeList + zPlaneList structures (so a nested flat sequence isn't orphaned).
    diag_list = frame_elem.find("diagramList")
    if diag_list is not None:
        for diag_elem in diag_list.findall("SL__arrayElement[@class='diag']"):
            inner_node_uids.extend(frame_inner_node_uids(diag_elem))
    else:
        inner_node_uids.extend(frame_inner_node_uids(frame_elem))

    return inner_node_uids
