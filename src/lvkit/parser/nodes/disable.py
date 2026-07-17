"""Diagram/Conditional Disable structure parsing.

A Disable structure (Diagram Disable Structure or Conditional Disable
Structure) is serialized in the block-diagram heap as ``class="commentNode"``.
Every ``commentNode`` observed in the corpus (152 instances across 112 real
VIs -- see task investigation) carries subdiagrams: a plain free-text comment
is a separate element, ``class="label"``. This module still gates on the
actual structural feature (``is_disable_structure``) rather than trusting
that invariant blindly, so an unexpected/malformed ``commentNode`` degrades
to "not a structure" instead of corrupting downstream parsing.

Heap shape (verified against ``ctm_head.vi`` node 1926 and 111 other corpus
VIs) mirrors a case structure closely:
  - ``termList`` -- direct children are the structure's OWN outer boundary
    terminals, each with a ``dco class="commentTun"``. A ``commentTun``'s own
    ``termList`` is ``[frame0_inner, frame1_inner, ..., outer_self]`` --
    POSITIONALLY IDENTICAL to a case structure's ``selTun`` (one inner
    terminal per frame, outer last). Each per-frame inner terminal is owned by
    an ``sRN`` node inside that frame's ``nodeList`` (same shift-register-node
    mechanism loops/cases already use for tunnels).
  - ``diagramList`` -- DIRECT child of the commentNode (NOT nested inside
    ``label``, which only holds the border's own display text/textRec).
    Its direct children are ``SL__arrayElement[@class='diag']`` frames, one
    per subdiagram (2 for a plain Diagram Disable Structure: conventionally
    "Enabled" then "Disabled"; 3+ for a Conditional Disable Structure: one
    per compiler-symbol condition plus an implicit "Default").
  - ``activeDiag`` -- hex index of the currently-active/displayed subdiagram.
  - ``selString/textRec/text`` -- the display label of THAT active
    subdiagram (e.g. ``" Disabled "``, ``" Default "``, or a symbol
    condition like ``" TARGET_TYPE==Windows "``).

Per-frame labels for anything beyond the active frame (and, for a 2-frame
structure, its fixed Enabled/Disabled complement) are NOT reliably
recoverable from the heap without decoding ``commentSelInfoArray``'s
per-frame ``ExpressionInfo`` (a follow-up -- faithful greyed-out styling of
disabled diagrams is out of scope here). Those frames get an honest,
non-misleading placeholder (``"Frame N"``), matching the ``case.py``
precedent of falling back to the frame index when no stored label exists.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import CaseFrame, Tunnel

from ..constants import STRUCTURE_NODE_CLASSES, TERMINAL_CLASS
from ..models import ParsedDisableStructure
from ..utils import clean_labview_string

COMMENT_NODE_CLASS = "commentNode"
COMMENT_TUNNEL_CLASS = "commentTun"

# Fixed complementary pair for a plain (2-frame) Diagram Disable Structure --
# a LabVIEW product invariant (only these two states exist), not a guess.
_COMPLEMENT_LABEL = {"enabled": "Disabled", "disabled": "Enabled"}


def is_disable_structure(elem: ET.Element) -> bool:
    """Whether a ``commentNode`` element is a Disable structure.

    Gates on the real structural feature -- a ``diagramList`` of >=1
    ``diag`` children -- rather than assuming every ``commentNode`` qualifies.
    """
    diag_list = elem.find("diagramList")
    if diag_list is None:
        return False
    return diag_list.find("SL__arrayElement[@class='diag']") is not None


def is_structure_boundary(elem: ET.Element) -> bool:
    """Whether ``elem`` is a structure-shaped container for the purposes of
    ``_find_own_descendants``-style bounded walks: every ``STRUCTURE_NODE_CLASSES``
    member, PLUS a genuine Disable structure (a plain ``commentNode`` comment
    is not a boundary -- it never contains further nested nodes worth
    stopping at)."""
    cls = elem.get("class", "")
    if cls in STRUCTURE_NODE_CLASSES:
        return True
    return cls == COMMENT_NODE_CLASS and is_disable_structure(elem)


def extract_disable_structures(root: ET.Element) -> list[ParsedDisableStructure]:
    """Extract Disable structures with frame mappings.

    Mirrors ``extract_case_structures`` (case.py) -- same diagramList/diag
    frame shape, same commentTun outer<->per-frame-inner tunnel shape
    (positionally identical to a case's selTun) -- but keyed off
    ``commentNode`` and with no selector terminal.
    """
    structures: list[ParsedDisableStructure] = []
    for elem in root.iter():
        if elem.get("class") != COMMENT_NODE_CLASS:
            continue
        if not is_disable_structure(elem):
            continue
        uid = elem.get("uid")
        if not uid:
            continue
        ds = _extract_one_disable_structure(elem, uid)
        if ds:
            structures.append(ds)
    return structures


def _parse_hex_index(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def _active_label(elem: ET.Element) -> str | None:
    """The display label of the currently-active subdiagram, from
    ``selString/textRec/text`` (quoted LabVIEW label text)."""
    sel = elem.find("selString")
    if sel is None:
        return None
    text_elem = sel.find("textRec/text")
    if text_elem is None:
        return None
    label = clean_labview_string(text_elem.text).strip()
    return label or None


def _extract_one_disable_structure(
    elem: ET.Element, uid: str,
) -> ParsedDisableStructure | None:
    diag_list = elem.find("diagramList")
    diag_elems = (
        diag_list.findall("SL__arrayElement[@class='diag']")
        if diag_list is not None else []
    )
    if not diag_elems:
        return None
    num_frames = len(diag_elems)

    active_idx = _parse_hex_index(elem.findtext("activeDiag"))
    active_label = _active_label(elem)

    tunnels = _extract_disable_tunnels(elem)

    frames: list[CaseFrame] = []
    for idx, diag_elem in enumerate(diag_elems):
        is_active = idx == active_idx
        if is_active and active_label:
            label = active_label
        elif num_frames == 2 and active_label:
            complement = _COMPLEMENT_LABEL.get(active_label.lower())
            label = complement or f"Frame {idx}"
        else:
            label = f"Frame {idx}"

        inner_node_uids: list[str] = []
        node_list = diag_elem.find("nodeList")
        if node_list is not None:
            for node_elem in node_list.findall("SL__arrayElement"):
                node_uid = node_elem.get("uid")
                if node_uid:
                    inner_node_uids.append(node_uid)

        frames.append(CaseFrame(
            selector_value=label,
            inner_node_uids=inner_node_uids,
            is_default=is_active,
        ))

    return ParsedDisableStructure(
        uid=uid,
        frames=frames,
        tunnels=tunnels,
        active_frame=active_idx,
    )


def _extract_disable_tunnels(elem: ET.Element) -> list[Tunnel]:
    """Extract the structure's own boundary tunnels from its direct
    ``termList`` (``dco class="commentTun"``).

    Layout is ``[frame0_inner, frame1_inner, ..., outer_self]`` -- the SAME
    selTun-style shape ``case.py::_extract_case_tunnels`` handles for case
    structures; reimplemented narrowly here (rather than imported) to avoid
    a parser-submodule import cycle (``case.py`` imports
    ``is_disable_structure`` from this module for its own boundary check).
    """
    tunnels: list[Tunnel] = []
    term_list_elem = elem.find("termList")
    if term_list_elem is None:
        return tunnels
    for term_elem in term_list_elem.findall(
        f"SL__arrayElement[@class='{TERMINAL_CLASS}']"
    ):
        term_uid = term_elem.get("uid")
        dco = term_elem.find("dco")
        if not term_uid or dco is None:
            continue
        if dco.get("class") != COMMENT_TUNNEL_CLASS:
            continue
        dco_term_list = dco.find("termList")
        if dco_term_list is None:
            continue
        term_refs: list[str] = [
            ref_uid
            for e in dco_term_list.findall("SL__arrayElement")
            if (ref_uid := e.get("uid"))
        ]
        if len(term_refs) < 2:
            continue
        outer_uid = term_refs[-1]
        for inner_uid in term_refs[:-1]:
            tunnels.append(Tunnel(
                outer_terminal_uid=outer_uid,
                inner_terminal_uid=inner_uid,
                tunnel_type=COMMENT_TUNNEL_CLASS,
            ))
    return tunnels
