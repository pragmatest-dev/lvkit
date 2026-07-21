"""Event structure parsing.

An Event Structure (heap ``class="eventStruct"``) is structurally close to a
case structure: one frame ("diag") per registered event, a selector-style
navigator at the top (``[N] EventName``), and border tunnels threading data
through every frame. This module mirrors ``case.py``'s frame/tunnel
extraction shape.

Frame labels: LabVIEW stores a real label for only ONE frame — the one it
last displayed in the editor (heap ``selString``/``textRec``/``text``, keyed
by the heap's own ``dIdx``). Unlike a case structure there is no per-frame
label table in the dataspace (no ``EventSpec``-to-name correlation is
recoverable without an undocumented internal LabVIEW event-type-ID scheme —
see the module docstring investigation in task #75). Every OTHER frame gets
an honest ``"[N]"`` placeholder, matching the fallback ``case.py``/
``disable.py`` already use when a frame's real label isn't recoverable.
LabVIEW's own convention BAKES the bracketed index into the label text
itself (``" [3] "copyrights": Value Change "``), so the displayed frame's
faithful label and every other frame's placeholder share the same visual
shape.

Border tunnels: FOUR dco classes carry data across the structure boundary,
ALL using the identical per-frame-array shape as a case's ``selTun``
(``[frame0_inner, frame1_inner, ..., outer_self]`` — see
``base.py::extract_tunnel_mapping``, which self-selects on element count and
already handles this shape generically):

- ``eventTimeOut`` — the timeout-value input (top-left ⏱).
- ``eventDynDCO`` (appears twice, an ``otherSide``-linked pair) — the
  dynamic-event registration refnum passing through top+bottom (green
  terminals in LabVIEW).
- ``selTun`` — an ordinary output tunnel (e.g. a loop's "stop" boolean
  threaded out through every frame).

The Event Data Node / Event Filter Node (heap class ``eventDataNode`` for
BOTH — distinguished only by which per-frame list references the uid,
``dataNodeList``/``filterNodeList`` on the ``eventStruct`` element, not by a
separate XML class) are NOT tunnels — they're ordinary Bundle/Unbundle-By-
Name-shaped nodes (``nmxDCO`` field terminals, ``dcoAgg``/``dcoList``)
sitting in the frame's own diagram, parsed by ``_EventDataNodeHandler``
(node_types.py, a thin ``NMuxHandler`` subclass). This module only needs to
fold their uids into the frame's ``inner_node_uids`` so the graph layer
parents/frame-stamps them like any other frame-owned node — see
``_frame_extra_node_uids``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import EventFrame, Tunnel

from ..models import ParsedEventStructure
from ..utils import clean_labview_string
from .base import extract_tunnel_mapping, frame_inner_node_uids

# dco classes on an eventStruct's OWN termList that carry data across the
# structure boundary. All four share the SAME per-frame-array shape as a
# case's selTun (see extract_tunnel_mapping) — no per-class branching needed.
EVENT_TUNNEL_DCO_CLASSES = ("selTun", "eventDynDCO", "eventTimeOut")

# The heap class an eventStruct frame's data/filter node uses — shared by
# BOTH the Event Data Node and the Event Filter Node (see module docstring).
_EVENT_DATA_NODE_CLASS = "eventDataNode"


def extract_event_structures(root: ET.Element) -> list[ParsedEventStructure]:
    """Extract Event Structures with frame mappings.

    Args:
        root: Block-diagram XML root element.

    Returns:
        List of ParsedEventStructure, one per ``class="eventStruct"`` element.
    """
    structures: list[ParsedEventStructure] = []
    for elem in root.findall(".//*[@class='eventStruct']"):
        uid = elem.get("uid")
        if not uid:
            continue
        es = _extract_one_event_structure(elem, uid)
        if es:
            structures.append(es)
    return structures


def _extract_one_event_structure(
    elem: ET.Element, uid: str,
) -> ParsedEventStructure | None:
    diag_list = elem.find("diagramList")
    diag_elems = (
        diag_list.findall("SL__arrayElement[@class='diag']")
        if diag_list is not None else []
    )
    if not diag_elems:
        return None
    num_frames = len(diag_elems)

    tunnels = _extract_event_tunnels(elem)
    labels, displayed = _resolve_frame_labels(elem, num_frames)
    data_node_uids = _list_uids(elem.find("dataNodeList"))
    filter_node_uids = _list_uids(elem.find("filterNodeList"))

    frames: list[EventFrame] = []
    for idx, diag_elem in enumerate(diag_elems):
        inner_node_uids = frame_inner_node_uids(diag_elem)
        inner_node_uids += _frame_extra_node_uids(
            inner_node_uids, data_node_uids, filter_node_uids, idx,
        )
        frames.append(EventFrame(
            event_label=labels[idx],
            inner_node_uids=inner_node_uids,
        ))

    return ParsedEventStructure(
        uid=uid,
        frames=frames,
        tunnels=tunnels,
        displayed_frame=displayed,
        filter_node_uids=frozenset(u for u in filter_node_uids if u),
    )


def _extract_event_tunnels(elem: ET.Element) -> list[Tunnel]:
    """Extract the structure's own boundary tunnels from its ``termList``.

    Same per-frame-array shape as a case structure's ``selTun`` for every
    dco class involved (see ``EVENT_TUNNEL_DCO_CLASSES``); reuses the shared
    generic extractor rather than reimplementing it (unlike disable.py,
    there's no cross-module import cycle here to avoid).
    """
    tunnels: list[Tunnel] = []
    term_list_elem = elem.find("termList")
    if term_list_elem is None:
        return tunnels
    for term_elem in term_list_elem.findall("SL__arrayElement[@class='term']"):
        dco = term_elem.find("dco")
        if dco is None:
            continue
        dco_class = dco.get("class", "")
        if dco_class not in EVENT_TUNNEL_DCO_CLASSES:
            continue
        tunnels.extend(extract_tunnel_mapping(dco, dco_class))
    return tunnels


def _list_uids(list_elem: ET.Element | None) -> list[str | None]:
    """Per-frame uid list from ``dataNodeList``/``filterNodeList`` — index i
    is frame i's data/filter node uid, or ``None`` when the heap's own
    ``uid="0"`` marks "no node for this frame" (a frame with no event-data
    fields wired, or that doesn't use the Filter Node at all)."""
    if list_elem is None:
        return []
    uids: list[str | None] = []
    for e in list_elem.findall("SL__arrayElement"):
        u = e.get("uid")
        uids.append(u if u and u != "0" else None)
    return uids


def _frame_extra_node_uids(
    existing: list[str],
    data_node_uids: list[str | None],
    filter_node_uids: list[str | None],
    idx: int,
) -> list[str]:
    """The frame's own Event Data Node / Event Filter Node uid(s), if any and
    not already picked up by ``frame_inner_node_uids`` (they never are —
    ``eventDataNode`` isn't a STRUCTURE_NODE_CLASSES member, so the shared
    zPlaneList structure-scan skips it; listed here explicitly instead, keyed
    by the authoritative per-frame ``dataNodeList``/``filterNodeList`` heap
    arrays rather than a generic zPlaneList walk)."""
    extra: list[str] = []
    for uids in (data_node_uids, filter_node_uids):
        if idx < len(uids) and uids[idx] and uids[idx] not in existing:
            extra.append(uids[idx])  # type: ignore[arg-type]
    return extra


def _resolve_frame_labels(
    elem: ET.Element, num_frames: int,
) -> tuple[list[str], int | None]:
    """Per-frame display labels, faithful for exactly one frame (see module
    docstring): every frame defaults to ``"[N]"``; the frame LabVIEW last
    displayed (heap ``dIdx``, omitted when 0 — the same "XML omits a 0
    field" convention as ``parmIndex``/``paramIdx`` elsewhere in this parser)
    gets its own precomputed ``selString`` text instead, verbatim (LabVIEW's
    own rendering already includes the bracketed index, e.g. ``[3]
    "copyrights": Value Change`` or ``[0] Timeout`` — confirmed against two
    independent VIs).
    """
    labels = [f"[{i}]" for i in range(num_frames)]
    d_idx_text = elem.findtext("dIdx")
    displayed = (
        int(d_idx_text) if d_idx_text and d_idx_text.lstrip("-").isdigit() else 0
    )
    if not (0 <= displayed < num_frames):
        return labels, None
    sel = elem.find("selString")
    if sel is not None:
        text_elem = sel.find("textRec/text")
        if text_elem is not None and text_elem.text:
            label = clean_labview_string(text_elem.text).strip()
            if label:
                labels[displayed] = label
    return labels, displayed
