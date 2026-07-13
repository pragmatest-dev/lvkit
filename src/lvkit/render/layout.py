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
    # raw uids whose ``<label>`` child is hidden (objFlags bit 0x8) — i.e.
    # LabVIEW's "label visible" property is off for that element.
    hidden_labels: set[str] = field(default_factory=set)
    # Flat-sequence raw uid -> absolute x of each inter-frame boundary
    # (frames 1..N-1) — the film-strip dividers. Empty for every other
    # structure kind (stacked sequences overlap; nothing to divide).
    sequence_dividers: dict[str, list[float]] = field(default_factory=dict)
    # Constant raw uid -> the absolute rect of its developer-authored OWNED
    # LABEL (the ``partsList`` ``class="label"`` part). Kept apart from
    # node_bounds (which is the value box, label EXCLUDED — task #77); the
    # renderer draws the label text here only when the graph carries label
    # text (``ConstantNode.label``) for the uid.
    label_bounds: dict[str, Rect] = field(default_factory=dict)
    # Pair of rounded-int endpoint centers ((x1,y1),(x2,y2)) -> the signal's
    # ``compressedWireTable`` hex, for 2-endpoint signals only. Registered
    # under BOTH orderings. Keyed by center because both the graph side
    # (scene.py) and the heap signal resolve terminals through
    # ``terminal_centers``, making the resolved center the common key;
    # fan-out signals are intentionally NOT registered here (so they fall
    # back to the router). See task #84.
    wire_geometry: dict[tuple[tuple[int, int], tuple[int, int]], str] = field(
        default_factory=dict
    )
    # Fan-out (3+ endpoint) signals, pre-decoded because a tree can't be decoded
    # one branch at a time: (source center, one sink center) -> that branch's
    # intermediate bend points (absolute), ready for the same
    # ``_compress([src, *mid, dst])`` path the single-net case uses. Only the
    # source->sink ordering is registered (branches always run source->sink).
    # See task #84 / ``wire_table.decode_fanout``.
    fanout_geometry: dict[
        tuple[tuple[int, int], tuple[int, int]], list[tuple[float, float]]
    ] = field(default_factory=dict)
    # IDENTITY lookup: a wire's geometry keyed by its DESTINATION terminal uid
    # (the sink the branch reaches). The graph knows a drawn wire's exact
    # destination uid, and the heap signal lists that same uid — so this is an
    # exact match with no center rounding/tolerance. It recovers wires the
    # center-pair key misses when the graph's net-SOURCE terminal is a different
    # (co-located) uid than the heap signal's source (the two resolve to centers
    # ~a terminal-width apart, so the (src,dst) center pair never matches even
    # though the geometry decoded fine). ``wire_by_uid`` maps a 2-endpoint
    # signal's blob under BOTH its uids (direction is the caller's to supply);
    # ``fanout_by_uid`` maps each decoded branch under its sink uid. See #84/#76.
    wire_by_uid: dict[str, str] = field(default_factory=dict)
    fanout_by_uid: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    icon_png: Path | None = None

    def scene_bounds(self, pad: float = 30.0) -> Rect:
        """Bounding box over every known rect, padded — the SVG viewBox."""
        xs: list[float] = []
        ys: list[float] = []
        for x1, y1, x2, y2 in (
            *self.node_bounds.values(), *self.border_terminals.values(),
            *self.label_bounds.values(),
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


def _const_value_box(ddo: ET.Element) -> Rect | None:
    """The DRAWABLE box of a constant DDO, EXCLUDING its caption ``label``
    part.

    A constant DDO's own ``<bounds>`` is the bounding box of every part it
    owns — and when the developer gave the constant an inline caption, that
    caption (``partsList`` entry ``class="label"``, the free text beside the
    value) sits INSIDE those bounds and inflates them: a hex ``U32`` showing
    ``x2`` reports a 139x69 rect whose left ~120px is the caption region, with
    the real 19x19 value box (``cosm``/``numLabel`` parts) squeezed to the
    right (task #77). Using the raw DDO bounds draws a giant empty box and
    swallows the caption.

    The value box is the union of the DDO's NON-caption parts (their bounds are
    relative to the DDO's top-left origin). This can only ever match or SHRINK
    the DDO box — the excluded caption is the sole part that extends it — so a
    constant with no inline caption (the common case; its caption sits at a
    negative offset, already outside the DDO bounds) is unchanged. Returns None
    if the DDO has no usable part geometry, so callers fall back to the raw box.
    """
    box = _rect(ddo)
    parts = ddo.find("partsList")
    if box is None or parts is None:
        return box
    ox, oy = box[0], box[1]  # DDO origin; part bounds are relative to it
    rects = [
        r for p in parts.findall("SL__arrayElement")
        if p.get("class") != "label" and (r := _rect(p)) is not None
    ]
    if not rects:
        return box
    # Union of the non-caption parts, CLAMPED to the DDO box so this can only
    # ever SHRINK it (drop the caption region), never grow it — a caption-free
    # constant whose value part happens to overhang the frame by a pixel stays
    # byte-identical instead of being silently resized.
    return (
        max(box[0], ox + min(r[0] for r in rects)),
        max(box[1], oy + min(r[1] for r in rects)),
        min(box[2], ox + max(r[2] for r in rects)),
        min(box[3], oy + max(r[3] for r in rects)),
    )


def _const_label_box(ddo: ET.Element) -> Rect | None:
    """Absolute rect of a constant's caption (``partsList`` ``class="label"``
    part), or None. Its bounds are relative to the DDO origin, exactly like the
    value parts in :func:`_const_value_box`. Paired with the graph's caption
    TEXT (``ConstantNode.label``) to draw the free label at its real position."""
    box = _rect(ddo)
    lab = ddo.find("partsList/SL__arrayElement[@class='label']")
    if box is None or lab is None:
        return None
    r = _rect(lab)
    if r is None:
        return None
    ox, oy = box[0], box[1]
    return (ox + r[0], oy + r[1], ox + r[2], oy + r[3])


class _LayoutBuilder:
    def __init__(self) -> None:
        self.node_bounds: dict[str, Rect] = {}
        self.label_bounds: dict[str, Rect] = {}
        self.terminal_centers: dict[str, Point] = {}
        self.border_terminals: dict[str, Rect] = {}
        self.border_terminal_kind: dict[str, str] = {}
        self.structure_border_uids: dict[str, list[str]] = {}
        # raw uids of AUTO-INDEXING loop tunnels (array index/accumulate); a
        # last-value passthrough tunnel is absent from this set.
        self.indexing_tunnels: set[str] = set()
        # raw uids whose direct <label> child is hidden (objFlags bit 0x8).
        self.hidden_labels: set[str] = set()
        # flat-sequence raw uid -> inter-frame divider x-positions.
        self.sequence_dividers: dict[str, list[float]] = {}
        # (termList uids, compressedWireTable hex) for every heap signal,
        # collected raw during the walk; resolved to centers afterward by
        # ``_resolve_wire_geometry`` once ``terminal_centers`` is complete.
        self.raw_signals: list[tuple[list[str], str]] = []

    def _record_label_hidden(self, elem: ET.Element, uid: str | None) -> None:
        """Record uid whose ``<label>`` is hidden (objFlags bit 0x8), so the
        renderer can honor LabVIEW's 'label visible' property."""
        if not uid:
            return
        lbl = elem.find("label")
        if lbl is None:
            return
        try:
            flags = int((lbl.findtext("objFlags") or "0").strip())
        except ValueError:
            return
        if flags & 0x8:
            self.hidden_labels.add(uid)

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
                self._record_label_hidden(term, uid)
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
            # NO termBounds of its own — only its nested ddo carries a box. Use
            # the value-only box (drop the inline caption region — task #77).
            cb = _const_value_box(ddo) if ddo is not None else None
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
                    # A tunnel's GRAPH terminal uid can be a nested alias of
                    # this term (the dco's inner/outer termList uids) rather
                    # than term_uid itself — e.g. a stacked-sequence tunnel is
                    # modeled under uid 729 while the rect lives on term 704.
                    # Register the border rect under every aliased uid too
                    # (mirroring terminal_centers below), but only for a pure
                    # terminal (no attached constant), so the structure-border
                    # lookup by the graph's chosen uid finds geometry.
                    if cb is None:
                        for u in self._collect_uids(term):
                            self.node_bounds.setdefault(u, abs_tb)
            if cb is not None:
                abs_cb = (
                    ox + cb[0] + off_x, oy + cb[1] + off_y,
                    ox + cb[2] + off_x, oy + cb[3] + off_y,
                )
                if term_uid:
                    self.node_bounds[term_uid] = abs_cb
                    # Its caption (free label) rect, same offset frame as the
                    # value box — the renderer draws text here iff the graph
                    # carries caption text for this uid (task #77).
                    capb = _const_label_box(ddo) if ddo is not None else None
                    if capb is not None:
                        self.label_bounds[term_uid] = (
                            ox + capb[0] + off_x, oy + capb[1] + off_y,
                            ox + capb[2] + off_x, oy + capb[3] + off_y,
                        )
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
            abs_rect = (ox + tb[0], oy + tb[1], ox + tb[2], oy + tb[3])
            self.border_terminals.setdefault(uid, abs_rect)
            self.border_terminal_kind.setdefault(uid, _TAG_TO_GLYPH_KIND[tag])
            self.structure_border_uids.setdefault(structure_uid, []).append(uid)
            # A border DCO's WIREABLE terminal uid (its nested dco/term uids)
            # differs from the DCO's own uid; register the glyph center under
            # all of them — exactly as _map_terms does for ordinary terminals —
            # so a wire to e.g. the While-loop conditional (stop) or the case
            # selector anchors to the glyph instead of being dropped for want
            # of geometry.
            center = ((abs_rect[0] + abs_rect[2]) / 2,
                      (abs_rect[1] + abs_rect[3]) / 2)
            for u in self._collect_uids(dco):
                self.terminal_centers.setdefault(u, center)

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

        # Each diagram (root or a structure's inner frame) carries its own
        # signalList — the routed geometry for every wire whose endpoints
        # live in this diagram. Collect the raw (uids, blob) pairs now;
        # they're resolved to absolute centers once the whole heap has been
        # walked and terminal_centers is complete (see
        # ``_resolve_wire_geometry``).
        sl = diag.find("signalList")
        if sl is not None:
            for sig in sl.findall("SL__arrayElement"):
                if sig.get("class") != "signal":
                    continue
                tl = sig.find("termList")
                cw = sig.find("compressedWireTable")
                if tl is None or cw is None or not cw.text:
                    continue
                uids = [e.get("uid") for e in tl.findall("SL__arrayElement")]
                uids = [u for u in uids if u]
                self.raw_signals.append((uids, cw.text.strip()))

    def _resolve_wire_geometry(
        self,
    ) -> tuple[dict[tuple[tuple[int, int], tuple[int, int]], str], dict[str, str]]:
        """Resolve ``raw_signals`` into the center-keyed ``Layout.wire_geometry``
        map (both orderings) AND the identity-keyed ``Layout.wire_by_uid`` map
        (the blob under each of its two terminal uids). Only 2-endpoint signals
        are registered; fan-out and >2 signals are handled by
        ``_resolve_fanout_geometry``. The center map needs both centers resolved;
        the uid map does not (the caller supplies the drawn endpoints).
        """
        geo: dict[tuple[tuple[int, int], tuple[int, int]], str] = {}
        by_uid: dict[str, str] = {}
        for uids, blob in self.raw_signals:
            if len(uids) != 2:
                continue
            by_uid[uids[0]] = blob
            by_uid[uids[1]] = blob
            ca = self.terminal_centers.get(uids[0])
            cb = self.terminal_centers.get(uids[1])
            if ca is None or cb is None:
                continue
            ka = (int(round(ca[0])), int(round(ca[1])))
            kb = (int(round(cb[0])), int(round(cb[1])))
            geo[(ka, kb)] = blob
            geo[(kb, ka)] = blob
        return geo, by_uid

    def _resolve_fanout_geometry(
        self,
    ) -> tuple[
        dict[tuple[tuple[int, int], tuple[int, int]], list[tuple[float, float]]],
        dict[str, list[tuple[float, float]]],
    ]:
        """Decode 3+-endpoint ``raw_signals`` into per-branch bend polylines.

        A fan-out tree is decoded once (``decode_fanout`` needs the source and
        ALL sink centers to resolve fork directions), then each resulting branch
        is registered by (source center, that sink's center) so ``scene.py`` can
        look it up per drawn wire exactly like the single-net map. Signals with
        an unresolved terminal, or that don't cleanly decode, are skipped and
        fall back to the auto-router.
        """
        from .wire_table import decode_fanout

        geo: dict[
            tuple[tuple[int, int], tuple[int, int]], list[tuple[float, float]]
        ] = {}
        by_uid: dict[str, list[tuple[float, float]]] = {}
        for uids, blob in self.raw_signals:
            if len(uids) < 3:
                continue
            src = self.terminal_centers.get(uids[0])
            sink_uids = uids[1:]
            sinks = [self.terminal_centers.get(u) for u in sink_uids]
            if src is None or any(s is None for s in sinks):
                continue
            resolved = [s for s in sinks if s is not None]
            mids = decode_fanout(blob, src, resolved)
            if mids is None:
                continue
            ks = (int(round(src[0])), int(round(src[1])))
            for sink_uid, sink, mid in zip(sink_uids, resolved, mids):
                kd = (int(round(sink[0])), int(round(sink[1])))
                geo[(ks, kd)] = mid
                by_uid[sink_uid] = mid  # identity key: the branch's sink uid
        return geo, by_uid

    def _visit(self, elem: ET.Element, ox: float, oy: float) -> None:
        bb = _rect(elem)
        if bb is None:
            return
        ax1, ay1 = ox + bb[0], oy + bb[1]
        ax2, ay2 = ox + bb[2], oy + bb[3]

        uid = elem.get("uid")
        self._record_label_hidden(elem, uid)
        if uid:
            self.node_bounds.setdefault(uid, (ax1, ay1, ax2, ay2))
        # An sRN's own ``bounds`` is a translation for out-of-diagram terminal
        # REFERENCES, but its ``termList`` holds fPTerm/constants that are
        # diagram-level objects — each already carries diagram-relative bounds,
        # so map them from the diagram origin, not the sRN's translated corner
        # (otherwise a control inside a loop lands far to the upper-left).
        term_ox, term_oy = (ox, oy) if elem.get("class") == "sRN" else (ax1, ay1)
        self._map_terms(elem, term_ox, term_oy)
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
        # its own diagramList.
        #
        # Stacked sequence: frames overlap at one spot (you flip through
        # them) — matching the prior renderer, every frame is walked at the
        # sequence's own origin.
        #
        # Flat sequence: frames sit side by side (a film strip). Each
        # ``<sequenceFrame>`` carries its own ``<bounds>`` whose ``left``/
        # ``top`` are the frame's absolute heap position — the per-frame
        # x/y offset relative to frame 0 is real recorded data, not a
        # guess. Tile each frame's diagram by that offset and record the
        # inter-frame boundaries as film-strip dividers.
        seqlist = elem.find("sequenceList")
        if seqlist is None:
            return
        is_flat = elem.get("class") == "flatSequence"
        frames = seqlist.findall("SL__arrayElement")
        frame0_rect = _rect(frames[0]) if is_flat and frames else None
        dividers: list[float] = []
        for i, frame in enumerate(frames):
            dx = dy = 0.0
            if is_flat and frame0_rect is not None:
                frect = _rect(frame)
                if frect is not None:
                    dx = frect[0] - frame0_rect[0]
                    dy = frect[1] - frame0_rect[1]
                    if i > 0:
                        dividers.append(ax1 + dx)
            # A sequence frame's tunnels (seqTun/flatSeqTun) live in the
            # ``sequenceFrame``'s OWN termList — not the sequence element's
            # (which has none) — so map them here with the frame offset.
            # Without this the structure's border tunnels get no geometry and
            # never render, and every wire through them is dropped.
            self._map_terms(frame, ax1 + dx, ay1 + dy)
            fdl = frame.find("diagramList")
            if fdl is None:
                continue
            for d in fdl.findall("SL__arrayElement"):
                if d.get("class") == "diag":
                    self.walk(d, ax1 + dx, ay1 + dy)
        if is_flat and dividers and uid:
            self.sequence_dividers.setdefault(uid, []).extend(dividers)


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
    wire_geometry, wire_by_uid = builder._resolve_wire_geometry()
    fanout_geometry, fanout_by_uid = builder._resolve_fanout_geometry()
    return Layout(
        node_bounds=builder.node_bounds,
        terminal_centers=builder.terminal_centers,
        border_terminals=builder.border_terminals,
        border_terminal_kind=builder.border_terminal_kind,
        structure_border_uids=builder.structure_border_uids,
        indexing_tunnels=builder.indexing_tunnels,
        hidden_labels=builder.hidden_labels,
        sequence_dividers=builder.sequence_dividers,
        label_bounds=builder.label_bounds,
        wire_geometry=wire_geometry,
        fanout_geometry=fanout_geometry,
        wire_by_uid=wire_by_uid,
        fanout_by_uid=fanout_by_uid,
        icon_png=icon if icon.exists() else None,
    )
