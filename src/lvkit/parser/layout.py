"""Heap XML → pure geometry.

lvkit's semantic parse (``parse_vi``) keeps node kinds, wires, and types but
discards block-diagram node geometry (it keeps only front-panel control
bounds). Faithful rendering needs that geometry, so this is the geometry half
of the parser: it decodes the same ``_BDHb.xml`` heap element the semantic
parse already reads, and ``parse_vi(..., layout=True)`` runs it on that SAME
parsed root (one read — no second ``ET.parse``). It never touches code
generation, which parses with ``layout=False`` and pays nothing.

Living in ``parser/`` keeps the XML abstraction in one place: render consumes a
``Layout`` (via the graph) and never reads heap XML itself.

This module supplies GEOMETRY ONLY — positions the semantic parse discards. It
knows nothing about node kinds, primitive names, or wire connectivity; that
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
from .image_resources import carve_png, decode_picc_points, resources_for_heap

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x1, y1, x2, y2


def bundle_aggregate_columns(box: Rect, node: Rect) -> tuple[Rect, Rect]:
    """The two cluster columns of a Bundle-By-Name, from its shared aggregate
    ``box`` (the one the OUTPUT owns) and the ``node`` bounds.

    Returns ``(interior_input_column, edge_output_column)``. The output keeps its
    own ``box`` — the EDGE column, flush to whichever node side it lines (right
    for the usual right-hand aggregate). The INPUT — which the heap gives no box
    of its own — takes the equal-width column immediately on the field-facing
    side of that box (the empty band the heap leaves between the field cells and
    the box). The single source of this rule: ``layout`` anchors the input
    wire's endpoint to the interior column's top before wire decode, and
    ``scene`` gives the two terminals these boxes for the glyph/hover panel — so
    both agree by construction rather than by two hand-kept copies."""
    ax1, ay1, ax2, ay2 = box
    nx1, _, nx2, _ = node
    w = ax2 - ax1
    if (nx2 - ax2) <= (ax1 - nx1):  # box lines the node's RIGHT edge
        interior = (ax1 - w, ay1, ax1, ay2)
    else:  # left-hand aggregate: interior column on the box's right
        interior = (ax2, ay1, ax2 + w, ay2)
    return interior, box

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
class LayoutDecoration:
    """A block-diagram decoration — LabVIEW's ``class="cosm"`` shape (Flat Frame,
    Thin/Thick Line, Thin/Thick Line with Arrow, or an embedded picture), OR a
    ``class="attachment"`` label-to-object leader/pushpin (``is_attachment``).
    PURE VISUAL: no data, no dataflow, never a graph node. ``image_res_id``
    selects the shape kind (see render/glyphs/decorations) when POSITIVE it
    instead names a DSIM picture section — see ``Layout.images``;
    ``container_uid`` is the raw uid of the structure whose frame diagram this
    decoration lives in (None at the root diagram), so it paints inside that
    structure. Its bounds + paint rank are looked up from
    ``Layout.node_bounds``/``z_order`` by ``uid`` like any other element."""

    uid: str
    image_res_id: str
    bg_color: str | None = None
    container_uid: str | None = None
    # Absolute (already walk-offset) endpoints decoded from the heap's
    # ImageInternalsResID PICC section, when present -- see
    # image_resources.decode_picc_points. ``()`` when no PICC decoded (a plain
    # cosm shape with no internals, or resolution failed) -- glyphs fall back
    # to computing endpoints from `bounds` (line_endpoints). points[0] is the
    # tail, points[-1] the head (see labview-binary-format.md).
    points: tuple[Point, ...] = ()
    # True for a ``class="attachment"`` heap element (a label leader/pushpin),
    # False for a ``class="cosm"`` decoration. An attachment with no decoded
    # `points` has no drawable geometry (see render/glyphs/decorations/factory)
    # and is dropped rather than drawn as a placeholder box.
    is_attachment: bool = False
    # The uid an attachment's ``<attachedObject uid="..."/>`` child names --
    # the node this leader points AT. None for a plain cosm shape, or an
    # attachment with no recorded target (see labview-binary-format.md).
    attached_uid: str | None = None


@dataclass(frozen=True)
class ClusterFieldGeom:
    """One field's REAL geometry inside its owning cluster, decoded from the
    heap's own front-panel-editor layout (the cluster's ``paneHierarchy``/
    ``zPlaneList``) — see ``_cluster_field_geoms``.

    ``value_rect``/``label_rect`` are relative to the CLUSTER's own (0, 0)
    origin, at the cluster's NATIVE size (``ClusterGeom.width``/``height``) —
    not yet placed on the diagram. ``label_rect`` is None when the caption is
    hidden (objFlags bit 0x8) or the field carries none. A field whose own
    value is itself a cluster (``class="stdClust"``) carries that cluster's
    full geometry in ``nested`` (None for a non-cluster field).

    ``refnum_expanded`` is True when this field is a data-typed
    ``stdRefNum`` whose type-display background selects the EXPANDED image
    variant (heap-verified: its ``multiCosm`` carries ``<index>1</index>`` —
    see ``_refnum_type_display_expanded``); False for a compact refnum or
    any non-refnum field. The renderer uses this to decide whether to draw
    the registered payload's TYPE filling the box (never as a nested VALUE
    cluster, which ``nested`` is reserved for) — see ``refnum_payload`` for
    WHERE, at real heap geometry, that payload actually goes.

    ``refnum_payload`` is set only for a data-typed ``stdRefNum`` field whose
    registered payload is itself a CLUSTER with decodable heap geometry (see
    ``_refnum_payload_layout``) — the payload's own real per-field elements
    and their real placement within THIS field's box, straight from the
    heap's ``<ddo class="stdClust">`` (a DIRECT child of the refnum ddo, a
    sibling of its own ``partsList`` — never a part of it, and never the
    genuinely-nested-cluster-FIELD case ``nested`` already covers)."""

    name: str
    value_rect: Rect
    label_rect: Rect | None
    nested: ClusterGeom | None = None
    refnum_expanded: bool = False
    refnum_payload: RefnumPayload | None = None


@dataclass(frozen=True)
class ClusterGeom:
    """A cluster's real geometry: its own natural (``width``, ``height``) —
    the heap's real value-box size — and each field's rect relative to that
    box's (0, 0) origin (see ``ClusterFieldGeom``).

    Draw it at any real target box via a SINGLE uniform scale (never a
    per-axis stretch): ``s = min(box_w / width, box_h / height)``, then a
    field's on-screen rect is ``(box_x1 + s*x1, box_y1 + s*y1, box_x1 + s*x2,
    box_y1 + s*y2)``. At the top level (a cluster constant drawn at its own
    real heap box) ``s`` is always 1.0 — pure translation; the scale only
    does real work for a NESTED field (whose assigned box is the parent's own
    uniformly-scaled ``value_rect``) or an array-of-clusters element (whose
    assigned box is the array glyph's fixed per-row cell)."""

    width: float
    height: float
    fields: tuple[ClusterFieldGeom, ...]


@dataclass(frozen=True)
class RefnumPayload:
    """Where a data-typed refnum's registered CLUSTER payload sits, and its
    own real per-field geometry — see ``_refnum_payload_layout``.

    ``offset`` is the payload's rect expressed as FRACTIONS (0..1) of the
    refnum's OWN native (unscaled) box — not an absolute pixel rect — so it
    survives any later uniform rescale of the refnum's drawn box (a nested
    field, an array-of-refnums element, …) EXACTLY: every step in this
    codebase composes rescales uniformly (never a per-axis stretch), so a
    field's fraction-of-its-own-box position is preserved algebraically
    identical to the heap's real absolute offset at every composed scale.
    ``geom`` is the payload's own self-contained ``ClusterGeom`` (relative to
    ITS OWN (0, 0) origin, at its native size — the SAME contract as any
    other ``ClusterGeom``)."""

    offset: Rect
    geom: ClusterGeom


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
    # A drawn wire's faithful geometry keyed by its DESTINATION terminal uid
    # (the sink the branch reaches). The graph knows a wire's exact destination
    # uid and the heap signal lists that same uid, so this is an EXACT match with
    # no center rounding or proximity tolerance. Each signal — 2-endpoint or
    # fan-out alike — is decoded once by ``wire_table.decode_signal`` and every
    # branch is stored under its sink uid: the branch's intermediate bend points
    # (absolute), ready for ``_compress([src, *mid, dst])`` in scene.py. A signal
    # that doesn't decode exactly is absent (→ auto-router). See task #84 / #76.
    wire_by_uid: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    # Z-ORDER paint rank per raw uid: the position at which the diagram walk
    # first saw the element, assigned as a single monotonic DFS counter over
    # ``zPlaneList`` (then ``nodeList``) at every nesting level. LabVIEW paints
    # ``zPlaneList`` back-to-front, so a LOWER rank draws first (further back)
    # and a later sibling (higher rank) occludes it. This is the ONLY carrier
    # of paint order — a pure RENDER concern (never enters the graph, which
    # holds containment). The render tree sorts a container's children (from
    # graph containment) by this rank. Absent uids fall back to node order.
    z_order: dict[str, int] = field(default_factory=dict)
    # Paint rank per WIRE, keyed by its SOURCE terminal uid — the position of the
    # signal in its diagram's ``signalList`` (LabVIEW's separate z-list for
    # wires; nodes/terms are in ``zPlaneList``, wires are NOT). Same convention
    # as ``z_order``: a LOWER rank draws first (further back). The render sorts a
    # container's wire nets by this so crossings paint in LabVIEW's order.
    wire_z: dict[str, int] = field(default_factory=dict)
    # Block-diagram decorations (cosm shapes) — visual only, drawn z-ordered with
    # nodes (never graph nodes). Bounds/rank come from node_bounds/z_order by uid.
    decorations: list[LayoutDecoration] = field(default_factory=list)
    # Embedded-picture PNG bytes (carved from a DSIM resource section by
    # ``image_resources.carve_png``), keyed by the owning decoration's raw uid.
    # Only present for a decoration whose ``image_res_id`` is a POSITIVE DSIM
    # section index that resolved to a real PNG (issue #82).
    images: dict[str, bytes] = field(default_factory=dict)
    icon_png: Path | None = None
    # A cluster-constant's raw uid -> its REAL heap geometry (see
    # ClusterGeom), for the glyph to draw each field at its actual value/
    # label rect instead of a uniform-row stretch. Present only when the
    # constant's ddo resolves to a cluster shape (``_cluster_shape``) that
    # carries a decodable ``paneHierarchy`` — absent (empty) for anything
    # else, and the glyph falls back to its own small-box/uniform-row draw.
    cluster_field_geom: dict[str, ClusterGeom] = field(default_factory=dict)
    # An array CONSTANT's raw uid -> its ELEMENT type's real cluster geometry
    # (same ClusterGeom, at the element's own natural size), when the array's
    # element is a cluster (``ArrayConstantGlyph`` draws every visible row at
    # this fixed real size — arrays are homogeneous, so one shape serves every
    # row). Absent for a non-cluster element or one that can't be resolved.
    array_element_cluster: dict[str, ClusterGeom] = field(default_factory=dict)
    # Raw uids of TOP-LEVEL (not cluster-field) data-typed ``stdRefNum``
    # constants whose type-display is EXPANDED (see
    # ``_refnum_type_display_expanded``) — the same signal
    # ``ClusterFieldGeom.refnum_expanded`` carries for a cluster FIELD, kept
    # separately here since a bare refnum constant has no ``ClusterFieldGeom``
    # of its own to carry it on.
    refnum_expanded: set[str] = field(default_factory=set)
    # Raw uids of TOP-LEVEL (not cluster-field) data-typed ``stdRefNum``
    # constants whose registered payload is a CLUSTER with decodable heap
    # geometry -> that payload's real placement + per-field geometry (see
    # ``RefnumPayload`` / ``_refnum_payload_layout``) — the same data
    # ``ClusterFieldGeom.refnum_payload`` carries for a cluster FIELD, kept
    # separately here for the same reason ``refnum_expanded`` is.
    refnum_payload: dict[str, RefnumPayload] = field(default_factory=dict)

    def scene_bounds(self, pad: float = 30.0) -> Rect:
        """Bounding box over every known rect, padded — the SVG viewBox."""
        xs: list[float] = []
        ys: list[float] = []
        for x1, y1, x2, y2 in (
            *self.node_bounds.values(),
            *self.border_terminals.values(),
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
        r
        for p in parts.findall("SL__arrayElement")
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


def _fp_label_box(term: ET.Element) -> Rect | None:
    """Relative rect of an fPTerm's on-diagram label — its direct ``<label>``
    child's ``<bounds>``, positioned relative to the terminal's OWN origin (like
    a constant caption in :func:`_const_label_box`, but an fPTerm carries the
    label as a direct child, not a ``partsList`` part). This is the
    DEVELOPER-PLACED position (above OR below the terminal — see the corpus:
    some controls label below), so the renderer honors it instead of a fixed
    'always above, centered' offset that collides when terminals are close."""
    lab = term.find("label")
    if lab is None or lab.get("class") != "label":
        return None
    return _rect(lab)


def _field_name(field_el: ET.Element) -> str | None:
    """A cluster field's own name — its caption's ``textRec/text`` — or None
    when the field carries no caption part to read a name from at all (a
    field with no name can't be joined to the graph's ``ClusterField.name``,
    so callers skip it)."""
    lab = field_el.find("partsList/SL__arrayElement[@class='label']")
    if lab is None:
        return None
    text = lab.findtext("textRec/text")
    return text.strip('"') if text else None


def _field_label_hidden(field_el: ET.Element) -> bool:
    """True when a cluster field's caption is hidden (objFlags bit 0x8,
    mirroring ``_LayoutBuilder._record_label_hidden``) or the field carries no
    caption part at all — either way, nothing to draw a label rect for."""
    lab = field_el.find("partsList/SL__arrayElement[@class='label']")
    if lab is None:
        return True
    try:
        flags = int((lab.findtext("objFlags") or "0").strip())
    except ValueError:
        return False
    return bool(flags & 0x8)


# Front-panel/block-diagram ddo classes whose control shape is an array —
# mirrors ``parser.fp_heap_type._ARRAY_CLASSES`` (a block-diagram array
# CONSTANT's ddo is verified ``class="indArr"`` on the corpus — the same
# class an array-typed FIELD/indicator uses; ``stdArray`` is that module's
# established name for the same control shape, kept here for the same
# reason, unverified in this corpus but not a new guess).
_ARRAY_DDO_CLASSES = frozenset({"indArr", "stdArray"})


def _cluster_shape(el: ET.Element | None) -> ET.Element | None:
    """The ``class="stdClust"`` element that defines ``el``'s cluster shape,
    or None when ``el`` isn't a cluster at all.

    ``el`` itself when it's directly ``class="stdClust"``. Otherwise (a
    cluster used as a named ``.ctl`` typedef control — the ddo's own class is
    ``"typeDef"``) the cluster shape is embedded as a ``stdClust`` PART of
    the typedef ddo's own ``partsList`` (verified: a typedef-wrapped cluster
    constant's ``partsList`` carries exactly one such part, alongside its
    caption/border chrome) — identified by carrying its own
    ``paneHierarchy``, not by name/position."""
    if el is None:
        return None
    if el.get("class") == "stdClust":
        return el
    parts = el.find("partsList")
    if parts is None:
        return None
    for p in parts.findall("SL__arrayElement"):
        if p.get("class") == "stdClust" and p.find("paneHierarchy") is not None:
            return p
    return None


def _refnum_type_display_expanded(ddo: ET.Element) -> bool:
    """True when a data-typed ``stdRefNum`` control's REAL heap state is
    "expanded" — LabVIEW draws its registered payload TYPE inline, filling
    a large box — rather than "compact" — an icon plus a small type badge.

    A data-typed refnum (queue / notifier / user event / …) carries a
    nested ``<ddo>`` CHILD recording its registered payload's TYPE (a
    ``stdString``/``stdClust``/etc — a SIBLING of the refnum's own
    ``<partsList>``, never a part of it), and a ``partsList`` ``multiCosm``
    that draws the type-display's own background, selecting one of two
    recorded background images via its own ``<index>``. Verified on 5
    stdRefNum instances in one VI (same corpus this module already cites):
    every EXPANDED one (``ResultChangedRef`` h=206, ``SuiteChangedRef``
    h=83) has ``<index>1</index>`` on that multiCosm; every COMPACT one
    (``AbortEventRef``/``ExitEventReference``/``TextStream``, h=48 each)
    omits ``<index>`` entirely (LabVIEW's own default, 0). This is
    LabVIEW's OWN recorded display-state bit for the control, not a size
    threshold or a label/ImageResID match — the field's real ``<bounds>``
    height independently agrees with it on every verified instance, but
    this reads the authoritative signal directly rather than inferring it
    from size.

    False for a plain untyped refnum (no nested ``<ddo>`` at all — already
    compact, nothing to distinguish) or anything that isn't a
    ``stdRefNum``."""
    if ddo.get("class") != "stdRefNum" or ddo.find("ddo") is None:
        return False
    parts = ddo.find("partsList")
    if parts is None:
        return False
    for p in parts.findall("SL__arrayElement"):
        if p.get("class") == "multiCosm":
            idx = p.findtext("index")
            return idx is not None and idx.strip() == "1"
    return False


def _refnum_payload_layout(ddo: ET.Element) -> RefnumPayload | None:
    """A data-typed ``stdRefNum``'s registered CLUSTER payload's real
    placement + geometry, both straight from the heap — see ``RefnumPayload``
    for the coordinate contract.

    The payload ddo (a DIRECT ``<ddo>`` CHILD of the refnum — a sibling of
    its own ``<partsList>``, never a part of it, and never the
    genuinely-nested-cluster-FIELD case ``_cluster_shape(field_el)`` covers)
    carries its own ``<bounds>`` relative to the REFNUM's own raw-bounds
    origin — the SAME "relative to the owning ddo's top-left" convention
    every ``partsList`` part uses (see ``_const_value_box``), verified on
    GTR's "ResultChangedRef" (nested ``<bounds>(5, 31, 201, 106)`` inside a
    ``(521, 399, 727, 510)`` field — a 75x196 payload box at local offset
    (31, 5) inside a 111x206 field).

    None when ``ddo`` isn't a ``stdRefNum``, carries no nested ``<ddo>``, that
    nested control isn't a cluster shape (``_cluster_shape``), or either
    level's geometry can't be decoded (no ``paneHierarchy``, degenerate
    extent, …) — callers then fall back to the COMPACT type-terminal
    display, never an invented layout."""
    if ddo.get("class") != "stdRefNum":
        return None
    nested = ddo.find("ddo")
    if nested is None:
        return None
    shape = _cluster_shape(nested)
    if shape is None:
        return None
    geom = _cluster_field_geoms(shape)
    if geom is None:
        return None
    field_raw = _rect(ddo)
    nested_local = _rect(nested)
    if field_raw is None or nested_local is None:
        return None
    fw, fh = field_raw[2] - field_raw[0], field_raw[3] - field_raw[1]
    if fw <= 0 or fh <= 0:
        return None
    offset = (
        nested_local[0] / fw,
        nested_local[1] / fh,
        nested_local[2] / fw,
        nested_local[3] / fh,
    )
    return RefnumPayload(offset=offset, geom=geom)


def _cluster_field_geoms(cluster_el: ET.Element) -> ClusterGeom | None:
    """A cluster's real geometry, decoded from its own ``paneHierarchy``/
    ``zPlaneList`` — see ``ClusterGeom`` for the coordinate contract (fields
    are relative to the cluster's own (0, 0) origin at its NATIVE size;
    callers fit that into wherever they actually draw it).

    The field rects live in the typedef front-panel-editor's OWN (much
    larger, unrelated-scale) coordinate space, not the pane's own tiny
    content-area frame — verified on the corpus: a field's raw ``<bounds>``
    numbers (e.g. in the hundreds) bear no relation to the pane's own
    ``<bounds>`` (tens), yet the fields' COLLECTIVE extent exactly equals the
    pane's real inner content area (both axes, to the pixel) — i.e. clusters
    never scroll, every field is shown. So this NORMALIZES by that collective
    extent (preserving every field's real relative position and size) and
    fits it into the pane's real inner area with a SINGLE uniform scale (the
    verified case is pure translation, scale 1.0; a uniform scale is the
    documented fallback for a future cluster whose extent doesn't match).

    ``paneHierarchy``'s own ``<bounds>`` is relative to ``cluster_el``'s RAW
    ``<bounds>`` origin (a heap-format fact, same convention
    ``_const_value_box``/``_const_label_box`` use) — which can differ from
    the CLAMPED value box (``_const_value_box(cluster_el)``, the box this
    geometry is actually relative to) when an inline caption inflates the raw
    box (task #77). The pane inset is re-baselined by that delta so ``(0, 0)``
    always means the value box's own top-left, matching what a caller's
    assigned drawn box actually starts at.

    A field whose OWN value is itself a cluster (``_cluster_shape`` — a
    direct ``stdClust``, or a typedef-wrapped one) recurses into its OWN
    full ``ClusterGeom`` (``ClusterFieldGeom.nested``) — self-contained,
    relative to ITS OWN (0, 0), never scaled by this level's scale (the
    DRAWER composes scales across levels, not the extractor). A field of
    some OTHER type that merely carries a nested cluster elsewhere in its
    heap subtree (e.g. a data-typed refnum's registered payload type) is
    NOT recursed here — LabVIEW draws such a refnum compact regardless of
    its payload's complexity, so the renderer draws it from the graph's own
    type (a compact type-mnemonic badge), never this per-field geometry —
    see ``render.nodes._leaf_const_glyph``'s ``Refnum`` branch.

    None when there's no field-level geometry to extract (no
    ``paneHierarchy``/``zPlaneList``, or no field has both a name and a value
    box) — callers fall back to the glyph's own uniform-row draw.
    """
    pane = cluster_el.find("paneHierarchy")
    if pane is None:
        return None
    zp = pane.find("zPlaneList")
    if zp is None:
        return None
    raw_box = _rect(cluster_el)
    value_box = _const_value_box(cluster_el)
    pane_local = _rect(pane)
    if raw_box is None or value_box is None or pane_local is None:
        return None
    dx, dy = value_box[0] - raw_box[0], value_box[1] - raw_box[1]
    pane_rel = (
        pane_local[0] - dx,
        pane_local[1] - dy,
        pane_local[2] - dx,
        pane_local[3] - dy,
    )
    inner_w = pane_rel[2] - pane_rel[0]
    inner_h = pane_rel[3] - pane_rel[1]
    if inner_w <= 0 or inner_h <= 0:
        return None

    entries: list[tuple[str, Rect, Rect | None, ET.Element]] = []
    extent_rects: list[Rect] = []
    for f in zp.findall("SL__arrayElement"):
        name = _field_name(f)
        field_value_box = _const_value_box(f)
        if name is None or field_value_box is None:
            continue
        label_box = None if _field_label_hidden(f) else _const_label_box(f)
        entries.append((name, field_value_box, label_box, f))
        extent_rects.append(field_value_box)
        if label_box is not None:
            extent_rects.append(label_box)
    if not entries:
        return None

    min_l = min(r[0] for r in extent_rects)
    min_t = min(r[1] for r in extent_rects)
    extent_l = max(r[2] for r in extent_rects) - min_l
    extent_t = max(r[3] for r in extent_rects) - min_t
    if extent_l <= 0 or extent_t <= 0:
        return None
    scale = min(inner_w / extent_l, inner_h / extent_t)
    bx1, by1 = pane_rel[0], pane_rel[1]

    def _map(rect: Rect) -> Rect:
        x1, y1, x2, y2 = rect
        return (
            bx1 + (x1 - min_l) * scale,
            by1 + (y1 - min_t) * scale,
            bx1 + (x2 - min_l) * scale,
            by1 + (y2 - min_t) * scale,
        )

    result = []
    for name, field_value_box, label_box, f in entries:
        mapped_value = _map(field_value_box)
        mapped_label = _map(label_box) if label_box is not None else None
        nested_shape = _cluster_shape(f)
        nested = (
            _cluster_field_geoms(nested_shape) if nested_shape is not None else None
        )
        result.append(
            ClusterFieldGeom(
                name,
                mapped_value,
                mapped_label,
                nested,
                refnum_expanded=_refnum_type_display_expanded(f),
                refnum_payload=_refnum_payload_layout(f),
            )
        )
    return ClusterGeom(
        width=value_box[2] - value_box[0],
        height=value_box[3] - value_box[1],
        fields=tuple(result),
    )


class _LayoutBuilder:
    def __init__(self, resources: dict[int, Path] | None = None) -> None:
        # Section Index -> resource file (PICC/DSIM), for resolving a
        # decoration's ImageResID (embedded picture) — see image_resources.py.
        # Empty when the caller has no resource map (today's behavior).
        self.resources: dict[int, Path] = resources or {}
        # Embedded-picture PNG bytes, keyed by the owning decoration's uid.
        self.images: dict[str, bytes] = {}
        self.node_bounds: dict[str, Rect] = {}
        self.label_bounds: dict[str, Rect] = {}
        # A cluster-constant's raw uid -> its real geometry (see ClusterGeom
        # / _cluster_field_geoms).
        self.cluster_field_geom: dict[str, ClusterGeom] = {}
        # An array constant's raw uid -> its cluster-typed ELEMENT's real
        # geometry (same ClusterGeom, at the element's own natural size).
        self.array_element_cluster: dict[str, ClusterGeom] = {}
        # Raw uids of top-level data-typed stdRefNum constants whose
        # type-display is EXPANDED (see _refnum_type_display_expanded).
        self.refnum_expanded: set[str] = set()
        # Top-level data-typed stdRefNum constants' registered CLUSTER
        # payload real placement + geometry (see RefnumPayload /
        # _refnum_payload_layout) — present only when the payload is a
        # cluster with decodable heap geometry.
        self.refnum_payload: dict[str, RefnumPayload] = {}
        self.terminal_centers: dict[str, Point] = {}
        self.border_terminals: dict[str, Rect] = {}
        self.border_terminal_kind: dict[str, str] = {}
        self.structure_border_uids: dict[str, list[str]] = {}
        # raw uids whose direct <label> child is hidden (objFlags bit 0x8).
        self.hidden_labels: set[str] = set()
        # flat-sequence raw uid -> inter-frame divider x-positions.
        self.sequence_dividers: dict[str, list[float]] = {}
        # (termList uids, compressedWireTable hex) for every heap signal,
        # collected raw during the walk; resolved to centers afterward by
        # ``_resolve_wire_geometry`` once ``terminal_centers`` is complete.
        self.raw_signals: list[tuple[list[str], str]] = []
        # Z-ORDER paint rank per raw uid + the monotonic DFS counter feeding it.
        # Assigned in ``_visit`` as the walk descends ``zPlaneList`` (then
        # ``nodeList``) at every level, so it captures LabVIEW's back-to-front
        # paint order (see Layout.z_order). Render-only.
        self.z_order: dict[str, int] = {}
        self._z_seq: int = 0
        # WIRE paint rank (per source terminal uid) + its own monotonic counter,
        # assigned as each diagram's ``signalList`` is walked (see Layout.wire_z).
        self.wire_z: dict[str, int] = {}
        self.decorations: list[LayoutDecoration] = []
        self._wire_z_seq: int = 0

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

    # -- shift-register pairs ---------------------------------------------
    def _map_shift_register(
        self,
        lsr: ET.Element,
        term_uid: str | None,
        ox: float,
        oy: float,
        off_x: float,
        off_y: float,
    ) -> None:
        """Map both halves of a loop shift-register pair to their own borders.

        A shift register is serialized as ONE heap ``term`` carrying a left
        register (``dco class="lSR"``); the right register (``rSR``) is either a
        SEPARATE term (for-loops) or NESTED inside the left as ``rsrDCO``
        (while-loops). When nested, each register still has its OWN
        ``termBounds`` (left vs right structure border) and its OWN, disjoint
        ``termList`` of wire-endpoint uids. Map each independently so a wire
        feeding the right register anchors to the right border rather than being
        pulled to the left glyph by the generic descendant search (task #96). A
        nested rsrDCO with no termBounds of its own (the empty for-loop ref) is
        skipped — that side is a standalone term handled by ``_map_terms``.
        """
        rsr = lsr.find("rsrDCO")
        for reg, extra in ((lsr, term_uid), (rsr, None)):
            if reg is None:
                continue
            tb = _rect(reg, "termBounds")
            if tb is None:
                continue
            abs_tb = (
                ox + tb[0] + off_x,
                oy + tb[1] + off_y,
                ox + tb[2] + off_x,
                oy + tb[3] + off_y,
            )
            center = ((abs_tb[0] + abs_tb[2]) / 2, (abs_tb[1] + abs_tb[3]) / 2)
            uids = {u for u in (reg.get("uid"), extra) if u}
            uids.update(
                s.get("uid", "")
                for s in reg.findall("termList/SL__arrayElement")
                if s.get("uid")
            )
            for u in uids:
                self.node_bounds.setdefault(u, abs_tb)
                self.terminal_centers.setdefault(u, center)

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
                r
                for r in (
                    _rect(t, ".//termBounds")
                    for t in tl.findall("SL__arrayElement")
                    if t.get("class") == "term"
                )
                if r is not None
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
                        uid,
                        (
                            (abs_rect[0] + abs_rect[2]) / 2,
                            (abs_rect[1] + abs_rect[3]) / 2,
                        ),
                    )
                    # The label's saved position (relative to the terminal's
                    # origin b[0]/b[1]) lifted into the same absolute frame as
                    # abs_rect — so the renderer can honor above/below placement.
                    lab = _fp_label_box(term)
                    if lab is not None:
                        self.label_bounds.setdefault(
                            uid,
                            (
                                ox + b[0] + lab[0],
                                oy + b[1] + lab[1],
                                ox + b[0] + lab[2],
                                oy + b[1] + lab[3],
                            ),
                        )
                continue
            if cls != "term":
                continue
            term_uid = term.get("uid")
            # Shift-register pair: a loop's register is one heap `term` whose
            # `dco class="lSR"` (left border) may carry the right register
            # NESTED as `rsrDCO`. Each side has its OWN termBounds + DISJOINT
            # termList; map them independently (the generic ``.//termList``
            # search below would descend into the nested rsrDCO and give the
            # RIGHT register's wire uids the LEFT glyph's center — a wire
            # feeding the right register then routes back across the whole
            # structure, task #96).
            dco0 = term.find("dco")
            if dco0 is not None and dco0.get("class") == "lSR":
                self._map_shift_register(dco0, term_uid, ox, oy, off_x, off_y)
                continue
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
                    ox + tb[0] + off_x,
                    oy + tb[1] + off_y,
                    ox + tb[2] + off_x,
                    oy + tb[3] + off_y,
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
                    ox + cb[0] + off_x,
                    oy + cb[1] + off_y,
                    ox + cb[2] + off_x,
                    oy + cb[3] + off_y,
                )
                if term_uid:
                    self.node_bounds[term_uid] = abs_cb
                    # Its caption (free label) rect, same offset frame as the
                    # value box — the renderer draws text here iff the graph
                    # carries caption text for this uid (task #77).
                    capb = _const_label_box(ddo) if ddo is not None else None
                    if capb is not None:
                        self.label_bounds[term_uid] = (
                            ox + capb[0] + off_x,
                            oy + capb[1] + off_y,
                            ox + capb[2] + off_x,
                            oy + capb[3] + off_y,
                        )
                    # A cluster constant (direct, or a typedef-wrapped named
                    # cluster type): its REAL per-field geometry (issue #45).
                    cluster_shape = _cluster_shape(ddo) if ddo is not None else None
                    if cluster_shape is not None:
                        cg = _cluster_field_geoms(cluster_shape)
                        if cg is not None:
                            self.cluster_field_geom[term_uid] = cg
                    # An array constant whose ELEMENT is a cluster: the
                    # element's real geometry, at its own natural size — every
                    # visible row draws this SAME shape (issue #45).
                    elif ddo is not None and ddo.get("class") in _ARRAY_DDO_CLASSES:
                        elem_shape = _cluster_shape(ddo.find("ddo"))
                        if elem_shape is not None:
                            elem_cg = _cluster_field_geoms(elem_shape)
                            if elem_cg is not None:
                                self.array_element_cluster[term_uid] = elem_cg
                    # A bare (not cluster-field) data-typed refnum constant:
                    # its own expanded/compact type-display state, and (when
                    # expanded with a decodable cluster payload) that
                    # payload's real placement + geometry.
                    elif ddo is not None and _refnum_type_display_expanded(ddo):
                        self.refnum_expanded.add(term_uid)
                        payload = _refnum_payload_layout(ddo)
                        if payload is not None:
                            self.refnum_payload[term_uid] = payload
                cx = (abs_cb[0] + abs_cb[2]) / 2
                cy = (abs_cb[1] + abs_cb[3]) / 2
            # termHotPoint: LabVIEW's EXPLICIT per-terminal wire-attach offset
            # from the termBounds centre — the wire connects HERE, not the
            # geometric middle (an expandable prim's row terminal, say, attaches
            # above/below its box centre). It shifts the CONNECTION point only,
            # never the terminal's own box (node_bounds). Absent on most
            # terminals (centre == box middle). (x, y) in the node's frame.
            hp = term.find(".//termHotPoint")
            if hp is not None and hp.text:
                try:
                    hx, hy = (int(v) for v in hp.text.strip("()").split(","))
                    cx, cy = cx + hx, cy + hy
                except ValueError:
                    pass
            for u in self._collect_uids(term):
                self.terminal_centers.setdefault(u, (cx, cy))

    # -- structure border DCOs (loop N/i/cond, case selector) --------------
    def _border_dcos(
        self,
        struct: ET.Element,
        ox: float,
        oy: float,
        structure_uid: str,
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
            center = ((abs_rect[0] + abs_rect[2]) / 2, (abs_rect[1] + abs_rect[3]) / 2)
            for u in self._collect_uids(dco):
                self.terminal_centers.setdefault(u, center)

    # -- recursive walk -----------------------------------------------------
    def walk(
        self, diag: ET.Element, ox: float, oy: float, container_uid: str | None = None
    ) -> None:
        zp = diag.find("zPlaneList")
        if zp is not None:
            for elem in zp.findall("SL__arrayElement"):
                self._visit(elem, ox, oy, container_uid)

        # nodeList holds diagram nodes that don't sit in zPlaneList: sRN
        # (shift-register / border-terminal groups) AND In-Place-Element-
        # Structure border access nodes (decomposeCluster/Array/MatchNode — the
        # little split/recompose tabs on the IPES border). Each carries its own
        # ``bounds`` (sRN's is a translation to this diagram's origin for
        # out-of-diagram terminal refs; a decompose node's is its own tab rect)
        # plus a termList needing offset-aware geometry — so visit them all.
        # `_visit` is bounds-gated (skips anything without a rect) and already
        # branches on sRN internally, so this stays correct for both. Skipping
        # the decompose nodes left their wires terminating in empty space and
        # made whole VIs decline for "missing geometry".
        nl = diag.find("nodeList")
        if nl is not None:
            for elem in nl.findall("SL__arrayElement"):
                self._visit(elem, ox, oy, container_uid)

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
                if uids:
                    # signalList order IS the wire z-list (front-to-back), keyed
                    # by source terminal uid so the render can sort nets by it.
                    self.wire_z.setdefault(uids[0], self._wire_z_seq)
                    self._wire_z_seq += 1
                self.raw_signals.append((uids, cw.text.strip()))

    def _reanchor_nmux_aggregate_inputs(self, root: ET.Element) -> None:
        """Anchor a Bundle-By-Name's INPUT aggregate terminal to the interior
        cluster column, BEFORE wire geometry is resolved.

        A Bundle's input and output cluster terminals SHARE one aggregate DCO:
        the output OWNS it (a ``<dco class="nmxDCO" ...>`` carrying the
        ``termBounds``), the input is a bare ``<dco uid=.../>`` REFERENCE with no
        box of its own — a fixed fact of the heap format (verified across Bundle
        nodes). So the generic term walk collapses the input onto the output's
        box centre, and the input cluster wire then terminates INSIDE the output
        box. LabVIEW draws the two as adjacent equal-width columns: the output on
        its own box (flush to the node's right edge), the input in the column
        immediately to its left — the empty band between the field cells and the
        box. Move the input's centre to that column's top.

        This runs on ``terminal_centers`` BEFORE ``_resolve_wire_geometry``, so
        the faithful wire is DECODED against the corrected anchor — its blob is
        re-formed onto the interior column, never re-routed or overridden. Only
        the position-less INPUT moves; the output keeps its real recorded box.
        An Unbundle (a single aggregate that owns its own box) has no alias term
        and is left untouched.
        """
        for nmux in root.iter("SL__arrayElement"):
            if nmux.get("class") != "nMux":
                continue
            agg_el = nmux.find("dcoAgg")
            tl = nmux.find("termList")
            nmux_uid = nmux.get("uid")
            if agg_el is None or tl is None or not nmux_uid:
                continue
            agg_uid = agg_el.get("uid")
            owner_uid: str | None = None
            alias_uid: str | None = None
            for term in tl.findall("SL__arrayElement"):
                if term.get("class") != "term":
                    continue
                dco = term.find("dco")
                if dco is None or dco.get("uid") != agg_uid:
                    continue
                if dco.get("class") == "nmxDCO":
                    owner_uid = term.get("uid")  # output: owns the box
                else:
                    alias_uid = term.get("uid")  # input: bare reference, no box
            if owner_uid is None or alias_uid is None:
                continue  # Unbundle / single aggregate — nothing to split
            box = self.node_bounds.get(owner_uid)
            node = self.node_bounds.get(nmux_uid)
            if box is None or node is None:
                continue
            # Anchor the position-less input to the TOP of its interior column.
            (ix1, iy1, ix2, _), _ = bundle_aggregate_columns(box, node)
            self.terminal_centers[alias_uid] = ((ix1 + ix2) / 2, iy1)

    def _resolve_wire_geometry(self) -> dict[str, list[tuple[float, float]]]:
        """Decode every ``raw_signal`` into ``Layout.wire_by_uid``: each branch's
        FULL polyline (LabVIEW's own source anchor + bend points + sink anchor)
        keyed by its SINK terminal uid.

        One pass over both 2-endpoint and fan-out signals — ``decode_signal``
        handles both (a 2-endpoint wire is the 1-leaf case). It needs the source
        and ALL sink centers (a tree can't be decoded one branch at a time), then
        each resulting branch is stored under its sink uid so ``scene.py`` can
        look it up per drawn wire by exact identity — no center rounding, no
        proximity tolerance. The stored polyline is the wire's ACTUAL block-
        diagram geometry, drawn verbatim: the endpoints are the heap terminal
        centers the decode was anchored to, so the renderer never re-anchors it
        to a separately-computed terminal coordinate. Signals with an unresolved
        terminal, or that don't decode exactly, are skipped and fall back to the
        auto-router (the ONLY case autoroute runs).
        """
        from .wire_table import decode_signal

        by_uid: dict[str, list[tuple[float, float]]] = {}
        for uids, blob in self.raw_signals:
            src = self.terminal_centers.get(uids[0])
            sink_uids = uids[1:]
            sinks = [self.terminal_centers.get(u) for u in sink_uids]
            if src is None or not sinks or any(s is None for s in sinks):
                continue
            resolved = [s for s in sinks if s is not None]
            mids = decode_signal(blob, src, resolved)
            if mids is None:
                continue
            for sink_uid, mid in zip(sink_uids, mids):
                # Full wire: source anchor -> decoded bends -> sink anchor.
                by_uid[sink_uid] = [src, *mid, self.terminal_centers[sink_uid]]
        return by_uid

    def _resolve_embedded_picture(self, uid: str, res_id: str) -> None:
        """A POSITIVE ``ImageResID`` on a decoration names a DSIM picture
        section (an embedded image the developer pasted onto the diagram), not
        a Decorations-palette shape id (those are always negative — see
        ``render/glyphs/decorations/factory.py``). Carve its PNG and stash it
        in ``self.images`` keyed by the owning uid; no-op (never raises) when
        the id isn't a positive int, has no resource map, or doesn't carve."""
        try:
            index = int(res_id)
        except ValueError:
            return
        if index <= 0:
            return
        section = self.resources.get(index)
        if section is None or not section.exists():
            return
        png = carve_png(section.read_bytes())
        if png is not None:
            self.images[uid] = png

    def _resolve_picc_points(
        self, internals_res_id: str | None, ox: float, oy: float
    ) -> tuple[Point, ...]:
        """The decoration's real per-instance endpoints from its
        ``ImageInternalsResID`` PICC section, offset into absolute coordinates
        by the SAME walk offset (``ox, oy``) the element's own ``<bounds>``
        gets (see labview-binary-format.md — the PICC points are pre-offset,
        exactly like ``bb`` in ``_visit``). ``()`` when there's no internals id,
        no resource map, or the section doesn't resolve/decode."""
        if internals_res_id is None:
            return ()
        try:
            index = int(internals_res_id)
        except ValueError:
            return ()
        section = self.resources.get(index)
        if section is None or not section.exists():
            return ()
        raw = decode_picc_points(section.read_bytes())
        return tuple((ox + x, oy + y) for x, y in raw)

    def _record_decoration(
        self,
        elem: ET.Element,
        uid: str,
        ox: float,
        oy: float,
        container_uid: str | None,
        *,
        is_attachment: bool,
    ) -> None:
        """Record one ``cosm`` or ``attachment`` element as a
        :class:`LayoutDecoration`: its shape id, any embedded-picture PNG
        (``self.images``), any decoded PICC endpoints, and — for an
        ``attachment`` only — the uid its ``<attachedObject>`` names. No-op
        when the element carries no ``<image><ImageResID>`` at all (every
        sample seen has one, but this stays defensive)."""
        res_id = elem.findtext("image/ImageResID")
        if res_id is None:
            return
        res_id = res_id.strip()
        bg = elem.findtext("bgColor")
        self._resolve_embedded_picture(uid, res_id)
        internals_id = elem.findtext("image/ImageInternalsResID")
        points = self._resolve_picc_points(internals_id, ox, oy)
        attached_uid = None
        if is_attachment:
            target = elem.find("attachedObject")
            attached_uid = target.get("uid") if target is not None else None
        self.decorations.append(
            LayoutDecoration(
                uid=uid,
                image_res_id=res_id,
                bg_color=bg.strip() if bg else None,
                container_uid=container_uid,
                points=points,
                is_attachment=is_attachment,
                attached_uid=attached_uid,
            )
        )

    def _visit(
        self, elem: ET.Element, ox: float, oy: float, container_uid: str | None = None
    ) -> None:
        bb = _rect(elem)
        if bb is None:
            return
        ax1, ay1 = ox + bb[0], oy + bb[1]
        ax2, ay2 = ox + bb[2], oy + bb[3]

        uid = elem.get("uid")
        self._record_label_hidden(elem, uid)
        if uid:
            self.node_bounds.setdefault(uid, (ax1, ay1, ax2, ay2))
            # A block-diagram decoration: a pure-visual Flat Frame / Line /
            # Arrow / embedded picture (``cosm``), or a label-to-object
            # leader/pushpin (``attachment``). Record its shape id + owning
            # container; bounds + paint rank were just recorded above (uid-keyed)
            # like any element.
            cls = elem.get("class")
            if cls in ("cosm", "attachment"):
                self._record_decoration(
                    elem, uid, ox, oy, container_uid, is_attachment=cls == "attachment"
                )
            # First-sight paint rank (see Layout.z_order): the walk reaches
            # elements in zPlaneList back-to-front order at each level, so the
            # rank captures LabVIEW's occlusion order for free.
            if uid not in self.z_order:
                self.z_order[uid] = self._z_seq
                self._z_seq += 1
        # An sRN's own ``bounds`` is a translation for out-of-diagram terminal
        # REFERENCES, but its ``termList`` holds fPTerm/constants that are
        # diagram-level objects — each already carries diagram-relative bounds,
        # so map them from the diagram origin, not the sRN's translated corner
        # (otherwise a control inside a loop lands far to the upper-left).
        term_ox, term_oy = (ox, oy) if elem.get("class") == "sRN" else (ax1, ay1)
        self._map_terms(elem, term_ox, term_oy)
        if uid:
            self._border_dcos(elem, ax1, ay1, uid)

        dlist = elem.find("diagramList")
        inner = (
            [d for d in dlist.findall("SL__arrayElement") if d.get("class") == "diag"]
            if dlist is not None
            else []
        )
        if inner:
            for d in inner:
                self.walk(d, ax1, ay1, uid)
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
                    self.walk(d, ax1 + dx, ay1 + dy, uid)
        if is_flat and dividers and uid:
            self.sequence_dividers.setdefault(uid, []).extend(dividers)


def build_layout_from_root(
    root_elem: ET.Element,
    *,
    icon_png: Path | None = None,
    resources: dict[int, Path] | None = None,
) -> Layout:
    """Build a ``Layout`` from an ALREADY-PARSED heap root element.

    This is the pure geometry decode with no I/O — it takes the same top
    element ``ET.parse(_BDHb.xml).getroot()`` yields, so ``parse_vi`` can run it
    on the very root it already parsed (no second read). ``root_elem`` may be
    the heap wrapper (with a ``<root>`` child) or the ``<root>`` diagram itself;
    both are handled, exactly as :func:`build_layout` did. ``icon_png`` is the
    connector-pane icon path (derived from the heap path by the caller, which a
    bare root can't know) or None. ``resources`` is the Section Index -> file
    map (``image_resources.resources_for_heap``) for resolving an embedded
    picture's ``ImageResID``; ``None`` (the default) reproduces today's
    behavior with no picture/PICC resolution — every existing caller with no
    resource map keeps working unchanged.
    """
    root = root_elem.find("root")
    if root is None:
        root = root_elem

    builder = _LayoutBuilder(resources)
    builder.walk(root, 0.0, 0.0)
    # Correct the position-less Bundle input aggregate anchor BEFORE the wires
    # are decoded, so the faithful blob re-forms onto the interior column.
    builder._reanchor_nmux_aggregate_inputs(root)

    return Layout(
        node_bounds=builder.node_bounds,
        terminal_centers=builder.terminal_centers,
        border_terminals=builder.border_terminals,
        border_terminal_kind=builder.border_terminal_kind,
        structure_border_uids=builder.structure_border_uids,
        hidden_labels=builder.hidden_labels,
        sequence_dividers=builder.sequence_dividers,
        label_bounds=builder.label_bounds,
        wire_by_uid=builder._resolve_wire_geometry(),
        z_order=builder.z_order,
        wire_z=builder.wire_z,
        decorations=builder.decorations,
        images=builder.images,
        icon_png=icon_png,
        cluster_field_geom=builder.cluster_field_geom,
        array_element_cluster=builder.array_element_cluster,
        refnum_expanded=builder.refnum_expanded,
        refnum_payload=builder.refnum_payload,
    )


def _icon_for_heap(bd: Path) -> Path | None:
    """The connector-pane icon PNG beside a heap file, or None if absent."""
    icon = bd.parent / f"{bd.stem.replace('_BDHb', '')}_ICON.png"
    return icon if icon.exists() else None


def build_layout(vi_or_bd: Path) -> Layout:
    """Build a ``Layout`` from a ``.vi`` file or a ``_BDHb.xml`` heap path.

    Thin I/O wrapper: it does the read (extract for a ``.vi``, ``ET.parse`` the
    heap, locate the icon + resource map) and hands the parsed root to
    :func:`build_layout_from_root`. Prefer ``parse_vi(..., layout=True)`` when a
    graph is already being built — that reuses its single parse instead of
    reading the heap a second time.
    """
    if vi_or_bd.suffix.lower() == ".vi":
        bd_path, _, _ = extract_vi_xml(vi_or_bd)
        bd = Path(bd_path)
    else:
        bd = vi_or_bd
    root_elem = ET.parse(bd).getroot()
    return build_layout_from_root(
        root_elem, icon_png=_icon_for_heap(bd), resources=resources_for_heap(bd)
    )
