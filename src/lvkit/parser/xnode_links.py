"""The library file each XNode is an instance of, from the VI's link records.

LabVIEW records, for every XNode library a block diagram uses, one ``XNXI``
("XNode To XInterface") record in the block-diagram link table (``LIbd``, under
``BDHP``): ``LinkSavePathRef`` is the recorded path of the ``.xnode`` file
(``<vilib>/ErrorRing/Error Ring.xnode``) and ``LinkOffsetList`` the heap uid of
every XNode of it. pylabview decodes it into the main XML for most VIs; for the
rest the link table stays the raw ``_LIbd.bin`` sidecar, which
``link_table.decode_xnode_links`` reads.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .error_ring import ERROR_RING_PATH, decode_error_ring
from .link_table import LinkTableVersion, decode_xnode_links
from .models import ParsedNode, ParsedTerminalInfo
from .naming import build_relative_path
from .node_types import XNodeNode
from .utils import extract_xtunnel_id
from .xml_trees import parsed_xml

# The XNode libraries whose terminals, with no <englishName>, are named by their
# xTunnel <id>: the two checked against the corpus (every Error Ring and Match
# Regular Expression xTunnel's <id> is its terminal's name). Any other XNode,
# an FPGA Interface node among them, keeps the names #107 gives it.
ID_NAMED_LIBRARIES = frozenset(
    {ERROR_RING_PATH, "<vilib>/regexp/Match Regular Expression.xnode"}
)


def read_xnode_paths(root: ET.Element, main_xml: Path) -> dict[int, str]:
    """The recorded library path of each XNode, by heap uid: from the main XML,
    else from the raw ``_LIbd.bin`` sidecar beside it."""
    paths: dict[int, str] = {}
    elements = root.findall(".//LIbd//BDHP/XNXI")
    if elements:
        for element in elements:
            tokens = [s.text or "" for s in element.findall("./LinkSavePathRef/String")]
            for offset in element.findall("./LinkOffsetList/Offset"):
                if offset.text:
                    paths[int(offset.text, 0)] = build_relative_path(tokens)
        return paths
    sidecar = main_xml.with_name(main_xml.stem + "_LIbd.bin")
    version = LinkTableVersion.of_vi(root)
    if sidecar.exists() and version is not None:
        for link in decode_xnode_links(sidecar.read_bytes(), version):
            for uid in link.node_uids:
                paths[uid] = build_relative_path(list(link.path_tokens))
    return paths


def resolve_xnode_libraries(
    nodes: list[ParsedNode], main_xml: Path | str | None
) -> None:
    """Give each XNode its recorded library path, in place, and an Error Ring
    its configuration."""
    pending = [n for n in nodes if isinstance(n, XNodeNode)]
    if not pending or main_xml is None:
        return
    paths = read_xnode_paths(parsed_xml(Path(main_xml)), Path(main_xml))
    for node in pending:
        node.library_path = paths.get(int(node.uid), "") if node.uid.isdigit() else ""
        if node.library_path == ERROR_RING_PATH:
            node.error_ring = decode_error_ring(node.state_data)


def name_terminals_by_id(
    root: ET.Element,
    nodes: list[ParsedNode],
    terminal_info: dict[str, ParsedTerminalInfo],
) -> None:
    """Name each still-unnamed terminal of an XNode of an ``ID_NAMED_LIBRARIES``
    library by its xTunnel ``<id>``, in place."""
    named = {
        n.uid
        for n in nodes
        if isinstance(n, XNodeNode) and n.library_path in ID_NAMED_LIBRARIES
    }
    if not named:
        return
    for elem in root.iter():
        if elem.get("class") != "xNode" or elem.get("uid") not in named:
            continue
        for term in elem.findall("./termList/SL__arrayElement"):
            info = terminal_info.get(term.get("uid") or "")
            dco = term.find("dco")
            if info is not None and info.name is None and dco is not None:
                info.name = extract_xtunnel_id(dco)
