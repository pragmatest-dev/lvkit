"""In Place Element Structure (decomposeRecomposeStructure) parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import Tunnel

from ..constants import TERMINAL_CLASS, TUNNEL_CLASS_DECOMPOSE_RECOMPOSE
from ..models import ParsedDecomposeRecomposeStructure
from .base import extract_tunnel_mapping


def extract_decompose_structures(
    root: ET.Element,
) -> list[ParsedDecomposeRecomposeStructure]:
    """Extract In Place Element Structures with tunnel mappings.

    IPES in LabVIEW decompose a cluster/array/DVR into fields at entry,
    allow inner operations to modify those fields, then recompose at exit.
    Structurally like a loop: one inner diagram, tunnels at the boundary.

    The tunnel type is 'decomposeRecomposeTunnel'. Format is
    [inner_uid, outer_uid] — same as loop/case tunnels.

    Args:
        root: XML root element

    Returns:
        List of ParsedDecomposeRecomposeStructure with tunnel mappings
    """
    structures: list[ParsedDecomposeRecomposeStructure] = []

    for elem in root.findall(".//*[@class='decomposeRecomposeStructure']"):
        uid = elem.get("uid")
        if not uid:
            continue

        tunnels: list[Tunnel] = []
        inner_node_uids: list[str] = []

        # Extract tunnels from termList
        term_list_elem = elem.find("termList")
        if term_list_elem is not None:
            for term_elem in term_list_elem.findall(
                f"SL__arrayElement[@class='{TERMINAL_CLASS}']"
            ):
                dco = term_elem.find("dco")
                if dco is not None:
                    dco_class = dco.get("class", "")
                    if dco_class == TUNNEL_CLASS_DECOMPOSE_RECOMPOSE:
                        tunnel = extract_tunnel_mapping(dco, dco_class)
                        if tunnel:
                            tunnels.append(tunnel)

        # Extract inner node UIDs from the single inner diagram
        diag_list = elem.find("diagramList")
        if diag_list is not None:
            inner_diag = diag_list.find("SL__arrayElement[@class='diag']")
            if inner_diag is not None:
                node_list = inner_diag.find("nodeList")
                if node_list is not None:
                    for node_elem in node_list.findall("SL__arrayElement"):
                        node_uid = node_elem.get("uid")
                        if node_uid:
                            inner_node_uids.append(node_uid)

        structures.append(ParsedDecomposeRecomposeStructure(
            uid=uid,
            tunnels=tunnels,
            inner_node_uids=inner_node_uids,
        ))

    return structures
