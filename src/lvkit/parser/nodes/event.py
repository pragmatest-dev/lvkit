"""Event structure parsing.

An Event Structure (heap ``class="eventStruct"``) is structurally close to a
case structure: one frame ("diag") per registered event, a selector-style
navigator at the top (``[N] EventName``), and border tunnels threading data
through every frame. This module mirrors ``case.py``'s frame/tunnel
extraction shape.

Frame labels: LabVIEW stores a real, faithful label for only ONE frame — the
one it last displayed in the editor (heap ``selString``/``textRec``/``text``,
keyed by the heap's own ``dIdx``). Every OTHER frame's label is RECONSTRUCTED
(task #75 follow-up) from that frame's ``EventSpec`` entry
(``EventNodeEvents/SL__arrayElement[@class='EventSpec']``, keyed by
``diagramIdx``): ``ddoUID`` names the source control — resolved to its
caption via the FRONT-PANEL heap (``ddo[@uid=...]``, same
``extract_label``-on-a-``ddo`` lookup ``vi.py`` already uses for front-panel
control names) — and ``type`` is the event-type code, matched against
``_CONFIRMED_EVENT_TYPES`` (a short, clean-room-verified table; an
unconfirmed code is surfaced as an explicit ``<unknown event 0x...>``
sentinel, never guessed at, see ``_format_event_label``). LabVIEW's
own convention BAKES the bracketed index into the label text itself
(``" [3] "copyrights": Value Change "``), so the displayed frame's faithful
label and every reconstructed label share the same visual shape — the
displayed frame's own heap text is left untouched and used as a
cross-check (see ``_resolve_frame_labels``).

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
from dataclasses import dataclass
from pathlib import Path

from lvkit.models import EventFrame, Tunnel

from ..models import ParsedEventStructure
from ..utils import clean_labview_string, extract_label
from .base import extract_tunnel_mapping, frame_inner_node_uids

# dco classes on an eventStruct's OWN termList that carry data across the
# structure boundary. All four share the SAME per-frame-array shape as a
# case's selTun (see extract_tunnel_mapping) — no per-class branching needed.
EVENT_TUNNEL_DCO_CLASSES = ("selTun", "eventDynDCO", "eventTimeOut")

# The heap class an eventStruct frame's data/filter node uses — shared by
# BOTH the Event Data Node and the Event Filter Node (see module docstring).
_EVENT_DATA_NODE_CLASS = "eventDataNode"

# EventSpec.type codes confirmed clean-room by cross-checking the
# reconstructed label against the displayed frame's own faithful heap text
# (VI Tester About.vi — see module docstring). Add an entry here ONLY once
# confirmed the same way; an unconfirmed/unmapped code is never guessed at —
# ``_format_event_label`` just omits the event-type name for it.
_CONFIRMED_EVENT_TYPES: dict[int, str] = {
    1073741826: "Value Change",
    1073741825: "Timeout",
}


@dataclass(frozen=True)
class _EventSpec:
    """One frame's ``EventSpec`` entry: the source control's DDO uid (``None``
    when ``ddoUID`` is ``0`` — a pane/app/filter event with no control) and
    the raw event-type code (``None`` if unparseable)."""

    ddo_uid: str | None
    type_code: int | None


def extract_event_structures(
    root: ET.Element,
    fp_xml_path: Path | str | None = None,
) -> list[ParsedEventStructure]:
    """Extract Event Structures with frame mappings.

    Args:
        root: Block-diagram XML root element.
        fp_xml_path: Path to the VI's ``*_FPHb.xml`` (front-panel heap),
            needed to resolve an ``EventSpec.ddoUID`` to its control's
            caption when reconstructing non-displayed frames' labels. Frame
            labels degrade to the bare ``"[N]"``/event-type-only form when
            omitted (e.g. no front panel available).

    Returns:
        List of ParsedEventStructure, one per ``class="eventStruct"`` element.
    """
    fp_root = _parse_fp_root(fp_xml_path)
    structures: list[ParsedEventStructure] = []
    for elem in root.findall(".//*[@class='eventStruct']"):
        uid = elem.get("uid")
        if not uid:
            continue
        es = _extract_one_event_structure(elem, uid, fp_root)
        if es:
            structures.append(es)
    return structures


def _parse_fp_root(fp_xml_path: Path | str | None) -> ET.Element | None:
    """Parse the front-panel heap, or None if unavailable — the caption
    lookup then degrades gracefully rather than crashing."""
    if fp_xml_path is None:
        return None
    path = Path(fp_xml_path)
    if not path.exists():
        return None
    return ET.parse(path).getroot()


def _extract_one_event_structure(
    elem: ET.Element, uid: str, fp_root: ET.Element | None,
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
    event_specs = _extract_event_specs(elem)
    labels, displayed = _resolve_frame_labels(elem, num_frames, event_specs, fp_root)
    data_node_uids = _list_uids(elem.find("dataNodeList"))
    filter_node_uids = _list_uids(elem.find("filterNodeList"))

    frames: list[EventFrame] = []
    for idx, diag_elem in enumerate(diag_elems):
        inner_node_uids = frame_inner_node_uids(diag_elem)
        inner_node_uids += _frame_extra_node_uids(
            inner_node_uids, data_node_uids, filter_node_uids, idx,
        )
        frames.append(EventFrame(
            index=idx,
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


def _extract_event_specs(elem: ET.Element) -> dict[int, _EventSpec]:
    """Per-frame ``EventSpec`` info, keyed by ``diagramIdx`` — the ONLY heap
    source correlating a frame to its source control and event-type code
    (see module docstring). ``EventNodeEvents`` is the eventStruct's own
    array of ``EventSpec`` elements; absent (or malformed) entries are simply
    skipped, leaving that frame's reconstruction to degrade to ``"[N]"``.
    """
    specs: dict[int, _EventSpec] = {}
    events_list = elem.find("EventNodeEvents")
    if events_list is None:
        return specs
    for spec_elem in events_list.findall("SL__arrayElement[@class='EventSpec']"):
        idx_text = spec_elem.findtext("diagramIdx")
        if idx_text is None or not idx_text.lstrip("-").isdigit():
            continue
        ddo_uid = spec_elem.findtext("ddoUID")
        type_text = spec_elem.findtext("type")
        specs[int(idx_text)] = _EventSpec(
            ddo_uid=ddo_uid if ddo_uid and ddo_uid != "0" else None,
            type_code=(
                int(type_text)
                if type_text and type_text.lstrip("-").isdigit()
                else None
            ),
        )
    return specs


def _resolve_control_caption(
    fp_root: ET.Element | None, ddo_uid: str,
) -> str | None:
    """The source control's caption, read from the FRONT-PANEL heap's own
    ``ddo`` element (keyed by ``EventSpec.ddoUID``) — the same
    ``extract_label``-on-a-``ddo`` lookup ``vi.py``'s ``_parse_ddo`` already
    uses for front-panel control names. None if the FP heap is unavailable,
    the uid isn't found, or the ddo carries no label text (clean-room:
    degrade rather than guess)."""
    if fp_root is None:
        return None
    ddo = fp_root.find(f".//ddo[@uid='{ddo_uid}']")
    if ddo is None:
        return None
    return extract_label(ddo)


def _format_event_label(
    idx: int, spec: _EventSpec | None, fp_root: ET.Element | None,
) -> str:
    """Reconstruct frame ``idx``'s display label from its ``EventSpec``,
    matching LabVIEW's own displayed-frame format
    (``[N] "<Control>": <EventType>``) exactly. Degrades honestly — a control
    that can't be resolved is omitted, and a ``type`` code not in the
    clean-room-verified ``_CONFIRMED_EVENT_TYPES`` table becomes an explicit
    ``<unknown event 0x...>`` sentinel: never a guessed event NAME, but never a
    silently blank frame either, so the label states plainly that we don't
    recognise the event type (and carries the raw code for lookup/reporting)."""
    base = f"[{idx}]"
    if spec is None:
        return base
    caption = (
        _resolve_control_caption(fp_root, spec.ddo_uid) if spec.ddo_uid else None
    )
    type_name = (
        _CONFIRMED_EVENT_TYPES.get(spec.type_code)
        if spec.type_code is not None
        else None
    )
    # A real type code we haven't clean-room-confirmed: show a sentinel with the
    # raw code, not a fabricated name and not nothing.
    if type_name is None and spec.type_code is not None:
        type_name = f"<unknown event 0x{spec.type_code & 0xFFFFFFFF:08X}>"
    if caption and type_name:
        return f'{base} "{caption}": {type_name}'
    if caption:
        return f'{base} "{caption}"'
    if type_name:
        return f"{base} {type_name}"
    return base


def _resolve_frame_labels(
    elem: ET.Element,
    num_frames: int,
    event_specs: dict[int, _EventSpec],
    fp_root: ET.Element | None,
) -> tuple[list[str], int | None]:
    """Per-frame display labels: every frame is RECONSTRUCTED from its
    ``EventSpec`` (see ``_format_event_label``); the frame LabVIEW last
    displayed (heap ``dIdx``, omitted when 0 — the same "XML omits a 0
    field" convention as ``parmIndex``/``paramIdx`` elsewhere in this parser)
    then gets its own precomputed ``selString`` text INSTEAD, verbatim —
    LabVIEW's own rendering already includes the bracketed index, e.g.
    ``[3] "copyrights": Value Change`` or ``[0] Timeout`` — that faithful
    text is the ground truth this reconstruction was verified against, and
    it always wins over the reconstruction for its own frame.
    """
    labels = [
        _format_event_label(i, event_specs.get(i), fp_root)
        for i in range(num_frames)
    ]
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
