"""Base extraction helpers for node parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import Tunnel, TunnelMode

from ..constants import (
    FREE_LABEL_CLASS,
    NODE_CLASS_COMMENT,
    STRUCTURE_NODE_CLASSES,
)
from ..utils import extract_label, safe_int, safe_text

# Re-export extract_label for backward compatibility
__all__ = [
    "extract_label",
    "extract_tunnel_mapping",
    "frame_inner_node_uids",
    "parse_displayed_frame",
]


def parse_displayed_frame(
    elem: ET.Element,
    num_frames: int,
    *,
    absent_is_zero: bool = False,
) -> int | None:
    """The frame LabVIEW last displayed for a stacked structure, from its heap
    ``dIdx``, or None when it isn't a usable local frame index.

    ``dIdx`` is the displayed frame's index ONLY when ``0 <= dIdx < num_frames``
    -- validated corpus-wide against ``selString`` (20/20 in-range boolean cases
    matched). An OUT-OF-RANGE ``dIdx`` (e.g. 17 on a 3-frame sequence) is an OLD
    global-diagram ordinal, NOT a frame index, so it yields None and the caller
    keeps its own fallback (default/first frame). This range check is exactly
    what tells the two encodings apart. See issue #30 (and #81, which correctly
    rejected the out-of-range value but over-generalised to rejecting ``dIdx``
    wholesale).

    ``absent_is_zero`` selects how a MISSING/non-numeric ``dIdx`` is read.
    LabVIEW omits the element when the value is 0 (the same "XML omits a 0
    field" convention as ``parmIndex``): the event structure relies on that, so
    it passes ``True`` (absent -> frame 0). Case/sequence pass ``False`` (absent
    -> None -> keep the existing default/first-frame fallback), since that
    omit-0 convention isn't separately verified for them."""
    d_idx_text = elem.findtext("dIdx")
    if not (d_idx_text and d_idx_text.lstrip("-").isdigit()):
        return 0 if (absent_is_zero and num_frames > 0) else None
    d = int(d_idx_text)
    return d if 0 <= d < num_frames else None

# zPlaneList element classes a frame OWNS beyond its nodeList — structures
# (incl. the disable ``commentNode``) and free labels. LabVIEW places a free
# label inside the diagram it belongs to, so one in a frame's zPlaneList is
# owned by that frame and must be stamped with it (see frame_inner_node_uids).
_FRAME_ZPLANE_OWNED = STRUCTURE_NODE_CLASSES | {NODE_CLASS_COMMENT, FREE_LABEL_CLASS}


def frame_inner_node_uids(diag_elem: ET.Element) -> list[str]:
    """UIDs of the nodes/structures/labels on a frame's diagram.

    Reads ``nodeList`` (the primary list, unchanged behaviour) AND augments it
    with any STRUCTURE or FREE-LABEL element found only in ``zPlaneList``.
    Neither list is a superset: for a NESTED FLAT SEQUENCE, LabVIEW lists the
    flat seq's inner ``sequenceFrame`` in the parent frame's ``nodeList`` but the
    ``flatSequence`` structure itself ONLY in the z-plane — so a nodeList-only
    walk orphans it (and everything inside it) to the top level. nodeList also
    uniquely carries the shift registers, and zPlaneList uniquely carries free
    labels (a per-frame comment lives in that frame's z-plane, not its
    nodeList), so we union: every nodeList member, plus every zPlaneList element
    whose class is a structure or free label. Order preserved, deduped.
    """
    uids: list[str] = []
    seen: set[str] = set()
    node_list = diag_elem.find("nodeList")
    if node_list is not None:
        for e in node_list.findall("SL__arrayElement"):
            u = e.get("uid")
            if u and u not in seen:
                seen.add(u)
                uids.append(u)
    zplane = diag_elem.find("zPlaneList")
    if zplane is not None:
        for e in zplane.findall("SL__arrayElement"):
            u = e.get("uid")
            if u and u not in seen and e.get("class") in _FRAME_ZPLANE_OWNED:
                seen.add(u)
                uids.append(u)
    return uids


# innerLpTunDCO objFlags bit: auto-index / auto-aggregate ENABLED. Set on both
# input (array->element) and output (element->array) array tunnels; LabVIEW
# calls both "indexing". Corpus-validated against resolved per-side types: set
# on ALL 254 aggregating lpTun tunnels and NONE of the 81 last-value ones.
_LP_TUN_INDEX_FLAG = 0x400000


def _lp_tun_mode(dco: ET.Element) -> TunnelMode:
    """Derive an ``lpTun`` dco's BASE ``TunnelMode`` -- read from the file's own
    auto-index flag, not inferred from types. The orthogonal ``conditional``
    modifier is decoded separately (see ``_lp_tun_conditional``).

    - ``innerLpTunDCO`` present:
      - indexing flag set -- ``innerLpTunDCO``'s ``objFlags`` bit ``0x400000``,
        OR an explicit ``<TunnelType>`` (older encoding) -> INDEXING.
      - flag clear (only ``<DefaultTunnelType>``) -> LAST_VALUE (an output
        tunnel passing the final iteration's value; the graph re-labels an
        INPUT tunnel here as PASSTHROUGH, since it knows direction).
    - ``innerLpTunDCO`` absent:
      - ``<TunnelType>02</TunnelType>`` -> CONCATENATING (rare).
      - otherwise (bare ``<dcoFiller>``) -> PASSTHROUGH.

    ``<TunnelType>`` presence alone MISSED 153 corpus tunnels that carry only
    the ``0x400000`` bit -- they were wrongly LAST_VALUE. The bit is the
    authoritative signal.
    """
    inner = dco.find("innerLpTunDCO")
    if inner is not None:
        indexing = (
            safe_int(inner.find("objFlags")) & _LP_TUN_INDEX_FLAG
            or dco.find("TunnelType") is not None
        )
        return TunnelMode.INDEXING if indexing else TunnelMode.LAST_VALUE
    if safe_text(dco.find("TunnelType")).strip() == "02":
        return TunnelMode.CONCATENATING
    return TunnelMode.PASSTHROUGH


def _lp_tun_conditional(dco: ET.Element) -> bool:
    """The orthogonal "Conditional" modifier on an output lpTun tunnel --
    ``<IsConditional>True</IsConditional>`` (a direct child of ``dco``, with a
    sibling ``<LpTunConditionDCO>``). Independent of the base mode: an output
    tunnel can conditionally take last-value, index, or concatenate."""
    return safe_text(dco.find("IsConditional")).strip() == "True"


def extract_tunnel_mapping(dco: ET.Element, dco_class: str) -> list[Tunnel]:
    """Extract tunnel mapping(s) from a dco element.

    Tunnels connect outer terminals to inner terminals across structure
    boundaries. Used by loops (lSR, rSR, lpTun, lMax), case structures (csTun),
    and sequences (seqTun, flatSeqTun).

    The termList is ``[inner_frame0, inner_frame1, ..., outer]``: the OUTER face
    is the LAST element, and every preceding element is a per-frame inner face.
    - A loop / flat-sequence / case-frame boundary tunnel has exactly ONE inner
      (2-element list → one Tunnel — the common case, unchanged).
    - A STACKED-sequence (or any multi-frame) tunnel is a UNION: one outer face
      shared across N frames, so N inner faces (N+1 elements → N Tunnels sharing
      the outer). This shape self-selects on element count — no branch on
      structure type is needed (flat/loop tunnels are 2-element, so they yield a
      single unchanged pair).

    The previous ``[inner, outer]`` read took only ``term_refs[0]``/``[1]``,
    which for a union DCO both dropped every inner past the first AND mistook an
    inner (``term_refs[1]``) for the outer — producing wrong tunnels for stacked
    sequences.

    Args:
        dco: dco element with tunnel info
        dco_class: Class of the dco (e.g., lSR, rSR, lpTun, lMax, csTun, seqTun)

    Returns:
        List of Tunnels (one per inner face); empty if invalid.
    """
    dco_term_list = dco.find("termList")
    if dco_term_list is None:
        return []

    term_refs: list[str] = [
        uid for e in dco_term_list.findall("SL__arrayElement") if (uid := e.get("uid"))
    ]

    if len(term_refs) < 2:
        return []

    outer_uid = term_refs[-1]
    # TunnelMode + the conditional modifier are lpTun-only concepts (see
    # models.TunnelMode) -- every other dco_class (lSR/rSR/lMax/csTun/seqTun/
    # ...) leaves mode=None / conditional=False.
    is_lp_tun = dco_class == "lpTun"
    mode = _lp_tun_mode(dco) if is_lp_tun else None
    conditional = _lp_tun_conditional(dco) if is_lp_tun else False
    return [
        Tunnel(
            outer_terminal_uid=outer_uid,
            inner_terminal_uid=inner_uid,
            tunnel_type=dco_class,
            mode=mode,
            conditional=conditional,
        )
        for inner_uid in term_refs[:-1]
    ]
