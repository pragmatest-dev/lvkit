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
  - ``activeDiag`` -- hex index of the active subdiagram (the enabled /
    accepted / matching frame). The heap OMITS it when it is 0 (zero-valued
    fields are dropped), so absent => frame 0.
  - ``commentSelInfoArray`` -- one ``SelectorInfoElement`` per frame, in frame
    order. A Conditional Disable frame stores its condition as hex-ASCII tokens
    under ``activeDiag/Tag0000`` (e.g. ``["RUN_TIME_ENGINE","False"]`` ->
    ``RUN_TIME_ENGINE==False``); an empty token list is the else/Default frame.
    Diagram Disable / Type Specialization frames carry no tokens.
  - ``Tag0273`` -- present only on a Type Specialization Structure (observed on
    every type-spec in the corpus, absent on every diagram/conditional).
  - ``selString/textRec/text`` -- the caption LabVIEW stored for the active
    frame (e.g. ``" [2] Ignored "``). Only meaningful for one frame, so not used.

Per-frame labels, by subtype:
  - Diagram Disable: the ``activeDiag`` frame is "Enabled", every other frame
    is "Disabled" (a LabVIEW invariant: exactly one enabled, the rest disabled).
  - Conditional Disable: each frame's own decoded condition; empty -> "Default".
  - Type Specialization: bare storage-order ``[i]``. LabVIEW's per-frame
    labels ([N] + Accepted/Declined/Ignored) are a COMPILE result (first
    subdiagram that compiles = Accepted) decided at edit time and NOT persisted
    (verified to the raw heap: only the active frame's slot is recoverable, one
    of N -- not enough to order or state the rest). So we show file order rather
    than fabricate it (see issue #31); file order need not match LabVIEW's [N].

Subtype is detected from stored fields (condition tokens => Conditional;
``Tag0273`` => Type Specialization; else Diagram Disable), never from the
user-editable border comment label. These labeling assumptions are pending
validation against a LabVIEW reference for issue #31.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import CaseFrame, DisableStructureKind, Tunnel
from lvkit.text_encoding import decode_labview_text

from ..constants import STRUCTURE_NODE_CLASSES, TERMINAL_CLASS
from ..models import ParsedDisableStructure
from .base import frame_inner_node_uids, parse_displayed_frame

COMMENT_NODE_CLASS = "commentNode"
COMMENT_TUNNEL_CLASS = "commentTun"


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
    for elem in root.findall(f".//*[@class='{COMMENT_NODE_CLASS}']"):
        if not is_disable_structure(elem):
            continue
        uid = elem.get("uid")
        if not uid:
            continue
        ds = _extract_one_disable_structure(elem, uid)
        if ds:
            structures.append(ds)
    return structures


def _parse_index(text: str | None, *, base: int) -> int | None:
    """Parse a frame-index heap field, or None if absent/unparseable.
    ``activeDiag`` is hex (``"01"``); ``dIdx`` is decimal (``"2"``, or an
    INT_MIN "no selection" sentinel) -- hence the explicit base."""
    if not text:
        return None
    try:
        return int(text, base)
    except ValueError:
        return None


def _selector_infos(elem: ET.Element) -> list[ET.Element]:
    """The per-frame ``SelectorInfoElement`` list (frame order), or []."""
    csa = elem.find("commentSelInfoArray")
    if csa is None:
        return []
    return csa.findall("SL__arrayElement[@class='SelectorInfoElement']")


def _condition_tokens(sel_info: ET.Element) -> list[str]:
    """Raw hex-encoded condition tokens for one frame -- empty for a
    non-Conditional frame or a Conditional's else/Default frame."""
    return [
        raw
        for el in sel_info.findall("activeDiag/Tag0000/SL__arrayElement")
        if (raw := (el.text or "").strip())
    ]


def _decode_condition(raw_tokens: list[str]) -> str:
    """Decode one Conditional Disable frame's condition tokens to a label.
    Empty -> the else/Default frame. A ``[SYMBOL, VALUE]`` pair renders as
    ``SYMBOL==VALUE``: the comparison operator is NOT stored in these tokens,
    and ``==`` is LabVIEW's dialog default (and matches the corpus/reference) --
    a ``!=`` condition would need the operator decoded from the ExpressionInfo
    tree, for which there is no sample yet. Any other token count is an unknown
    shape, joined with spaces rather than fabricating an operator. Values are
    decoded with the project text decoder (they are user strings, not ASCII)."""
    decoded: list[str] = []
    for raw in raw_tokens:
        try:
            decoded.append(decode_labview_text(bytes.fromhex(raw)))
        except ValueError:
            decoded.append("?")
    if not decoded:
        return "Default"
    if len(decoded) == 2:
        return f"{decoded[0]}=={decoded[1]}"
    return " ".join(decoded)


def _disable_kind(
    elem: ET.Element, frame_conditions: list[list[str]]
) -> DisableStructureKind:
    """Detect the disable-structure subtype from stored fields (see module
    docstring): any per-frame condition tokens => Conditional; ``Tag0273`` =>
    Type Specialization; else Diagram Disable."""
    if any(frame_conditions):
        return DisableStructureKind.CONDITIONAL
    if elem.find("Tag0273") is not None:
        return DisableStructureKind.TYPE_SPEC
    return DisableStructureKind.DIAGRAM


def _frame_labels(
    kind: DisableStructureKind,
    n_frames: int,
    active: int,
    frame_conditions: list[list[str]],
) -> list[str]:
    """The data-driven per-frame labels (see module docstring). ``active`` is
    the resolved active-frame index (0 when ``activeDiag`` was absent)."""
    if kind is DisableStructureKind.CONDITIONAL:
        if len(frame_conditions) != n_frames:
            # The per-frame array doesn't line up with the frames -- we can't
            # trust index->condition, so fall back to honest index labels rather
            # than emit confidently-misaligned conditions.
            return [f"Frame {i}" for i in range(n_frames)]
        return [_decode_condition(toks) for toks in frame_conditions]
    if kind is DisableStructureKind.TYPE_SPEC:
        # Frames get their bare STORAGE-ORDER index. LabVIEW's real per-frame
        # labels ([N] number + Accepted/Declined/Ignored state) are a COMPILE
        # result (first subdiagram that compiles = Accepted) decided at edit
        # time and NOT persisted -- only the ACTIVE frame's compile-order slot is
        # recoverable (activeDiag + dIdx), one of N, which isn't enough to order
        # or state the rest. So we don't fabricate them; we show file order (see
        # issue #31). NOTE: file order need not match LabVIEW's [N].
        return [f"[{i}]" for i in range(n_frames)]
    # Diagram Disable: exactly one Enabled frame; the rest Disabled. Index-
    # qualify when there is more than one disabled subdiagram so the frames stay
    # distinguishable (render keys frames by their label).
    return [
        "Enabled"
        if i == active
        else ("Disabled" if n_frames == 2 else f"Disabled [{i}]")
        for i in range(n_frames)
    ]


def _extract_one_disable_structure(
    elem: ET.Element,
    uid: str,
) -> ParsedDisableStructure | None:
    diag_list = elem.find("diagramList")
    diag_elems = (
        diag_list.findall("SL__arrayElement[@class='diag']")
        if diag_list is not None
        else []
    )
    if not diag_elems:
        return None
    n_frames = len(diag_elems)

    # activeDiag (hex) = the active/enabled/accepted frame. The heap omits it
    # when it is 0, so an ABSENT one means frame 0; a present-but-unparseable or
    # out-of-range value stays None (unresolved) rather than a misleading 0.
    active_text = elem.findtext("activeDiag")
    if active_text is None:
        active_frame: int | None = 0
    else:
        active_frame = _parse_index(active_text, base=16)
        if active_frame is not None and not 0 <= active_frame < n_frames:
            active_frame = None

    active = active_frame if active_frame is not None else 0
    frame_conditions = [_condition_tokens(si) for si in _selector_infos(elem)]
    kind = _disable_kind(elem, frame_conditions)
    labels = _frame_labels(kind, n_frames, active, frame_conditions)

    frames = [
        CaseFrame(
            selector_value=labels[idx],
            inner_node_uids=frame_inner_node_uids(diag_elem),
            is_default=idx == active,
        )
        for idx, diag_elem in enumerate(diag_elems)
    ]

    return ParsedDisableStructure(
        uid=uid,
        frames=frames,
        tunnels=_extract_disable_tunnels(elem),
        active_frame=active_frame,
        kind=kind,
        # The frame LabVIEW last displayed (heap ``dIdx``, range-checked) -- the
        # saved visible frame, which for a Conditional Disable can differ from
        # the enabled/active one. None when ``dIdx`` is an out-of-range legacy
        # ordinal (an INT_MIN sentinel on a plain Diagram Disable), so the
        # renderer keeps its Enabled/active_frame fallback. See issue #30.
        displayed_frame=parse_displayed_frame(elem, n_frames),
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
            tunnels.append(
                Tunnel(
                    outer_terminal_uid=outer_uid,
                    inner_terminal_uid=inner_uid,
                    tunnel_type=COMMENT_TUNNEL_CLASS,
                )
            )
    return tunnels
