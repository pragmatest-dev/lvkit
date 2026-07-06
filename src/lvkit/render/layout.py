"""Heap XML → pure geometry.

lvkit's normal parser discards block-diagram node geometry (it keeps only
front-panel control bounds). Faithful rendering needs that geometry, so this
is a separate, render-only pass over the same ``_BDHb.xml`` heap that
``extractor.extract_vi_xml`` already produces. It never touches code
generation.

This module supplies GEOMETRY ONLY — positions the parser discards. It knows
nothing about node kinds, primitive names, or wire connectivity; that
semantic information already lives in the graph (``InMemoryVIGraph``). The
scene layer (``render/scene.py``) joins the two by UID.

All coordinates are absolute LabVIEW pixels; every dict is keyed by the RAW
(unqualified) heap uid — the graph's qualified ids are ``"{vi}::{heapUID}"``
(see ``graph/core.py::_qid``), so callers strip the ``"{vi}::"`` prefix
before looking up geometry.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..extractor import extract_vi_xml

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x1, y1, x2, y2

# Structure border DCOs that live OUTSIDE their owning node's termList (loop
# count/index/test terminals, case selector) — each has its own uid + bounds,
# but the graph doesn't model them as full Terminal objects. Geometry only.
_BORDER_DCO_TAGS = ("loopIndexDCO", "loopLimitDCO", "loopTestDCO", "caseSelDCO")

# Fixed tag -> glyph-kind mapping. This is geometry-side decoration (same
# category as the comment/free-label pass DESIGN.md already permits): the
# DCO's on-diagram glyph meaning is a fixed function of which heap tag it
# is, never re-derived dataflow semantics. The scene layer separately
# decides (from the graph) whether/where each kind actually needs drawing
# -- see render/scene.py's structure-type-guaranteed border glyphs.
_TAG_TO_GLYPH_KIND = {
    "loopIndexDCO": "i",
    "loopLimitDCO": "N",
    "loopTestDCO": "cond",
    "caseSelDCO": "selector",
}


@dataclass(frozen=True)
class Layout:
    """Pure geometry extracted from a VI's heap XML — no semantics.

    node_bounds: every diagram element's bounding rect (primitives, SubVI
        calls, structures, constants, front-panel terminal placements) keyed
        by its own raw heap uid.
    terminal_centers: every terminal's connection point (tunnel outer/inner,
        sRN terminals, front-panel terminal centers) keyed by raw heap uid —
        this is what wires anchor to.
    border_terminals: structure border DCO rects (loop N/i/cond, case
        selector) keyed by their own raw heap uid. These aren't modeled as
        full graph Terminals, so they're kept separate from node_bounds.
    border_terminal_kind: raw border-terminal uid -> fixed glyph kind
        ("i"/"N"/"cond"/"selector"), a pure function of which heap DCO tag
        produced the entry. Geometry-side decoration only — see
        ``_TAG_TO_GLYPH_KIND``.
    icon_png: the VI's extracted connector-pane icon, if present.
    """

    node_bounds: dict[str, Rect] = field(default_factory=dict)
    terminal_centers: dict[str, Point] = field(default_factory=dict)
    border_terminals: dict[str, Rect] = field(default_factory=dict)
    border_terminal_kind: dict[str, str] = field(default_factory=dict)
    # Structure raw uid -> the raw uids of its border_terminals entries
    # (loop N/i/cond, case selector) — pure containment, no glyph semantics.
    structure_border_uids: dict[str, list[str]] = field(default_factory=dict)
    # raw uids belonging to AUTO-INDEXING tunnels (vs last-value passthroughs).
    indexing_tunnels: set[str] = field(default_factory=set)
    icon_png: Path | None = None

    def scene_bounds(self, pad: float = 30.0) -> Rect:
        """Bounding box over every known rect, padded — the SVG viewBox."""
        xs: list[float] = []
        ys: list[float] = []
        for x1, y1, x2, y2 in (
            *self.node_bounds.values(), *self.border_terminals.values(),
        ):
            xs += [x1, x2]
            ys += [y1, y2]
        if not xs:
            return (0.0, 0.0, 100.0, 100.0)
        return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _rect(elem: ET.Element, tag: str = "bounds") -> Rect | None:
    """Parse a LabVIEW ``(top, left, bottom, right)`` rect element."""
    b = elem.find(tag)
    if b is None or not b.text:
        return None
    t, left, btm, r = (int(x) for x in b.text.strip("()").split(","))
    return float(left), float(t), float(r), float(btm)  # x1, y1, x2, y2


class _LayoutBuilder:
    def __init__(self) -> None:
        self.node_bounds: dict[str, Rect] = {}
        self.terminal_centers: dict[str, Point] = {}
        self.border_terminals: dict[str, Rect] = {}
        self.border_terminal_kind: dict[str, str] = {}
        self.structure_border_uids: dict[str, list[str]] = {}
        # raw uids of AUTO-INDEXING loop tunnels (array index/accumulate); a
        # last-value passthrough tunnel is absent from this set.
        self.indexing_tunnels: set[str] = set()

    def _detect_tunnel_modes(self, elem: ET.Element) -> None:
        """Record which lpTun tunnels are auto-indexing vs last-value.

        LabVIEW marks an auto-indexing tunnel with a ``TunnelType`` (and an
        ``innerLpTunDCO``); a plain last-value passthrough has neither.
        """
        tl = elem.find("termList")
        if tl is None:
            return
        for term in tl.findall("SL__arrayElement"):
            dco = term.find("dco")
            src = dco if dco is not None and dco.get("class") == "lpTun" else (
                term if term.get("class") == "lpTun" else None
            )
            if src is None:
                continue
            tt = (src.findtext("TunnelType") or "").strip()
            indexing = src.find("innerLpTunDCO") is not None or (
                tt not in ("", "00", "0")
            )
            if indexing:
                self.indexing_tunnels.update(self._collect_uids(term))

    # -- uid collection -------------------------------------------------
    @staticmethod
    def _collect_uids(elem: ET.Element) -> set[str]:
        """All uids that should resolve to the same terminal center.

        A terminal's outer ``term`` uid, its ``dco`` uid, and any nested
        termList entries (sRN-owned inner terminals) are interchangeable
        wire-endpoint references in the heap XML.
        """
        uids: set[str] = set()
        if elem.get("uid"):
            uids.add(elem.get("uid", ""))
        dco = elem.find("dco")
        if dco is not None and dco.get("uid"):
            uids.add(dco.get("uid", ""))
        for s in elem.findall(".//termList/SL__arrayElement"):
            if s.get("uid"):
                uids.add(s.get("uid", ""))
        return {u for u in uids if u}

    # -- per-node terminal geometry ---------------------------------------
    def _map_terms(self, elem: ET.Element, ox: float, oy: float) -> None:
        """Record terminal centers (+ nested constant boxes) from one
        node's termList, in absolute coordinates."""
        tl = elem.find("termList")
        if tl is None:
            return

        # A primitive's termBounds are relative to its ICON, and the icon is
        # CENTERED within the node's clickable bounds — not top-left-aligned.
        # Compute the centering offset that maps termBounds-space into the
        # node's absolute bounds-space. Only applies to `class="prim"` nodes;
        # front-panel terminals (fPTerm) and other node kinds are unaffected.
        off_x = off_y = 0.0
        if elem.get("class") == "prim":
            nb = _rect(elem)
            trects = [
                r for r in (
                    _rect(t, ".//termBounds")
                    for t in tl.findall("SL__arrayElement")
                    if t.get("class") == "term"
                ) if r is not None
            ]
            if nb is not None and trects:
                emin_x = min(r[0] for r in trects)
                emin_y = min(r[1] for r in trects)
                emax_x = max(r[2] for r in trects)
                emax_y = max(r[3] for r in trects)
                off_x = (nb[2] - nb[0] - (emax_x - emin_x)) / 2 - emin_x
                off_y = (nb[3] - nb[1] - (emax_y - emin_y)) / 2 - emin_y

        for term in tl.findall("SL__arrayElement"):
            cls = term.get("class")
            if cls == "fPTerm":
                b = _rect(term)
                if b is None:
                    continue
                uid = term.get("uid")
                abs_rect = (ox + b[0], oy + b[1], ox + b[2], oy + b[3])
                if uid:
                    self.node_bounds.setdefault(uid, abs_rect)
                    self.terminal_centers.setdefault(
                        uid, ((abs_rect[0] + abs_rect[2]) / 2,
                              (abs_rect[1] + abs_rect[3]) / 2),
                    )
                continue
            if cls != "term":
                continue
            term_uid = term.get("uid")
            # termBounds is nested at varying depth (directly under a "term",
            # or under its dco/parm/overridableParm child) — search any depth.
            tb = _rect(term, ".//termBounds")
            ddo = term.find(".//ddo")
            # A constant attached directly to this terminal (bDConstDCO) has
            # NO termBounds of its own — only its nested ddo carries a box.
            cb = _rect(ddo) if ddo is not None else None
            if tb is None and cb is None:
                continue

            if tb is not None:
                abs_tb = (
                    ox + tb[0] + off_x, oy + tb[1] + off_y,
                    ox + tb[2] + off_x, oy + tb[3] + off_y,
                )
                cx, cy = (abs_tb[0] + abs_tb[2]) / 2, (abs_tb[1] + abs_tb[3]) / 2
                if term_uid:
                    # The terminal's own border-box rect (used for N/SR/
                    # tunnel/selector glyphs). A nested constant's ddo box,
                    # if present, is more specific and overrides it below.
                    self.node_bounds.setdefault(term_uid, abs_tb)
            if cb is not None:
                abs_cb = (
                    ox + cb[0] + off_x, oy + cb[1] + off_y,
                    ox + cb[2] + off_x, oy + cb[3] + off_y,
                )
                if term_uid:
                    self.node_bounds[term_uid] = abs_cb
                cx = (abs_cb[0] + abs_cb[2]) / 2
                cy = (abs_cb[1] + abs_cb[3]) / 2
            for u in self._collect_uids(term):
                self.terminal_centers.setdefault(u, (cx, cy))

    # -- structure border DCOs (loop N/i/cond, case selector) --------------
    def _border_dcos(
        self, struct: ET.Element, ox: float, oy: float, structure_uid: str,
    ) -> None:
        for tag in _BORDER_DCO_TAGS:
            dco = struct.find(tag)
            if dco is None:
                continue
            uid = dco.get("uid")
            tb = _rect(dco, "termBounds")
            if not uid or tb is None:
                continue
            self.border_terminals.setdefault(
                uid, (ox + tb[0], oy + tb[1], ox + tb[2], oy + tb[3]),
            )
            self.border_terminal_kind.setdefault(uid, _TAG_TO_GLYPH_KIND[tag])
            self.structure_border_uids.setdefault(structure_uid, []).append(uid)

    # -- recursive walk -----------------------------------------------------
    def walk(self, diag: ET.Element, ox: float, oy: float) -> None:
        zp = diag.find("zPlaneList")
        if zp is not None:
            for elem in zp.findall("SL__arrayElement"):
                self._visit(elem, ox, oy)

        # sRN (shift-register / border-terminal group) nodes live in
        # nodeList, not zPlaneList — but they carry their own ``bounds``
        # (a translation back to this diagram's absolute origin, since
        # they hold references to terminals whose visual glyph lives
        # outside this diagram, e.g. a loop's N/i border box) and a
        # termList of front-panel terminals / standalone constants that
        # DO need offset-aware geometry. Visit them the same way.
        nl = diag.find("nodeList")
        if nl is not None:
            for elem in nl.findall("SL__arrayElement"):
                if elem.get("class") == "sRN":
                    self._visit(elem, ox, oy)

    def _visit(self, elem: ET.Element, ox: float, oy: float) -> None:
        bb = _rect(elem)
        if bb is None:
            return
        ax1, ay1 = ox + bb[0], oy + bb[1]
        ax2, ay2 = ox + bb[2], oy + bb[3]

        uid = elem.get("uid")
        if uid:
            self.node_bounds.setdefault(uid, (ax1, ay1, ax2, ay2))
        self._map_terms(elem, ax1, ay1)
        self._detect_tunnel_modes(elem)
        if uid:
            self._border_dcos(elem, ax1, ay1, uid)

        dlist = elem.find("diagramList")
        inner = (
            [d for d in dlist.findall("SL__arrayElement")
             if d.get("class") == "diag"]
            if dlist is not None
            else []
        )
        if inner:
            for d in inner:
                self.walk(d, ax1, ay1)
            return

        # Flat/stacked sequence: frames live under sequenceList, each with
        # its own diagramList. LabVIEW doesn't record a per-frame absolute
        # offset in this element, so (matching the prior renderer) every
        # frame is walked at the sequence's own origin.
        seqlist = elem.find("sequenceList")
        if seqlist is not None:
            for frame in seqlist.findall("SL__arrayElement"):
                fdl = frame.find("diagramList")
                if fdl is None:
                    continue
                for d in fdl.findall("SL__arrayElement"):
                    if d.get("class") == "diag":
                        self.walk(d, ax1, ay1)


def build_layout(vi_or_bd: Path) -> Layout:
    """Build a ``Layout`` from a ``.vi`` file or a ``_BDHb.xml`` heap path."""
    if vi_or_bd.suffix.lower() == ".vi":
        bd_path, _, _ = extract_vi_xml(vi_or_bd)
        bd = Path(bd_path)
    else:
        bd = vi_or_bd
    root_elem = ET.parse(bd).getroot()
    root = root_elem.find("root")
    if root is None:
        root = root_elem

    builder = _LayoutBuilder()
    builder.walk(root, 0.0, 0.0)

    icon = bd.parent / f"{bd.stem.replace('_BDHb', '')}_ICON.png"
    return Layout(
        node_bounds=builder.node_bounds,
        terminal_centers=builder.terminal_centers,
        border_terminals=builder.border_terminals,
        border_terminal_kind=builder.border_terminal_kind,
        structure_border_uids=builder.structure_border_uids,
        indexing_tunnels=builder.indexing_tunnels,
        icon_png=icon if icon.exists() else None,
    )
