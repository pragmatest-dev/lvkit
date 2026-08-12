"""Diff two versions of a LabVIEW VI by comparing their graph representations."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from ..models import (
    CaseFrame,
    CaseOperation,
    DisableStructureOperation,
    EventFrame,
    EventOperation,
    FPTerminal,
    Frame,
    InPlaceOperation,
    LoopOperation,
    Operation,
    SequenceFrame,
    SequenceOperation,
    Terminal,
    _is_error_cluster,
)
from ..parser.node_types import get_display_name
from .models import (
    CURATED_PROPERTY_FLAGS,
    CURATED_STRUCTURE_FLAGS,
    Constant,
    VIProperties,
    VIStructure,
    Wire,
    WireEnd,
    bool_str,
)
from .netlist import (
    NetlistItem,
    NetlistScope,
    _selector_lv_type,
    ambiguous_bares,
    build_netlist,
    index_module,
    instance_line,
    scope_header,
)
from .op_walk import (
    _const_value_str,
    _selector_label,
    _terminal_display_name,
)

if TYPE_CHECKING:
    from ..parser.layout import Layout, Point, Rect
    from .core import InMemoryVIGraph


# ── Report dataclasses ────────────────────────────────────────────────


@dataclass
class SignatureChange:
    category: str  # "added", "removed", "type_changed"
    direction: str  # "input" or "output"
    name: str
    old_type: str | None = None
    new_type: str | None = None


@dataclass
class MetadataChange:
    """One VI-level Properties/Structure change -- ALWAYS a value transition.
    Properties/Structure are a FIXED schema every VI has (every VI has a
    lock_state, a reentrant flag, an is_broken flag, ...) -- a field's VALUE
    changes between versions, but the field itself is never added or removed.
    So unlike ``SignatureChange`` (added/removed/type_changed), there is only
    one category: old -> new, rendered with the ``~`` gutter."""
    name: str  # display name, e.g. "reentrant", "lock"
    old: str   # old value, display string ("false"/"true", or an enum value)
    new: str   # new value, display string


@dataclass
class ElementChange:
    """One LOGICAL change to the diagram, keyed by stable LabVIEW node UID.

    Only genuine behaviour changes are emitted. "Re-indentation" noise — a node
    wrapped in a new structure, moved, or handed a fresh UID by LabVIEW over
    identical wiring — is collapsed to unchanged and never appears (see
    ``diff_uid``), exactly as a code diff hides code that was only re-indented.
    """
    uid: str        # trailing numeric UID — matches SVG data-node / data-lv-struct
    full_id: str    # full op id, e.g. "TestCase.lvclass:run.vi::1065"
    kind: str       # node|structure|wire|constant|terminal|frame|value
    change: str     # "added" | "removed" | "modified"
    label: str      # display name
    # Absolute-pixel bounds (x1, y1, x2, y2) from the owning version's Layout —
    # the SAME coordinate space as the rendered SVG viewBox, so the viewer draws
    # a highlight straight from these with no getBBox scrape. None when the graph
    # was loaded without ``layout=True``. Added → head's layout; removed → base's;
    # modified → head's (the node persists; we point at its current position).
    bounds: Rect | None = None
    # For "modified" only: the SAME node's bounds in the BEFORE version, so the
    # viewer can highlight it in both panes (old → new before/after). None for
    # added (before has no such node) and removed (before bounds already in
    # `bounds`).
    bounds_before: Rect | None = None
    # For "modified": a short human-readable "old → new" of what changed (e.g. a
    # constant's value transition). None for added/removed (the label says it all).
    detail: str | None = None
    # The ELEMENT this change is (in words), when the row's own ``label`` is a
    # VALUE rather than a type — set for frame/value changes ("case frame",
    # "event frame") so the flat list reads glyph+words like every other row
    # (``○ str constant``, ``◻ <node>``) with the frame's value as subtext,
    # instead of a bare quoted value. None for kinds whose label already names
    # the element (node/wire/structure/constant).
    element: str | None = None
    # ── Faithful wire geometry (increment 2a) ────────────────────────────
    # The rendered wire's polyline — the SAME points render/scene.py draws:
    # [source-terminal center, *Layout.wire_by_uid[sink], sink-terminal center],
    # in absolute SVG-viewBox pixels. Set on WIRE changes so the viewer overlays
    # the real colored wire (not a pin). None when layout is absent. For an
    # added/modified wire it's the HEAD routing; for a removed wire the BASE.
    path: list[Point] | None = None
    # For a "modified" WIRE only: the OLD (before) routing polyline, so the
    # viewer can draw the dashed old wire beside the new one. None otherwise.
    path_before: list[Point] | None = None
    # For an added/removed NODE: the polylines of every wire incident to it (its
    # "chain"), so the viewer draws the node's wires in its add/remove color.
    # None when layout is absent or the node has no drawable incident wire.
    chain_paths: list[list[Point]] | None = None
    # ── Locality (which container/frame this element lives in) ───────────
    # Trailing UID of the INNERMOST enclosing Case structure or STACKED
    # Sequence the element sits in. None if the element is top-level, inside
    # a loop, or inside a flat sequence — those aren't independently
    # hide/show-able (render/scene.py's ``_frame_path`` skips them too; see
    # ``_is_interactive_struct``), so their contents count as base-level for
    # this field. Added/modified -> head-side container; removed -> base-side.
    container_uid: str | None = None
    # The full frame-addressing chain identifying which frame(s) the element
    # lives in, root->leaf, formatted EXACTLY like render/draw.py's
    # ``encode_frame_path``: ``"{struct_uid}={value}"`` segments joined by
    # ``;`` (e.g. ``"3870=2"``, or ``"3870=2;120=Default"`` when nested) — the
    # same string baked into the rendered SVG's ``<g class="lv-frame"
    # data-path="...">`` attribute, so a viewer can look up the live frame
    # group this change belongs to with no separate reconciliation. None when
    # ``container_uid`` is None. Added/modified -> head-side path; removed ->
    # base-side path.
    frame_path: str | None = None
    # For a "value modified" FRAME change only: the frame's addressing on the
    # BEFORE side, formatted like ``frame_path``. Needed because the selector
    # VALUE itself changed (a case ``1``->``0`` rename, an event relabel), so the
    # BEFORE pane addresses the very same frame differently (``...=1``) from the
    # after pane (``...=0``). The viewer drives each pane from its own side — own
    # pane from ``frame_path``, other pane from ``frame_path_before`` — so BOTH
    # land on the renamed frame at once. None for every other change, where the
    # frame value is identical in both panes and ``frame_path`` alone correlates.
    frame_path_before: str | None = None
    # For WIRE changes only: trailing UIDs of the wire's endpoint NODES (source
    # and sink), resolved as they appear in the OPPOSITE pane's SVG
    # ``data-node`` (translated through the exact/fuzzy match map -- see
    # ``_wire_changes``). A wire's own ``frame_path`` only ever addresses its
    # OWN pane (base for removed, head for added/modified), so the viewer has
    # no way to reveal the frame enclosing "where the wire was/is connected"
    # in the OTHER pane without this. None for non-wire changes, and also None
    # (well, an endpoint just dropped) when an endpoint has no counterpart at
    # all in the other pane -- it was itself added/removed alongside this wire,
    # so there is nothing there to reveal.
    endpoints: list[str] | None = None


@dataclass
class ChangeMap:
    """UID-keyed change-map — the single source of truth for the visual overlay
    and textual diff. Elements are matched by stable UID first, then unmatched
    leftovers by KIND-ANCHORED DATAFLOW, so a LabVIEW-regenerated UID over
    identical wiring collapses to unchanged instead of a bogus add+remove.
    """
    changes: list[ElementChange] = field(default_factory=list)
    common_node_uids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        def _poly(pts: list[Point] | None) -> list[list[float]] | None:
            return [list(p) for p in pts] if pts is not None else None

        return {
            "changes": [
                {"uid": c.uid, "full_id": c.full_id, "kind": c.kind,
                 "change": c.change, "label": c.label, "detail": c.detail,
                 "bounds": list(c.bounds) if c.bounds is not None else None,
                 "bounds_before": list(c.bounds_before)
                 if c.bounds_before is not None else None,
                 "path": _poly(c.path),
                 "path_before": _poly(c.path_before),
                 "chain_paths": [_poly(p) for p in c.chain_paths]
                 if c.chain_paths is not None else None,
                 "container_uid": c.container_uid,
                 "frame_path": c.frame_path,
                 "frame_path_before": c.frame_path_before,
                 "element": c.element,
                 "endpoints": c.endpoints}
                for c in self.changes
            ],
            "common_nodes": len(self.common_node_uids),
        }


# ── UID-keyed change-map (matches by stable node UID, not name) ────────

_STRUCT_OPS = (
    CaseOperation, LoopOperation, SequenceOperation, DisableStructureOperation,
    EventOperation, InPlaceOperation,
)
def _frames_of(op: Operation) -> Sequence[Frame] | None:
    """The frame list of a FRAME-BEARING structure, else None — the single
    'is this a frame-set structure?' test, shared by ``_struct_frame_changes``
    (which diffs the set) and ``_matched_struct_pairs`` (which pairs them). Every
    kind here has its changes represented as a frame-set diff (added / removed /
    value-changed) through the same helpers; Disable's frames are CaseFrames and
    Event's are EventFrames. A LoopOperation is a structure but NOT here: it has
    one unconditional body, not a selectable frame set. (The literal isinstance
    lives ONLY here so the type checker can narrow ``op`` to a ``.frames``-bearing
    type — pyright won't narrow on a tuple stored in a variable.)"""
    if isinstance(op, (CaseOperation, SequenceOperation,
                       EventOperation, DisableStructureOperation)):
        return op.frames
    return None


def _uid_of(op_id: str) -> str:
    """Trailing UID from an op.id ('...run.vi::1065' -> '1065')."""
    return op_id.rsplit("::", 1)[-1]


def _uid_sort(uid: str) -> tuple[int, object]:
    return (0, int(uid)) if uid.isdigit() else (1, uid)


def _struct_label(op: Operation) -> str:
    if isinstance(op, CaseOperation):
        return "Case structure"
    if isinstance(op, LoopOperation):
        return "While loop" if op.loop_type == "whileLoop" else "For loop"
    if isinstance(op, SequenceOperation):
        return "Flat sequence"
    if isinstance(op, EventOperation):
        return "Event structure"
    if isinstance(op, InPlaceOperation):
        return "In Place Element structure"
    return get_display_name(op.node_type) if op.node_type else "structure"


def _elem_label(op: Operation, kind: str) -> str:
    """Display label — structures name their kind, a node/SubVI uses its
    ``display_name`` (class-qualified when the op carries an ownership chain, so
    two classes' same-named methods disambiguate)."""
    if kind == "structure":
        return _struct_label(op)
    return op.display_name


@dataclass
class _ElemInfo:
    """One collected op plus its LOCALITY (task: locality stamping) — where it
    sits in the diagram, so every derived ``ElementChange`` can be stamped
    with the same information without re-walking the tree."""
    op: Operation
    kind: str
    # Outer locality (see ``ElementChange.container_uid``/``frame_path``) at
    # the point this op was encountered during the ``_collect_elements``
    # recursion — i.e. WHERE THIS OP ITSELF LIVES, not where its own frames
    # live (a structure's own entry never includes its own segment; only its
    # CHILDREN's entries do — see the recursion below).
    container_uid: str | None = None
    frame_path: str | None = None


def _is_interactive_struct(op: Operation) -> bool:
    """Whether ``op`` is wrapped in a togglable ``<g class="lv-frame"
    data-path=...>`` group by render/scene.py — a Case, (Conditional/Diagram-)
    Disable, or Event structure, or a STACKED sequence. MUST stay in lockstep
    with scene.py's own interactive-structure set (CaseStructureNode /
    DisableStructureNode / EventStructureNode / stacked sequence), so a frame
    change's frame_path segment matches a real hideable group the viewer can
    correlate. A While/For loop shows its body unconditionally, and a FLAT
    sequence shows every frame at once (film-strip) — neither is hidden, so
    their children keep the enclosing context's locality unchanged."""
    if isinstance(op, (CaseOperation, DisableStructureOperation, EventOperation)):
        return True
    return isinstance(op, SequenceOperation) and op.node_type != "flatSequence"


def _extend_frame_path(
    frame_path: str | None, struct_uid: str, value: object,
) -> str:
    """Append one ``"{struct_uid}={value}"`` segment — EXACTLY the token
    format ``render/draw.py``'s ``encode_frame_path`` bakes into the SVG's
    ``data-path`` attribute (see ``FramePath``/``encode_frame_path`` in
    render/scene.py), so the two are directly comparable with no
    reconciliation. Segments are ``;``-joined, root->leaf, matching the
    renderer's own nesting order (each recursive call only ever appends to
    its caller's already-built prefix)."""
    seg = f"{struct_uid}={value}"
    return f"{frame_path};{seg}" if frame_path else seg


def _frame_value(frame: Frame) -> object:
    """The raw selector/index value identifying ``frame`` —
    ``CaseFrame.selector_value`` for a case, ``SequenceFrame.index`` for a
    sequence (flat or stacked), ``EventFrame.index`` for an event frame
    (positional — the active frame is runtime-chosen, so it has no selector).
    Matches exactly what render/scene.py::_frame_path stores as ``cur.frame``
    for the same node (``construction.py`` stamps event children with
    ``str(idx)`` too), so ``str()``-ing it here reproduces the identical token
    and diff frame-paths correlate with the SVG ``data-path``."""
    if isinstance(frame, CaseFrame):
        return frame.selector_value
    if isinstance(frame, SequenceFrame):
        return frame.index
    if isinstance(frame, EventFrame):
        return frame.index
    return None


def _collect_elements(
    ops: list[Operation], out: dict[str, _ElemInfo],
    container_uid: str | None = None, frame_path: str | None = None,
) -> None:
    """Map trailing-UID -> ``_ElemInfo`` for every op, recursing structures.

    ``container_uid``/``frame_path`` carry the LOCALITY CONTEXT inherited
    from the caller — where the ops in ``ops`` themselves live (not their own
    frames' contents). Each op is stamped with that inherited context as-is;
    only when recursing INTO a Case/Sequence structure's frames does a new
    segment get appended (and only if that structure is interactive — see
    ``_is_interactive_struct`` — otherwise the context passes through
    unchanged, matching render/scene.py's own scoping).
    """
    for op in ops:
        kind = "structure" if isinstance(op, _STRUCT_OPS) else "node"
        out[_uid_of(op.id)] = _ElemInfo(op, kind, container_uid, frame_path)
        frames = _frames_of(op)
        if frames is not None:
            struct_uid = _uid_of(op.id)
            interactive = _is_interactive_struct(op)
            for frame in frames:
                if interactive:
                    value = _frame_value(frame)
                    child_container = struct_uid
                    child_path = _extend_frame_path(frame_path, struct_uid, value)
                else:
                    child_container, child_path = container_uid, frame_path
                _collect_elements(frame.operations, out, child_container, child_path)
        _collect_elements(op.inner_nodes, out, container_uid, frame_path)


_FUZZY_MIN = 0.5   # min Jaccard of dataflow edges for a fuzzy (modified) match


def _incident(wires: list[Wire]) -> dict[str, list[tuple[str, str, str]]]:
    """UID -> list of (role, neighbour-UID, neighbour-TERMINAL) over every wire it
    touches. Routes are irrelevant — only who-connects-to-which-terminal — so wire
    straightening is invisible. The neighbour terminal (a stable UID on the
    neighbour) distinguishes two unbundles that feed, say, different selectors of
    look-alike cases."""
    inc: dict[str, list[tuple[str, str, str]]] = {}
    for w in wires:
        su, du = _uid_of(w.source.node_id), _uid_of(w.dest.node_id)
        inc.setdefault(su, []).append(("out", du, w.dest.terminal_id))
        inc.setdefault(du, []).append(("in", su, w.source.terminal_id))
    return inc


def _match_elements(
    a: dict[str, _ElemInfo], b: dict[str, _ElemInfo],
    inc_a: dict[str, list[tuple[str, str, str]]],
    inc_b: dict[str, list[tuple[str, str, str]]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Match base-only <-> head-only elements by KIND-ANCHORED DATAFLOW and return
    ``(exact, fuzzy)``, each ``base_uid -> head_uid``.

    Each neighbour is anchored by a stable token — a UID common to both versions,
    an already-matched peer, else the neighbour's kind — plus the neighbour's
    terminal, never a raw self-UID, so a match survives churn in the surrounding
    UIDs. **exact** = identical dataflow: the same node LabVIEW re-keyed (new UID,
    same wiring) → collapses to unchanged. Found by fixpoint (each new match
    anchors the next), 1:1 only, so ambiguous look-alikes stay unmatched.
    **fuzzy** = same kind and dataflow overlap ≥ ``_FUZZY_MIN`` (Jaccard over the
    edge multiset): the same node with a real wiring change → ``modified``.
    """
    common = a.keys() & b.keys()
    exact: dict[str, str] = {}
    rev: dict[str, str] = {}
    ua, ub = set(a) - common, set(b) - common

    def kind(u: str) -> tuple[str, str]:
        entry = a.get(u) or b.get(u)
        op = entry.op if entry is not None else None
        if op is None:
            return ("?", "?")
        node_word = get_display_name(op.node_type) if op.node_type else ""
        return (op.name or "", node_word)

    def tok(v: str, base_side: bool) -> tuple:
        if v in common:
            return ("c", v)
        if base_side and v in exact:
            return ("m", v)
        if not base_side and v in rev:
            return ("m", rev[v])
        return ("k", *kind(v)) if (v in a or v in b) else ("ext", v)

    def edges(inc: dict, u: str, base_side: bool) -> Counter:
        return Counter((role, tok(v, base_side), term)
                       for role, v, term in inc.get(u, []))

    changed = True
    while changed:
        changed = False
        by_a: dict[tuple, list[str]] = {}
        by_b: dict[tuple, list[str]] = {}
        for u in ua:
            by_a.setdefault((kind(u), frozenset(edges(inc_a, u, True).items())),
                            []).append(u)
        for u in ub:
            by_b.setdefault((kind(u), frozenset(edges(inc_b, u, False).items())),
                            []).append(u)
        for sig, la in by_a.items():
            lb = by_b.get(sig)
            if lb and len(la) == 1 and len(lb) == 1:
                exact[la[0]] = lb[0]
                rev[lb[0]] = la[0]
                ua.discard(la[0])
                ub.discard(lb[0])
                changed = True

    # Fuzzy: best Jaccard over the residual, same kind, 1:1 greedy by score.
    ea = {u: edges(inc_a, u, True) for u in ua}
    eb = {u: edges(inc_b, u, False) for u in ub}
    scored: list[tuple[float, str, str]] = []
    for u in ua:
        for w in ub:
            if kind(u) != kind(w):
                continue
            inter = sum((ea[u] & eb[w]).values())
            union = sum((ea[u] | eb[w]).values())
            j = inter / union if union else 0.0
            if j >= _FUZZY_MIN:
                scored.append((j, u, w))
    fuzzy: dict[str, str] = {}
    used: set[str] = set()
    for _j, u, w in sorted(scored, key=lambda t: -t[0]):
        if u not in fuzzy and w not in used:
            fuzzy[u] = w
            used.add(w)
    return exact, fuzzy


# ── Wire endpoint diff (task #10) ──────────────────────────────────────
#
# Validated prototype: .tmp/probe_wirediff.py (20 corpus pairs). The engine
# below ports that algorithm to terminal granularity so it can key on the
# SINK (input) terminal, per the task spec, instead of just node pairs.


def _effective_sinks(
    graph: InMemoryVIGraph, vi: str, structs: set[str],
) -> dict[str, tuple[WireEnd, WireEnd, frozenset[str]]]:
    """Contract wires through structure tunnels.

    ``get_wires(vi, include_internal=True)`` gives internal edges as
    self-loops on the structure node bridging outer<->inner terminals. Follow
    each real producer's wire forward through any chain of such bridges to
    the REAL (non-structure-owned) sink terminal it ultimately feeds -- a
    wire wrapped in a new case/loop keeps its producer->consumer identity, so
    re-indentation is invisible to the diff.

    Returns sink TERMINAL id -> (effective source end, sink dest end, crossed),
    where ``crossed`` is the set of raw structure uids the contracted path
    threaded through (tunnel bridges it hopped). That set is the containment
    sieve's input: a wire whose path crosses an ADDED or REMOVED structure is
    an enclosure artifact (the wire is logically unchanged; it was just
    wrapped), so it must be suppressed. A LabVIEW input terminal takes exactly
    one wire, so each sink has a single effective producer.
    """
    wires = graph.get_wires(vi, include_internal=True)

    # terminal id -> its owning node id (raw, qualified) — from BOTH sides of
    # every wire, so a terminal is known whether it's ever a source or dest.
    owner: dict[str, str] = {}
    # terminal id -> dest ends of every wire sourced there (the "adjacency").
    out_edges: dict[str, list[WireEnd]] = {}
    for w in wires:
        owner[w.source.terminal_id] = w.source.node_id
        owner[w.dest.terminal_id] = w.dest.node_id
        out_edges.setdefault(w.source.terminal_id, []).append(w.dest)

    def struct_owner(term_id: str) -> str | None:
        node_id = owner.get(term_id)
        if node_id is None:
            return None
        uid = _uid_of(node_id)
        return uid if uid in structs else None

    sinks: dict[str, tuple[WireEnd, WireEnd, frozenset[str]]] = {}
    for w in wires:
        if _uid_of(w.source.node_id) in structs:
            continue  # start only from real (non-tunnel-bridge) producers
        # DFS forward through struct-owned terminals to real sinks, carrying
        # the set of structure uids each path has threaded through so far.
        seen: set[str] = set()
        stack: list[tuple[WireEnd, frozenset[str]]] = [(w.dest, frozenset())]
        while stack:
            dest_end, crossed = stack.pop()
            term_id = dest_end.terminal_id
            if term_id in seen:
                continue
            seen.add(term_id)
            owning_struct = struct_owner(term_id)
            if owning_struct is not None:
                crossed2 = crossed | {owning_struct}
                for nxt in out_edges.get(term_id, []):
                    stack.append((nxt, crossed2))
            else:
                sinks[term_id] = (w.source, dest_end, crossed)
    return sinks


def _point_rect(layout: Layout | None, uid: str) -> Rect | None:
    """A terminal's connection-point CENTER, widened to a zero-size ``Rect``
    -- ``ElementChange.bounds``/``bounds_before`` are ``Rect`` (node bounding
    boxes), but a wire endpoint has only a point (``Layout.terminal_centers``).
    A degenerate rect lets the viewer treat it identically to a node highlight
    (a single-pixel box) with no new field on ``ElementChange``."""
    if layout is None:
        return None
    center = layout.terminal_centers.get(uid)
    return (center[0], center[1], center[0], center[1]) if center else None


def _wire_path(
    layout: Layout | None, wires: list[Wire], sink_uid: str,
) -> list[Point] | None:
    """The FAITHFUL polyline of the drawn wire INTO ``sink_uid`` (a raw sink
    terminal uid) — the exact points ``render/scene.py`` draws:
    ``[source center, *Layout.wire_by_uid[sink], sink center]``.

    Uses the IMMEDIATE non-internal wire whose destination is ``sink_uid`` (the
    actual drawn wire), NOT any contracted effective source — this overlay must
    trace the real wire. ``wire_by_uid`` supplies the recorded intermediate
    bends; when absent the polyline is just the two endpoint centers (a straight
    segment — the renderer would auto-route, which is fine for an overlay).
    Returns None when layout is absent or an endpoint center is missing (an
    input terminal takes exactly one wire, so the first match is the wire)."""
    if layout is None:
        return None
    for w in wires:
        if _uid_of(w.dest.terminal_id) != sink_uid:
            continue
        src_center = layout.terminal_centers.get(_uid_of(w.source.terminal_id))
        sink_center = layout.terminal_centers.get(sink_uid)
        if src_center is None or sink_center is None:
            return None
        return [src_center, *layout.wire_by_uid.get(sink_uid, []), sink_center]
    return None


# An element "owns" the wires incident to it: for an added/removed node,
# constant, or terminal, every incident wire is itself new/gone, so it belongs
# to that element's add/remove — drawn as its ``chain_paths`` AND suppressed from
# the standalone wire diff (don't-describe-twice). The ONLY thing that differs
# per kind is how a wire is tested as "incident": a node/constant matches on the
# endpoint NODE uid, an FP terminal on the endpoint TERMINAL uid (a terminal's
# wire endpoint node is the always-unchanged VI ``__self__``, not the terminal).
# So ownership is expressed once, as a wire-match predicate, and both the chain
# geometry and the suppression key derive from it.
_WireMatch = Callable[[Wire], bool]


def _node_incident(node_uid: str) -> _WireMatch:
    """Match a wire incident to ``node_uid`` (as source OR sink node)."""
    return lambda w: node_uid in (
        _uid_of(w.source.node_id), _uid_of(w.dest.node_id))


def _term_incident(term_uid: str) -> _WireMatch:
    """Match a wire incident to the FP terminal ``term_uid`` (source OR sink
    terminal) — the key an FP terminal needs, since its endpoint node is the
    shared ``__self__`` VI node rather than the terminal itself."""
    return lambda w: term_uid in (
        _uid_of(w.source.terminal_id), _uid_of(w.dest.terminal_id))


def _incident_wires(wires: list[Wire], match: _WireMatch) -> list[Wire]:
    """Every wire matched by ``match`` — one element's incident ("owned") set."""
    return [w for w in wires if match(w)]


def _chain_paths(
    layout: Layout | None, wires: list[Wire], match: _WireMatch,
) -> list[list[Point]] | None:
    """Polylines of every wire matched by ``match`` — an element's wire "chain",
    drawn in its add/remove colour. Keyed per wire by its sink terminal (an input
    takes one wire, so sinks are unique across the incident set). None when
    layout is absent or no incident wire is drawable."""
    if layout is None:
        return None
    paths: list[list[Point]] = []
    for w in _incident_wires(wires, match):
        path = _wire_path(layout, wires, _uid_of(w.dest.terminal_id))
        if path is not None:
            paths.append(path)
    return paths or None


def _node_bounds(layout: Layout | None, uid: str) -> Rect | None:
    """A drawn element's absolute-pixel bounds from ``Layout.node_bounds``, keyed
    by its raw uid — the box every change highlight (node, constant, terminal)
    draws from. None when the graph was loaded without layout. (Terminals are in
    ``node_bounds`` too, keyed by their BD terminal uid — see render/scene.py.)"""
    return layout.node_bounds.get(uid) if layout is not None else None


def _transition(old: object, new: object) -> str:
    """Canonical ``old → new`` detail for any MODIFIED change — a constant value
    edit, a terminal retype/rename, a frame's selector value change. One arrow
    convention so every ``detail`` reads alike (and the netlist text renderer's
    ``_ascii_arrows`` has a single form to map). Wire changes keep their own
    deliberate ``← src (was …)`` idiom and don't use this."""
    return f"{old} → {new}"


# ── Change-kind registry ─────────────────────────────────────────────────────
# The ``ElementChange.kind`` taxonomy in ONE place, so the wire diff and the
# netlist tree derive their per-kind behaviour from named sets instead of the
# hand-synced literal tuples that used to be scattered through this file (the two
# ``("node","wire",…)`` filters in ``_netlist_diff`` drifted apart by a lone
# ``"structure"``). Membership:
#   • _LEAF_KINDS  — a leaf ROW in the containment tree.
#   • _TREE_KINDS  — everything IN that tree (leaves + the structure folders);
#     ``"frame"``/``"value"`` are handled separately, so they're deliberately out.
#   • _FRAME_KINDS — the frame-selector changes handled outside the tree.
# The per-kind netlist leaf-text renderer is ``_LEAF_TEXT`` (below, once its
# helpers exist). Adding a kind = update these sets + its detection pass — no
# hand-synced tuple to keep in step. (Wire-suppression needs NO per-kind knob
# here: an added/removed node/constant/tunnel endpoint falls out of "endpoint
# node not in the wire diff's ``unchanged`` set"; only added/removed FP terminals
# — sub-node elements sharing the ``__self__`` node — are passed in explicitly as
# ``changed_terms``; see ``_unstable_endpoint``.)
_LEAF_KINDS = frozenset({"node", "wire", "constant", "terminal"})
_TREE_KINDS = _LEAF_KINDS | frozenset({"structure"})
_FRAME_KINDS = frozenset({"frame", "value"})


def _unstable_endpoint(
    entry: _SinkEntry | None, stable_nodes: set[str], changed_terms: set[str],
) -> bool:
    """Whether either end of this contracted wire is NOT a stable operation
    boundary — so the wire is that endpoint's own story, not a standalone wire
    change (don't-describe-twice). One test covers every case:

    * a wire endpoint whose NODE isn't in ``stable_nodes`` (the unchanged/matched
      operations + the VI's ``__self__``): an added or removed node, a CONSTANT
      or a tunnel/non-operation node — none are stable producers/consumers a wire
      change should be reported between. (This subsumes the old "sink not
      unchanged" skip AND the "source not unchanged" downgrade.)
    * a wire endpoint whose TERMINAL is an added/removed FP terminal
      (``changed_terms``): terminals hang off the always-stable ``__self__``
      node, so the node test can't see them — this is the one sub-node case.

    Uses the CONTRACTED ends, so it's robust to tunnel routing. A new sub-node
    kind (e.g. mux field terminals) joins ``changed_terms``; nothing else here
    changes."""
    if entry is None:
        return False
    src, sink = entry[2], entry[3]
    return (
        _uid_of(src.node_id) not in stable_nodes
        or _uid_of(sink.node_id) not in stable_nodes
        or _uid_of(src.terminal_id) in changed_terms
        or _uid_of(sink.terminal_id) in changed_terms
    )


# One contracted sink: (canonical source node key, source terminal key, raw
# source end, raw sink end, canonical crossed-structure uids).
_SinkEntry = tuple[str, object, WireEnd, WireEnd, frozenset[str]]


def _sink_sort_key(key: tuple[str, object]) -> tuple:
    node_key, term_key = key
    if isinstance(term_key, int):
        term_rank: tuple = (0, term_key)
    elif isinstance(term_key, str):
        term_rank = (1, term_key)
    else:
        term_rank = (2, "")
    return (_uid_sort(node_key), term_rank)


def _wire_changes(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    va: str, vb: str,
    a: dict[str, _ElemInfo], b: dict[str, _ElemInfo],
    exact: dict[str, str], fuzzy: dict[str, str],
    layout_a: Layout | None, layout_b: Layout | None,
    changed_terms: set[str] | None = None,
) -> list[ElementChange]:
    """Wire endpoint diff (task #10), keyed on the SINK (input) terminal.

    For every input terminal belonging to an UNCHANGED (common/matched) real
    node, compare its effective (contracted) source between versions:

      base None  -> head X       : "added"
      base X     -> head None    : "removed"
      base X     -> head Y (X!=Y): "modified" -- but ONLY between two
        unchanged nodes; if the new source is an added node, or the old
        source a removed node, that half is already the node's own add/
        remove story (don't-describe-twice), so it's skipped here.

    Reuses the SAME node matching ``diff_uid`` already computed
    (``exact``/``fuzzy``) for canonical cross-version node identity.
    """
    structs_a = {u for u, entry in a.items() if entry.kind == "structure"}
    structs_b = {u for u, entry in b.items() if entry.kind == "structure"}

    # Non-internal (drawn) wires per version — for faithful wire-path overlay.
    wires_a = graph_a.get_wires(va, include_internal=False)
    wires_b = graph_b.get_wires(vb, include_internal=False)

    h2b = {**exact, **fuzzy}                     # base uid -> head uid
    b_of_h = {h: bs for bs, h in h2b.items()}     # head uid -> base uid
    matched_a = set(h2b.keys())
    matched_b = set(h2b.values())
    # STABLE nodes (base-space): the SAME logical operation on both sides —
    # common (identical uid) or exact/fuzzy-matched — plus the VI's own
    # ``__self__`` boundary. A wire endpoint whose node is NOT here is an added/
    # removed op, a constant, or a tunnel/non-operation: never a stable producer
    # or consumer a wire change should be reported between (see ``_unstable_endpoint``).
    unchanged = (a.keys() & b.keys()) | matched_a | {"__self__"}

    # CHANGED structures (base-space): cases/loops/sequences that are genuinely
    # added (head-only, unmatched) or removed (base-only, unmatched). These are
    # the enclosures whose appearance/disappearance re-wraps otherwise-
    # unchanged wiring — the containment sieve (below) drops any wire whose
    # contracted path threads through one of them.
    removed_struct = {u for u in structs_a if u not in b and u not in matched_a}
    added_struct = {u for u in structs_b if u not in a and u not in matched_b}
    changed_structs = removed_struct | {b_of_h.get(u, u) for u in added_struct}

    def canon(node_id: str, vi_self: str, base_side: bool) -> str:
        # The VI's own connector-pane/self node id is a qualified-name STRING
        # (the vi_name itself, not "{vi}::{uid}") that can flip on library
        # requalification (e.g. "…lvlib:Foo.vi" -> "Foo.vi"), producing
        # phantom wire changes. Canonicalize it to a fixed sentinel so a pure
        # requalification yields identical identity on both sides.
        if node_id == vi_self:
            return "__self__"
        uid = _uid_of(node_id)
        return uid if base_side else b_of_h.get(uid, uid)

    def _other_pane_endpoints(
        src_node_id: str, dest_node_id: str, vi_self: str, from_base: bool,
    ) -> list[str] | None:
        """This wire's source+sink NODE uids, translated to the OTHER pane's
        rendered SVG ``data-node`` identity -- the cross-pane reveal key a
        wire change otherwise carries no endpoint identity for at all (a
        wire's own ``frame_path`` only ever addresses its OWN pane; see
        ``ElementChange.endpoints``). ``from_base`` says which side (base/
        head) ``src_node_id``/``dest_node_id`` come from -- the OTHER pane is
        the opposite. Unlike ``canon`` (which folds BOTH sides down into one
        base-space key for comparison, defaulting an unmatched head uid to
        itself), this must answer "does the other pane actually have this
        node, and if so under what uid" -- so a genuinely unmatched endpoint
        has to come back as None, not a dangling uid the viewer could never
        find on screen. The VI's own connector-pane boundary node has no
        ``data-node`` in either pane's SVG (same boundary ``canon`` maps to
        the ``__self__`` sentinel for), so it always drops out below.
        Otherwise: a uid LabVIEW kept IDENTICAL across versions needs no
        translation (the same string already exists in both panes' SVG);
        anything else goes through the exact/fuzzy dataflow match already
        computed above (``h2b``/``b_of_h``) -- the SAME cross-version node
        identity every other locality/label lookup in this function uses.
        """
        def other(node_id: str) -> str | None:
            if node_id == vi_self:
                return None
            uid = _uid_of(node_id)
            if from_base:
                return uid if uid in b else h2b.get(uid)
            return uid if uid in a else b_of_h.get(uid)

        found = [u for u in (other(src_node_id), other(dest_node_id)) if u]
        return found or None

    # Constant sources render by their VALUE (e.g. "5"), matching how the
    # netlist's ``_resolve_source`` renders the same wire -- diff and netlist
    # must read a constant-fed wire identically.
    consts_a = {
        c.id: (c.label or _const_value_str(c)) for c in graph_a.get_constants(va)
    }
    consts_b = {
        c.id: (c.label or _const_value_str(c)) for c in graph_b.get_constants(vb)
    }
    self_terms_a = (
        graph_a.get_inputs(va, public_only=False)
        + graph_a.get_outputs(va, public_only=False)
    )
    self_terms_b = (
        graph_b.get_inputs(vb, public_only=False)
        + graph_b.get_outputs(vb, public_only=False)
    )

    def label_of(
        end: WireEnd, vi_self: str,
        elems: dict[str, _ElemInfo], self_terms: list[Terminal],
        consts: Mapping[str, str],
    ) -> str:
        """Human label for a wire endpoint. ``Wire.end.name`` is the owning
        NODE's display name (see ``get_wires``), not the per-terminal name,
        so recover the real terminal display name from the owning
        Operation's (or the VI's own connector-pane's) terminal list."""
        term_key = end.index if end.index is not None else end.name
        terminals: list[Terminal] = []
        owner_label: str | None = None
        if end.node_id == vi_self:
            terminals = self_terms
        else:
            entry = elems.get(_uid_of(end.node_id))
            if entry is not None:
                terminals = entry.op.terminals
                node_word = (
                    get_display_name(entry.op.node_type)
                    if entry.op.node_type else None
                )
                owner_label = entry.op.name or node_word
        for t in terminals:
            match = (
                t.index == term_key if isinstance(term_key, int)
                else t.name == term_key
            )
            if not match:
                continue
            # Resolve the terminal's identity — an nMux output reads its struct/
            # class FIELD net (e.g. ``isSkipped``), everything else its own
            # display name — stamped once at load (op_walk.stamp_nmux_lane_names).
            if (name := _terminal_display_name(t)) is not None:
                return name
        return (
            consts.get(end.node_id) or owner_label or end.name
            or end.node_id.split("::")[-1]
        )

    def keyed_sinks(
        graph: InMemoryVIGraph, vi: str,
        structs: set[str], vi_self: str, base_side: bool,
    ) -> dict[tuple[str, object], _SinkEntry]:
        raw = _effective_sinks(graph, vi, structs)
        out: dict[tuple[str, object], _SinkEntry] = {}
        for src_end, dest_end, crossed in raw.values():
            node_key = canon(dest_end.node_id, vi_self, base_side)
            term_key = dest_end.index if dest_end.index is not None else dest_end.name
            src_key = canon(src_end.node_id, vi_self, base_side)
            src_term_key = src_end.index if src_end.index is not None else src_end.name
            # Canonicalize crossed structure uids to base-space so they compare
            # against ``changed_structs`` (a structure LabVIEW re-keyed is not a
            # changed structure — only genuinely added/removed ones are).
            crossed_canon = frozenset(
                u if base_side else b_of_h.get(u, u) for u in crossed
            )
            out[(node_key, term_key)] = (
                src_key, src_term_key, src_end, dest_end, crossed_canon,
            )
        return out

    sinks_a = keyed_sinks(graph_a, va, structs_a, va, True)
    sinks_b = keyed_sinks(graph_b, vb, structs_b, vb, False)

    cterms = changed_terms or set()

    changes: list[ElementChange] = []
    for key in sorted(set(sinks_a) | set(sinks_b), key=_sink_sort_key):
        entry_a = sinks_a.get(key)
        entry_b = sinks_b.get(key)

        # CONTAINMENT SIEVE (sieve #3): if the base- or head-side contracted
        # path threads through a genuinely added/removed structure, this wire
        # is an ENCLOSURE artifact — the wire is logically unchanged; it was
        # merely wrapped in (or unwrapped from) a new structure. Founding law:
        # wrapping unchanged nodes in a new structure is NOT a change. Drop it.
        if (entry_a is not None and entry_a[4] & changed_structs) or (
            entry_b is not None and entry_b[4] & changed_structs
        ):
            continue

        src_id_a = (entry_a[0], entry_a[1]) if entry_a else None
        src_id_b = (entry_b[0], entry_b[1]) if entry_b else None
        if src_id_a == src_id_b:
            continue  # same effective source (or same absence) -- unchanged

        # DON'T-DESCRIBE-TWICE: drop an endpoint that isn't a stable operation
        # boundary (its node is added/removed/constant/tunnel, or its terminal is
        # an added/removed FP terminal) — that element already owns the wire (it
        # draws it as its chain), so what's LEFT is the genuine add/remove of the
        # OTHER endpoint. ONE check unifies the three parallel suppression paths
        # this used to carry (sink-not-unchanged skip, source-not-unchanged
        # downgrade, terminal-owned). See ``_unstable_endpoint``.
        nulled_a = _unstable_endpoint(entry_a, unchanged, cterms)
        nulled_b = _unstable_endpoint(entry_b, unchanged, cterms)
        if nulled_a:
            entry_a = None
        if nulled_b:
            entry_b = None
        if entry_a is None and entry_b is None:
            continue  # both endpoints are already their own added/removed story

        # CONTRACTION-PHANTOM GUARD: this reads as added/removed ONLY because the
        # OTHER endpoint was NULLED (its source became an added/removed node),
        # AND the SURVIVING endpoint reaches the sink THROUGH a structure (its
        # ``crossed`` set is non-empty) -- i.e. its "source" was contracted PAST a
        # case/loop/sequence/Select/IPES to a node that ISN'T the sink's real
        # producer. So an operand inserted/removed upstream of that structure
        # fabricates an add/remove on a sink whose real (immediate) wire never
        # moved; the added/removed operand already owns the rewiring. Suppress.
        # Left untouched: a genuine change whose survivor is DIRECTLY wired (empty
        # ``crossed`` -- the validated run.vi residual); a REAL deletion where the
        # other endpoint was genuinely absent, not nulled (``other_nulled`` False,
        # so a deleted wire through a structure still reports); and a modified wire
        # between two stable nodes (both survive -> ``survivor`` None).
        survivor, other_nulled = (
            (entry_a, nulled_b) if entry_b is None and entry_a is not None
            else (entry_b, nulled_a) if entry_a is None and entry_b is not None
            else (None, False)
        )
        if survivor is not None and other_nulled and survivor[4]:
            continue

        if entry_a is None:
            change = "added"
        elif entry_b is None:
            change = "removed"
        else:
            change = "modified"

        # Faithful wire polyline: the wire lives in the version matching its
        # change — removed → base, added & modified → head. A "modified" wire
        # ALSO carries the OLD (base) routing so the viewer can dash it.
        path_before: list[Point] | None = None
        if change == "removed":
            assert entry_a is not None
            dest_end = entry_a[3]
            sink_label = label_of(dest_end, va, a, self_terms_a, consts_a)
            old_label = label_of(entry_a[2], va, a, self_terms_a, consts_a)
            bounds = _point_rect(layout_a, _uid_of(dest_end.terminal_id))
            bounds_before = _point_rect(layout_a, _uid_of(entry_a[2].terminal_id))
            # The gutter (-) already says "removed" -- so render the connection
            # exactly like an added wire (sink <- source), not "(was ...)". In the
            # ASCII netlist this becomes "- sink = source" (the same port=net form
            # every node input uses), instead of the nonsensical "(was = source)"
            # the old "was ←" wording produced once ← was ASCII-mapped to =.
            detail = f"← {old_label}"
            path = _wire_path(layout_a, wires_a, _uid_of(dest_end.terminal_id))
            # Own pane is base (removed) -- reveal the OTHER (head) pane's
            # frame around this wire's surviving endpoint(s).
            endpoints = _other_pane_endpoints(
                entry_a[2].node_id, entry_a[3].node_id, va, True,
            )
        else:
            assert entry_b is not None
            dest_end = entry_b[3]
            sink_label = label_of(dest_end, vb, b, self_terms_b, consts_b)
            new_label = label_of(entry_b[2], vb, b, self_terms_b, consts_b)
            bounds = _point_rect(layout_b, _uid_of(dest_end.terminal_id))
            path = _wire_path(layout_b, wires_b, _uid_of(dest_end.terminal_id))
            # Own pane is head (added & modified alike) -- reveal the OTHER
            # (base) pane's frame around this wire's surviving endpoint(s).
            endpoints = _other_pane_endpoints(
                entry_b[2].node_id, entry_b[3].node_id, vb, False,
            )
            if change == "added":
                bounds_before = None
                detail = f"← {new_label}"
            else:
                assert entry_a is not None
                old_label = label_of(entry_a[2], va, a, self_terms_a, consts_a)
                bounds_before = _point_rect(
                    layout_a, _uid_of(entry_a[2].terminal_id),
                )
                detail = f"← {new_label} (was {old_label})"
                path_before = _wire_path(
                    layout_a, wires_a, _uid_of(entry_a[3].terminal_id),
                )

        # Locality: the SINK node's own already-stamped context (see
        # ``_collect_elements``) — removed uses the base-side map (a), added/
        # modified the head-side map (b), matching every other locality
        # convention in this module. None for the VI's own connector-pane
        # boundary terminal (it's not in either map — correctly top-level).
        loc_map = a if change == "removed" else b
        loc_entry = loc_map.get(_uid_of(dest_end.node_id))
        container_uid = loc_entry.container_uid if loc_entry is not None else None
        frame_path = loc_entry.frame_path if loc_entry is not None else None

        changes.append(ElementChange(
            _uid_of(dest_end.terminal_id), dest_end.terminal_id, "wire", change,
            sink_label, bounds, bounds_before=bounds_before, detail=detail,
            path=path, path_before=path_before,
            container_uid=container_uid, frame_path=frame_path,
            endpoints=endpoints,
        ))
    return changes


def _constant_locality(
    c: Constant, elements: dict[str, _ElemInfo],
) -> tuple[str | None, str | None]:
    """A constant's locality from its IMMEDIATE parent/frame (``Constant``
    only carries ONE level of containment — ``parent``/``frame`` — unlike
    Operations, which ``_collect_elements`` walks with the FULL ancestor
    context already threaded through). Looks up that parent structure's own
    already-stamped ``_ElemInfo`` and extends it by one segment if the
    parent is an interactive structure (Case/stacked Sequence, matching
    ``_is_interactive_struct``); a Loop/flat-Sequence parent contributes no
    segment, so the constant just inherits that structure's own locality
    unchanged (identical scoping to ``_collect_elements``'s own recursion)."""
    if c.parent is None:
        return None, None
    struct_uid = _uid_of(c.parent)
    entry = elements.get(struct_uid)
    if entry is None:
        return None, None
    if _is_interactive_struct(entry.op):
        return struct_uid, _extend_frame_path(entry.frame_path, struct_uid, c.frame)
    return entry.container_uid, entry.frame_path


# ── Frame set diff (frame added/removed/value-changed) ─────────────────
#
# Within a Case/Sequence structure matched across versions (same UID, or
# matched by ``_match_elements``'s exact/fuzzy dataflow identity), diff the
# FRAME SET by ``Frame.uid``. Individual node/wire changes inside a frame are
# already reported by the passes above (per-node UID matching, per-sink wire
# diff) — this only reports the frame CONTAINER itself: a whole frame
# appearing/disappearing, or the same frame's selector/index changing.


def _frame_display(frame: Frame, op: Operation) -> str:
    """Human label for one frame — the SAME faithful, enum-aware text the
    netlist/tree produces. A case frame goes through ``op_walk._selector_label``
    (resolving the owning structure's selector ``lv_type`` exactly as
    ``netlist._build_case_scope`` does, so an enum value reads as its item name,
    an error cluster as ``No Error``/``Error``, etc.); a sequence frame is its
    index. This keeps the flat-list label (``ElementChange.label``) and the tree
    frame label (rendered via ``_selector_label`` too) in agreement."""
    if isinstance(op, DisableStructureOperation) and isinstance(frame, CaseFrame):
        # Disable frames ARE CaseFrames, but their selector_value already IS the
        # display text ("Enabled"/"Disabled"/"Frame N") and their is_default
        # means "the active/compiled-in frame", not a catch-all default — so
        # they must NOT go through _selector_label's is_default→"Default" branch
        # (mirror netlist._build_disabled_scope, which bypasses it for the same
        # reason).
        return str(frame.selector_value)
    if isinstance(frame, CaseFrame):
        lv_type = (
            _selector_lv_type(op, op.selector_terminal)
            if isinstance(op, CaseOperation) else None
        )
        is_error = bool(lv_type and _is_error_cluster(lv_type))
        return _selector_label(frame, lv_type, is_error)
    if isinstance(frame, SequenceFrame):
        return str(frame.index)
    if isinstance(frame, EventFrame):
        return frame.event_label or "event frame"
    return "frame"


def _frame_element_label(op: Operation) -> str:
    """Words naming the ELEMENT a frame change IS, by the owning structure's
    kind ("case frame"/"event frame"/…) — so the flat-list row reads glyph+words
    like every other row, with the frame's value carried as subtext."""
    if isinstance(op, CaseOperation):
        return "case frame"
    if isinstance(op, SequenceOperation):
        return "sequence frame"
    if isinstance(op, EventOperation):
        return "event frame"
    if isinstance(op, DisableStructureOperation):
        return "disable frame"
    return "frame"


def _frame_key(frame: Frame) -> str:
    """Stable key for matching a frame across versions: LabVIEW's own frame
    ``uid`` when the parser recorded one (currently only ``SequenceFrame`` —
    see parser/nodes/sequence.py; a ``CaseFrame``'s heap format carries no
    per-frame uid, so the parser never sets one — always None today). Falls
    back to the frame's selector/index VALUE, which DEGRADES a genuine
    in-place value edit (e.g. LabVIEW's "add value to this case") into a
    remove+add, because the frame's identity is then indistinguishable from
    its value. Documented, not hidden — a real frame uid always wins when
    present.

    An ``EventFrame`` has no uid AND a RESOLUTION-dependent label (the displayed
    frame reconstructs faithfully; others degrade to ``<unknown event ...>``), so
    keying on its label would make a mere relabel read as remove+add. Key on the
    stable leading ``[N]`` index instead."""
    if frame.uid is not None:
        return frame.uid
    if isinstance(frame, EventFrame):
        s = frame.event_label.lstrip()
        if s.startswith("[") and "]" in s:
            idx = s[1:s.index("]")]
            if idx.isdigit():
                return f"~[{idx}]"
        return f"~{frame.event_label}"
    return f"~{_frame_value(frame)}"


def _frame_value_changed(fa: Frame, fb: Frame) -> bool:
    """Whether two frames MATCHED by ``_frame_key`` differ in their selector/
    index content — the ``kind="value"`` detector. When matched by real uid
    (sequence frames) this catches e.g. a stacked-sequence reorder or a
    case's ``is_default``/ranges/strings changing while the uid stayed put;
    when matched by the value-based fallback key, the key ITSELF encodes
    ``selector_value``/``index``, so those are equal by construction and only
    the remaining fields (``is_default``, ranges, strings) can differ."""
    if isinstance(fa, CaseFrame) and isinstance(fb, CaseFrame):
        return (
            fa.selector_value != fb.selector_value
            or fa.is_default != fb.is_default
            or fa.selector_ranges != fb.selector_ranges
            or fa.selector_strings != fb.selector_strings
        )
    if isinstance(fa, SequenceFrame) and isinstance(fb, SequenceFrame):
        return fa.index != fb.index
    if isinstance(fa, EventFrame) and isinstance(fb, EventFrame):
        # Matched by stable [N] index (see _frame_key); the label is
        # resolution-dependent, so a label-only diff is our text changing, not
        # the VI's event — never report it as a frame value change.
        return False
    return False


def _frame_locality(
    struct_uid: str, op: Operation, outer_frame_path: str | None, value: object,
) -> tuple[str, str | None]:
    """Locality for a frame add/remove/value-change (task: Part B). The
    CONTAINER is always the owning structure itself — the frame's identity is
    meaningless without it — and the frame_path extends that structure's own
    OUTER path (``outer_frame_path``, already stamped on its ``_ElemInfo`` by
    ``_collect_elements``) with ITS OWN segment, but ONLY when the structure
    is interactive (Case/stacked Sequence — see ``_is_interactive_struct``):
    those are the only kinds with a real rendered ``lv-frame`` group a viewer
    could ever correlate a token against. A flat sequence's frame change still
    gets a real ``container_uid`` (that structure exists and is addressable)
    but keeps its outer frame_path unextended — there is no separate hidden
    group a flat-sequence frame token could ever match."""
    if _is_interactive_struct(op):
        return struct_uid, _extend_frame_path(outer_frame_path, struct_uid, value)
    return struct_uid, outer_frame_path


def _frame_node_uids(frame: Frame) -> set[str]:
    """Every node UID contained anywhere in ``frame`` — recursing into nested
    structures' frames AND loop bodies (``inner_nodes``), matching how
    ``_collect_elements`` walks the tree. This set is the frame's CONTENT
    identity: a frame whose value changed but whose contents stayed keeps (most
    of) this set, letting two frames be recognised as the-same-frame-renamed."""
    uids: set[str] = set()

    def rec(ops: Sequence[Operation]) -> None:
        for op in ops:
            uids.add(_uid_of(op.id))
            for fr in _frames_of(op) or []:
                rec(fr.operations)
            rec(op.inner_nodes)

    rec(frame.operations)
    return uids


def _mk_frame_change(
    op: Operation, entry: _ElemInfo, struct_uid: str, frame: Frame,
    kind: str, change: str, detail: str | None = None,
    frame_path_before: str | None = None,
) -> ElementChange:
    """Assemble one frame-set ElementChange — the SINGLE place a frame change's
    key, id, locality, and label are built, reused for added/removed/modified
    across every frame-bearing structure kind. ``frame_path_before`` is the
    before-side addressing of a value change (see the field doc); None for
    add/remove and same-value changes."""
    key = _frame_key(frame)
    container_uid, frame_path = _frame_locality(
        struct_uid, op, entry.frame_path, _frame_value(frame),
    )
    return ElementChange(
        key, f"{op.id}::frame::{key}", kind, change,
        _frame_display(frame, op), detail=detail,
        container_uid=container_uid, frame_path=frame_path,
        frame_path_before=frame_path_before,
        element=_frame_element_label(op),
    )


def _pair_frames_by_content(
    only_a: list[Frame], only_b: list[Frame], matchmap: dict[str, str],
) -> list[tuple[Frame, Frame]]:
    """Pair leftover before/after frames that are the SAME frame with a changed
    value, recognised by shared CONTENT: a before-frame's node UIDs, mapped
    through the dataflow match map (``base->head``; identity when a node kept its
    UID — the common case), overlapping an after-frame's node UIDs. Greedy by
    most shared nodes with a deterministic tiebreak; each frame used at most
    once. Zero overlap ⇒ not paired here (a genuine add/remove)."""
    b_sig = [(fb, _frame_node_uids(fb)) for fb in only_b]
    scored: list[tuple[int, str, str, Frame, Frame]] = []
    for fa in only_a:
        a_uids = {matchmap.get(u, u) for u in _frame_node_uids(fa)}
        for fb, b_uids in b_sig:
            shared = len(a_uids & b_uids)
            if shared:
                scored.append(
                    (shared, str(_frame_value(fa)), str(_frame_value(fb)), fa, fb),
                )
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    used: set[int] = set()
    pairs: list[tuple[Frame, Frame]] = []
    for cand in scored:
        fa, fb = cand[3], cand[4]
        if id(fa) in used or id(fb) in used:
            continue
        used.add(id(fa))
        used.add(id(fb))
        pairs.append((fa, fb))
    return pairs


def _struct_frame_changes(
    entry_a: _ElemInfo, entry_b: _ElemInfo, matchmap: dict[str, str],
) -> list[ElementChange]:
    """Diff one matched frame-bearing structure's FRAME SET across versions —
    uniformly for Case / Sequence / Event / (Conditional-/Diagram-)Disable: a
    whole frame added/removed, or the SAME frame's value changed. A value change
    the frame's own key can't see (a case ``1``->``0`` rename, an event relabel,
    a range extension) is recovered by CONTENT — the frame is matched by its
    contained nodes' (dataflow-)identity — so it reads as ONE modification
    instead of an add+remove. Does NOT touch the frame's contents (already
    reported by the node/wire passes; same don't-describe-twice rule)."""
    op_a, op_b = entry_a.op, entry_b.op
    if type(op_a) is not type(op_b):
        return []  # UID recycle across kinds — not a frame-set change
    frames_a, frames_b = _frames_of(op_a), _frames_of(op_b)
    if frames_a is None or frames_b is None:
        return []

    base_uid, head_uid = _uid_of(op_a.id), _uid_of(op_b.id)
    map_a: dict[str, Frame] = {_frame_key(f): f for f in frames_a}
    map_b: dict[str, Frame] = {_frame_key(f): f for f in frames_b}

    def value_change(fa: Frame, fb: Frame) -> ElementChange:
        # The change is stamped on the AFTER frame (fb); its before-side twin
        # (fa) addresses the SAME frame under the OLD value, so the viewer can
        # drive the before pane there while the after pane goes to fb.
        _, fp_before = _frame_locality(
            base_uid, op_a, entry_a.frame_path, _frame_value(fa),
        )
        return _mk_frame_change(
            op_b, entry_b, head_uid, fb, "value", "modified",
            detail=_transition(_frame_display(fa, op_a), _frame_display(fb, op_b)),
            frame_path_before=fp_before,
        )

    changes: list[ElementChange] = []
    # 1. Frames matched by key: a same-key field change (case is_default/ranges/
    #    strings, a stacked-sequence reorder) — otherwise unchanged.
    for key in sorted(map_a.keys() & map_b.keys(), key=_uid_sort):
        fa, fb = map_a[key], map_b[key]
        if _frame_value_changed(fa, fb):
            changes.append(value_change(fa, fb))
    # 2. Leftover frames: recover the-same-frame-renamed by content; the rest are
    #    genuine adds/removes.
    only_a: list[Frame] = [
        map_a[k] for k in sorted(map_a.keys() - map_b.keys(), key=_uid_sort)
    ]
    only_b: list[Frame] = [
        map_b[k] for k in sorted(map_b.keys() - map_a.keys(), key=_uid_sort)
    ]
    pairs = _pair_frames_by_content(only_a, only_b, matchmap)
    paired = {id(f) for pair in pairs for f in pair}
    for fa, fb in pairs:
        changes.append(value_change(fa, fb))
    for fa in only_a:
        if id(fa) not in paired:
            changes.append(
                _mk_frame_change(op_a, entry_a, base_uid, fa, "frame", "removed"),
            )
    for fb in only_b:
        if id(fb) not in paired:
            changes.append(
                _mk_frame_change(op_b, entry_b, head_uid, fb, "frame", "added"),
            )
    return changes


def _matched_struct_pairs(
    a: dict[str, _ElemInfo], b: dict[str, _ElemInfo],
    exact: dict[str, str], fuzzy: dict[str, str],
) -> list[tuple[_ElemInfo, _ElemInfo]]:
    """Every (base entry, head entry) pair of the SAME logical Case/Sequence
    structure across versions — same UID kept by LabVIEW, or matched by
    ``_match_elements``'s exact/fuzzy dataflow identity (a re-keyed
    structure) — so their FRAME SETS can be diffed. A mismatched type at a
    shared uid (LabVIEW recycling a uid for a different kind of node) is
    skipped — same guard as the constant modified-check's type guard."""
    pairs: list[tuple[_ElemInfo, _ElemInfo]] = []
    for base_uid in sorted(a.keys() & b.keys(), key=_uid_sort):
        ea, eb = a[base_uid], b[base_uid]
        if type(ea.op) is not type(eb.op):
            continue
        if _frames_of(ea.op) is not None:
            pairs.append((ea, eb))
    h2b = {**exact, **fuzzy}
    for base_uid in sorted(h2b, key=_uid_sort):
        head_uid = h2b[base_uid]
        ea, eb = a.get(base_uid), b.get(head_uid)
        if ea is None or eb is None:
            continue
        if type(ea.op) is not type(eb.op):
            continue
        if _frames_of(ea.op) is not None:
            pairs.append((ea, eb))
    return pairs


# ── Constant diff (task: constants as first-class change elements) ─────
#
# Constants participate in the change-map as ``kind="constant"`` elements at ANY
# nesting depth — added/removed/modified alike — so a constant added inside a new
# case frame highlights on the diagram (box + numbered badge), lists in the flat
# CHANGES/JSON with a count, and places in the netlist tree at its true
# containment, exactly like a node change. This REPLACES the old split where only
# a MODIFIED constant entered the map (as a fake ``kind="node"``) and added/
# removed constants went through a separate top-level-only, geometry-less text
# pass (``_diff_constants``/``ConstantChange``, since removed).
#
# A constant carries no stable UID (LabVIEW re-keys it), so identity is
# reconstructed like the node fuzzy-matcher: exact-UID first, then leftovers
# paired by NAME, by CONNECTION (the canonical consumer terminals it feeds — the
# only cross-version anchor a constant has), and by LOCALITY (frame_path). VALUE
# is the CLASSIFIER (paired + equal → unchanged/collapsed; paired + differing →
# modified), never the identity, so a value edit reads as one modified constant
# rather than a remove+add.
#
# This pass does NOT use the generic ``_correlate_by_keys`` ladder (which the
# terminal pass does): its final tier is a value+type+locality MULTISET
# CANCELLATION (identical unnamed constants in the same frame simply annihilate,
# emitting nothing), not the 1:1 bucket-pop-then-classify the helper models — plus
# a tier-0 cross-type-recycle skip and a tier-1 "anchored" gate that the helper
# has no notion of. Forcing it through the shared ladder would contort both, so it
# stays bespoke; the shared helper is for kinds with a plain key ladder.


def _const_type(c: Constant) -> str | None:
    """FAITHFUL type label for a constant, used both for display and for
    change-detection equality (never ``to_python()`` — see LAW in
    ``models.py``'s ``LVType.lv_label()``)."""
    return c.lv_type.lv_label() if c.lv_type else None


def _const_consumers(
    wires: list[Wire], vi_self: str, base_side: bool, b_of_h: Mapping[str, str],
) -> dict[str, set[tuple[str, object]]]:
    """Constant-uid -> the set of its canonical consumers ``(node_uid, term)``.

    A constant appears in the wire table as a wire SOURCE; its consumers are the
    ``dest`` ends. Consumer node uids are canonicalized to BASE space (head uids
    mapped back through the node matching ``b_of_h``) and the VI's own
    connector-pane self node to the fixed ``__self__`` sentinel, so a constant's
    connection identity survives a re-key/requalification. The consumer TERMINAL
    (index when known, else its trailing uid) distinguishes two constants feeding
    different inputs of the SAME node."""
    out: dict[str, set[tuple[str, object]]] = {}
    for w in wires:
        dest_node = w.dest.node_id
        if dest_node == vi_self:
            cnode = "__self__"
        else:
            cuid = _uid_of(dest_node)
            cnode = cuid if base_side else b_of_h.get(cuid, cuid)
        term: object = (
            w.dest.index if w.dest.index is not None
            else _uid_of(w.dest.terminal_id)
        )
        out.setdefault(_uid_of(w.source.node_id), set()).add((cnode, term))
    return out


def _constant_changes(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph, va: str, vb: str,
    a: dict[str, _ElemInfo], b: dict[str, _ElemInfo],
    exact: dict[str, str], fuzzy: dict[str, str],
    layout_a: Layout | None, layout_b: Layout | None,
    wires_a: list[Wire], wires_b: list[Wire],
) -> list[ElementChange]:
    """All constant changes as ``kind="constant"`` ``ElementChange``s. See the
    section header above for the identity model."""
    consts_a = {_uid_of(c.id): c for c in graph_a.get_constants(va)}
    consts_b = {_uid_of(c.id): c for c in graph_b.get_constants(vb)}

    b_of_h = {h: base for base, h in {**exact, **fuzzy}.items()}
    anchored: set[str] = (
        (a.keys() & b.keys()) | set(exact) | set(fuzzy) | {"__self__"}
    )
    cons_a = _const_consumers(wires_a, va, True, b_of_h)
    cons_b = _const_consumers(wires_b, vb, False, b_of_h)

    # Added/removed constants carry their incident wire "chain" (the feed wire
    # to/from the consumer), drawn in the add/remove colour on selection exactly
    # like an added/removed NODE's chain (that wire is itself new/gone). A
    # MODIFIED constant's wiring is unchanged, so it gets no chain — same as a
    # modified node. The VALUE (old→new for modified) is stamped into ``detail``
    # — it survives in the JSON and the ``--verbose`` text tree — but the VIEWER
    # LIST deliberately doesn't render it (a string value can be arbitrarily
    # long); the concise row is just the type label, the diagram panes show the
    # actual values.
    def added(cb: Constant) -> ElementChange:
        uid = _uid_of(cb.id)
        cu, fp = _constant_locality(cb, b)
        return ElementChange(
            uid, cb.id, "constant", "added", _const_label(cb),
            _node_bounds(layout_b, uid), detail=_value_disp(cb.value),
            chain_paths=_chain_paths(layout_b, wires_b, _node_incident(uid)),
            container_uid=cu, frame_path=fp,
        )

    def removed(ca: Constant) -> ElementChange:
        uid = _uid_of(ca.id)
        cu, fp = _constant_locality(ca, a)
        return ElementChange(
            uid, ca.id, "constant", "removed", _const_label(ca),
            _node_bounds(layout_a, uid), detail=_value_disp(ca.value),
            chain_paths=_chain_paths(layout_a, wires_a, _node_incident(uid)),
            container_uid=cu, frame_path=fp,
        )

    def modified(ca: Constant, cb: Constant) -> ElementChange:
        uid = _uid_of(cb.id)
        cu, fp = _constant_locality(cb, b)
        return ElementChange(
            uid, cb.id, "constant", "modified", _const_label(cb),
            _node_bounds(layout_b, uid), bounds_before=_node_bounds(layout_a, uid),
            detail=_transition(_value_disp(ca.value), _value_disp(cb.value)),
            container_uid=cu, frame_path=fp,
        )

    changes: list[ElementChange] = []

    # ── Tier 0: same UID kept by LabVIEW — same constant. ──
    for uid in sorted(consts_a.keys() & consts_b.keys(), key=_uid_sort):
        ca, cb = consts_a[uid], consts_b[uid]
        if _const_type(ca) != _const_type(cb):
            continue  # UID recycle across types — not a constant edit (skip).
        if repr(ca.value) != repr(cb.value):
            changes.append(modified(ca, cb))

    left_a = [consts_a[u] for u in sorted(consts_a.keys() - consts_b.keys(),
                                          key=_uid_sort)]
    left_b = [consts_b[u] for u in sorted(consts_b.keys() - consts_a.keys(),
                                          key=_uid_sort)]

    # ── Tier 1: pair leftovers by reconstructed identity (name + connection +
    # locality), 1:1. VALUE is NOT in the key, so a value-only edit still pairs
    # (→ modified); equal value collapses to unchanged (pure re-key). ──
    def ident(
        c: Constant, cons: dict[str, set[tuple[str, object]]],
        elems: dict[str, _ElemInfo],
    ) -> tuple[tuple, bool]:
        ccons = frozenset(cons.get(_uid_of(c.id), set()))
        _, fp = _constant_locality(c, elems)
        anchored_here = c.label is not None or any(n in anchored for n, _ in ccons)
        return (c.label, fp, ccons), anchored_here

    b_by_key: dict[tuple, list[Constant]] = defaultdict(list)
    for cb in left_b:
        key, anch = ident(cb, cons_b, b)
        if anch:
            b_by_key[key].append(cb)
    paired_a: set[str] = set()
    paired_b: set[str] = set()
    for ca in left_a:
        key, anch = ident(ca, cons_a, a)
        if not anch or not b_by_key.get(key):
            continue
        cb = b_by_key[key].pop(0)
        paired_a.add(_uid_of(ca.id))
        paired_b.add(_uid_of(cb.id))
        if _const_type(ca) != _const_type(cb) or repr(ca.value) != repr(cb.value):
            changes.append(modified(ca, cb))
        # else: identical re-key → unchanged (collapse, emit nothing).

    rem_a = [c for c in left_a if _uid_of(c.id) not in paired_a]
    rem_b = [c for c in left_b if _uid_of(c.id) not in paired_b]

    # ── Tier 2: value+type multiset collapse, scoped per frame_path — the old
    # unnamed-constant behaviour, now locality-aware (identical constants in the
    # SAME frame cancel as unchanged; anything left is a real add/remove). ──
    def vkey(c: Constant, elems: dict[str, _ElemInfo]) -> tuple:
        _, fp = _constant_locality(c, elems)
        return (repr(c.value), _const_type(c) or "unknown", fp)

    b_by_vkey: dict[tuple, list[Constant]] = defaultdict(list)
    for cb in rem_b:
        b_by_vkey[vkey(cb, b)].append(cb)
    for ca in rem_a:
        bucket = b_by_vkey.get(vkey(ca, a))
        if bucket:
            bucket.pop(0)  # cancels a same-value/type/frame added constant.
        else:
            changes.append(removed(ca))
    for cb in sorted((c for lst in b_by_vkey.values() for c in lst),
                     key=lambda c: _uid_sort(_uid_of(c.id))):
        changes.append(added(cb))

    return changes


def _fp_terminals(graph: InMemoryVIGraph, vi: str, direction: str) -> list[FPTerminal]:
    """The VI's own FP controls (``direction=="input"``) or indicators
    (``"output"``) — every one with a BD terminal, connector-pane or not
    (``public_only=False``). Filtered to ``FPTerminal`` so the correlation keys
    (``fp_dco_uid``, ``is_indicator``) are available; the same enumeration the
    renderer walks (``render/scene.py``)."""
    terms = (graph.get_inputs(vi, public_only=False) if direction == "input"
             else graph.get_outputs(vi, public_only=False))
    return [t for t in terms if isinstance(t, FPTerminal)]


_T = TypeVar("_T")


def _correlate_by_keys(
    items_a: list[_T], items_b: list[_T],
    key_fns: Sequence[Callable[[_T], object | None]],
) -> tuple[list[tuple[_T, _T]], list[_T], list[_T]]:
    """Correlate two item lists by a LADDER of key functions. For each key in
    order: bucket the still-unmatched B items by that key, then pop a 1:1 match
    for each still-unmatched A item sharing the key (a ``None`` key never
    matches). Later keys only see what earlier ones left. Returns
    ``(pairs, leftover_a, leftover_b)`` — the shared skeleton of every "match by
    exact identity, then fall back to a weaker key, then treat the rest as
    add/remove" pass (e.g. terminals: FP-DCO-uid then name)."""
    pairs: list[tuple[_T, _T]] = []
    rem_a, rem_b = list(items_a), list(items_b)
    for key_fn in key_fns:
        buckets: dict[object, list[_T]] = defaultdict(list)
        for tb in rem_b:
            k = key_fn(tb)
            if k is not None:
                buckets[k].append(tb)
        next_a, matched_b = [], set()
        for ta in rem_a:
            k = key_fn(ta)
            bucket = buckets.get(k) if k is not None else None
            if bucket:
                tb = bucket.pop(0)
                pairs.append((ta, tb))
                matched_b.add(id(tb))
            else:
                next_a.append(ta)
        rem_a = next_a
        rem_b = [tb for tb in rem_b if id(tb) not in matched_b]
    return pairs, rem_a, rem_b


def _terminal_changes(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph, va: str, vb: str,
    layout_a: Layout | None, layout_b: Layout | None,
    wires_a: list[Wire], wires_b: list[Wire],
) -> list[ElementChange]:
    """FP control/indicator add / remove / retype / rename as
    ``kind="terminal"`` ``ElementChange``s.

    Controls/indicators live on the VINode as ``FPTerminal``s (not operations),
    so the node/constant passes never see them — a whole added or removed
    control is otherwise invisible, only its wire shows. Correlate the SAME
    control across versions by its FRONT-PANEL DCO uid (``fp_dco_uid`` — the FP
    object's identity, which survives a caption RENAME and, empirically, stays
    stable across versions while the BD terminal uid can churn), then by
    ``name`` for any whose DCO uid churned/absent. A matched pair with a changed
    faithful type label is a RETYPE and with a changed ``name`` a RENAME — both
    ``modified`` carrying the old→new in ``detail``. Genuine renames always keep
    the DCO uid, so they're caught by the uid tier; a leftover pair where BOTH
    the uid AND the name changed is indistinguishable from delete+add, so it's
    reported as add+remove (never a manufactured rename). ``bounds`` come from
    each side's own BD-terminal uid in ``Layout.node_bounds`` (the same lookup
    ``render/scene.py`` uses to draw the terminal box). Frame-parking of a
    terminal placed inside a case/sequence frame is a v1 gap — the box still
    highlights from its absolute bounds, it just isn't hidden with its frame.
    """
    def word(t: FPTerminal) -> str:
        return "indicator" if t.is_indicator else "control"

    def added(tb: FPTerminal) -> ElementChange:
        return ElementChange(
            _uid_of(tb.id), tb.id, "terminal", "added", tb.name or "(unnamed)",
            _node_bounds(layout_b, _uid_of(tb.id)), element=word(tb),
            chain_paths=_chain_paths(layout_b, wires_b, _term_incident(_uid_of(tb.id))),
        )

    def removed(ta: FPTerminal) -> ElementChange:
        return ElementChange(
            _uid_of(ta.id), ta.id, "terminal", "removed", ta.name or "(unnamed)",
            _node_bounds(layout_a, _uid_of(ta.id)), element=word(ta),
            chain_paths=_chain_paths(layout_a, wires_a, _term_incident(_uid_of(ta.id))),
        )

    def modified(ta: FPTerminal, tb: FPTerminal) -> ElementChange | None:
        """A retype and/or rename, or None when the pair is unchanged."""
        type_a = ta.lv_type.lv_label() if ta.lv_type else "Any"
        type_b = tb.lv_type.lv_label() if tb.lv_type else "Any"
        name_a, name_b = ta.name, tb.name
        if type_a != type_b and name_a != name_b:
            detail = _transition(f"{name_a} : {type_a}", f"{name_b} : {type_b}")
        elif type_a != type_b:
            detail = _transition(type_a, type_b)
        elif name_a != name_b:
            detail = _transition(name_a, name_b)
        else:
            return None
        return ElementChange(
            _uid_of(tb.id), tb.id, "terminal", "modified", tb.name or "(unnamed)",
            _node_bounds(layout_b, _uid_of(tb.id)),
            bounds_before=_node_bounds(layout_a, _uid_of(ta.id)),
            detail=detail, element=word(tb),
        )

    # Correlate per direction by a key ladder: FP DCO uid (the front-panel
    # object identity — survives a rename), then name (for any whose uid
    # churned). Matched pairs → retype/rename or unchanged; leftovers →
    # add/remove. The bucket-pop tiering lives in ``_correlate_by_keys``.
    changes: list[ElementChange] = []
    for direction in ("input", "output"):
        pairs, only_a, only_b = _correlate_by_keys(
            _fp_terminals(graph_a, va, direction),
            _fp_terminals(graph_b, vb, direction),
            [lambda t: t.fp_dco_uid or None, lambda t: t.name or None],
        )
        for ta, tb in pairs:
            mc = modified(ta, tb)
            if mc is not None:
                changes.append(mc)
        changes.extend(added(tb) for tb in only_b)
        changes.extend(removed(ta) for ta in only_a)

    return changes


def _matched_node_pairs(
    a: dict[str, _ElemInfo], b: dict[str, _ElemInfo],
    exact: dict[str, str], fuzzy: dict[str, str],
) -> list[tuple[_ElemInfo, _ElemInfo]]:
    """Every (base, head) pair of the SAME logical leaf NODE across versions —
    same uid kept by LabVIEW, or exact/fuzzy-matched — with matching op types
    (a recycled-uid type flip is skipped). The counterpart of
    ``_matched_struct_pairs`` for node-level (not structure) diffs."""
    pairs: list[tuple[_ElemInfo, _ElemInfo]] = []
    seen: set[tuple[str, str]] = set()
    cand = [(u, u) for u in a.keys() & b.keys()]
    cand += list({**exact, **fuzzy}.items())
    for base_uid, head_uid in sorted(cand, key=lambda p: _uid_sort(p[0])):
        ea, eb = a.get(base_uid), b.get(head_uid)
        if ea is None or eb is None or (base_uid, head_uid) in seen:
            continue
        seen.add((base_uid, head_uid))
        if ea.kind == "node" and eb.kind == "node" and type(ea.op) is type(eb.op):
            pairs.append((ea, eb))
    return pairs


def _term_delta_detail(
    added: list[Terminal], removed: list[Terminal],
) -> str:
    """``+field, -param`` summary of a node's added/removed terminals — naming
    each by its resolved display name (stamped once at load, so an nMux field
    reads the same here as in a wire change), falling back to ``dir[index]``
    when unnamed."""
    def lab(t: Terminal) -> str:
        return _terminal_display_name(t) or t.name or f"{t.direction}[{t.index}]"
    return ", ".join(
        [f"+{lab(t)}" for t in added] + [f"-{lab(t)}" for t in removed]
    )


def _node_terminal_changes(
    a: dict[str, _ElemInfo], b: dict[str, _ElemInfo],
    exact: dict[str, str], fuzzy: dict[str, str],
    layout_a: Layout | None, layout_b: Layout | None,
) -> tuple[list[ElementChange], set[str]]:
    """A MATCHED node whose OWN terminal SET changed — a variadic node
    (Bundle/Unbundle-By-Name reading more/fewer fields, Build Array), an Invoke/
    Property node whose method/property list changed, or a subVI whose connector
    pane changed — reported as ONE node ``modified`` with the field/param delta.

    Correlated per (direction, index) — the connector position — via
    ``_correlate_by_keys``, so a re-keyed but structurally identical node yields
    nothing (no false positive). A pure retype/rename at a STABLE position is
    deliberately NOT reported here: that's a noisier, separate concern (e.g. a
    cluster-field class rename would otherwise flag every node using the class).

    Returns the changes AND the uids of the added/removed terminals, so the
    caller feeds them into ``changed_terms`` — the new/gone field wires then fold
    into this node's story instead of surfacing as standalone wire changes,
    exactly as an added/removed node's incident wires already do."""
    changes: list[ElementChange] = []
    changed_terms: set[str] = set()
    for ea, eb in _matched_node_pairs(a, b, exact, fuzzy):
        _, removed, added = _correlate_by_keys(
            ea.op.terminals, eb.op.terminals, [lambda t: (t.direction, t.index)],
        )
        if not (added or removed):
            continue
        uid_h, uid_b = _uid_of(eb.op.id), _uid_of(ea.op.id)
        changes.append(ElementChange(
            uid_h, eb.op.id, "node", "modified", _elem_label(eb.op, "node"),
            _node_bounds(layout_b, uid_h),
            bounds_before=_node_bounds(layout_a, uid_b),
            detail=_term_delta_detail(added, removed),
            container_uid=eb.container_uid, frame_path=eb.frame_path,
        ))
        changed_terms |= {_uid_of(t.id) for t in added}
        changed_terms |= {_uid_of(t.id) for t in removed}
    return changes, changed_terms


def diff_uid(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    vi_name_a: str, vi_name_b: str,
) -> ChangeMap:
    """Build a UID-keyed change-map for two VI versions.

    Unlike matching operations by name/count (which misses added/removed
    instances of repeated names), this matches every node and structure by
    its stable LabVIEW UID. The UIDs are exactly those the renderer emits as
    ``data-node`` / ``data-lv-struct``, so the map binds onto the rendered SVG
    with no reconciliation. Wire endpoints and modified-node detection layer
    on top of this (see tasks #10/#11). This is the single source of truth
    for the visual overlay (``--format html``/``json``) AND the ``diff``
    TEXT report (both the concise default and ``--verbose`` -- see
    ``format_diff``/``_composition_tree``).
    """
    va = graph_a.resolve_vi_name(vi_name_a)
    vb = graph_b.resolve_vi_name(vi_name_b)

    a: dict[str, _ElemInfo] = {}
    b: dict[str, _ElemInfo] = {}
    _collect_elements(graph_a.get_operations(va), a)
    _collect_elements(graph_b.get_operations(vb), b)

    # Geometry sidecar (present only when the graph was loaded with layout=True).
    # node_bounds is keyed by the raw heap uid, which is exactly our trailing UID.
    layout_a = graph_a.get_layout(va)
    layout_b = graph_b.get_layout(vb)

    # Match the UID-set leftovers by dataflow, and COLLAPSE every match to
    # unchanged. EXACT = identical wiring: the same node LabVIEW re-keyed. FUZZY =
    # the same NODE (same operation, mostly-same wiring) whose difference is a
    # changed WIRE, not a changed node — so at the node level it's unchanged too.
    # The wire delta itself is a WIRING change, surfaced only by a wire-level diff
    # (task #10); we do not fake it as a node modification here.
    wires_a = graph_a.get_wires(va)
    wires_b = graph_b.get_wires(vb)
    exact, fuzzy = _match_elements(a, b, _incident(wires_a), _incident(wires_b))
    matched_a = exact.keys() | fuzzy.keys()
    matched_b = set(exact.values()) | set(fuzzy.values())

    cmap = ChangeMap()
    # Added: head-only node/structure with no dataflow counterpart in base.
    # Its "chain" — every wire incident to it — is drawn in the add color.
    for uid in b.keys() - a.keys() - matched_b:
        entry = b[uid]
        op, kind = entry.op, entry.kind
        # chain_paths (wire-route geometry) is only meaningful for a NODE's
        # own incident wires; a structure's "chain" would be every wire
        # anywhere inside it (task #27 -- viewer-only geometry, noise even
        # in the map, and never used for a structure highlight).
        chain = (_chain_paths(layout_b, wires_b, _node_incident(uid))
                 if kind == "node" else None)
        cmap.changes.append(
            ElementChange(uid, op.id, kind, "added", _elem_label(op, kind),
                          _node_bounds(layout_b, uid),
                          chain_paths=chain,
                          container_uid=entry.container_uid,
                          frame_path=entry.frame_path)
        )
    # Removed: base-only node/structure with no dataflow counterpart in head.
    for uid in a.keys() - b.keys() - matched_a:
        entry = a[uid]
        op, kind = entry.op, entry.kind
        chain = (_chain_paths(layout_a, wires_a, _node_incident(uid))
                 if kind == "node" else None)
        cmap.changes.append(
            ElementChange(uid, op.id, kind, "removed", _elem_label(op, kind),
                          _node_bounds(layout_a, uid),
                          chain_paths=chain,
                          container_uid=entry.container_uid,
                          frame_path=entry.frame_path)
        )
    # Constant changes (added/removed/modified), as ``kind="constant"`` elements
    # at any nesting depth — matched by name/connection/locality, classified by
    # value (see ``_constant_changes``). Reuses the exact/fuzzy node matching for
    # cross-version consumer identity.
    cmap.changes.extend(_constant_changes(
        graph_a, graph_b, va, vb, a, b, exact, fuzzy,
        layout_a, layout_b, wires_a, wires_b,
    ))

    # FP control/indicator changes (added/removed/retyped/renamed), as
    # ``kind="terminal"`` elements. These live on the VINode (not as operations),
    # so they're outside the node/constant passes; correlated by front-panel DCO
    # uid then name (see ``_terminal_changes``).
    cmap.changes.extend(_terminal_changes(
        graph_a, graph_b, va, vb, layout_a, layout_b, wires_a, wires_b,
    ))

    # Node terminal-SET changes: a matched node (Bundle/Unbundle-By-Name, an
    # Invoke/Property node, a subVI) whose own field/param terminals were
    # added/removed — reported as a node ``modified`` with the delta, its new/
    # gone terminals folding their wires in (below).
    node_term_changes, node_changed_terms = _node_terminal_changes(
        a, b, exact, fuzzy, layout_a, layout_b,
    )
    cmap.changes.extend(node_term_changes)

    # Sub-node terminal uids the wire diff can't infer from node membership
    # (they hang off a node that IS in ``unchanged`` — an FP terminal off the VI
    # ``__self__`` node, or a matched node's own field terminal). Their new/gone
    # wires fold into the owning element's story. Everything else the wire diff
    # suppresses (added/removed nodes, constants, tunnels) falls out of "endpoint
    # node not in ``unchanged``" — no owner enumeration needed.
    changed_terms = {c.uid for c in cmap.changes
                     if c.kind == "terminal" and c.change in ("added", "removed")}
    changed_terms |= node_changed_terms

    # Wire endpoint changes (#10): for every input terminal on an unchanged
    # node, compare its effective (tunnel-contracted) source across versions.
    # Reuses the exact/fuzzy node matching computed above.
    cmap.changes.extend(_wire_changes(
        graph_a, graph_b, va, vb, a, b, exact, fuzzy, layout_a, layout_b,
        changed_terms,
    ))

    # Frame set changes: within every Case/Sequence structure matched across
    # versions (same uid, or exact/fuzzy dataflow match), a whole frame
    # added/removed, or the same frame's selector/index value changed.
    for entry_a, entry_b in _matched_struct_pairs(a, b, exact, fuzzy):
        cmap.changes.extend(_struct_frame_changes(entry_a, entry_b, {**exact, **fuzzy}))

    # Common UIDs and all matched pairs are unchanged at the node level — a node
    # wrapped in a new case, moved, re-keyed, or with only a wire added/removed is
    # not itself a changed node. EXCEPT a node whose own terminal SET changed:
    # ``_node_terminal_changes`` emitted a ``modified`` for it, so it's not in the
    # unchanged tally. (A pure config/retype at a stable terminal position is
    # still deferred — it must distinguish a genuine reconfigure from a UID
    # recycle, and would reintroduce class-rename noise.)
    _node_modified = {c.uid for c in cmap.changes
                      if c.kind == "node" and c.change == "modified"}
    cmap.common_node_uids = sorted(
        (uid for uid in a.keys() & b.keys()
         if a[uid].kind == "node" and uid not in _node_modified),
        key=_uid_sort,
    )
    # Provisional display order: structures first, then added < removed <
    # modified, then by UID. This is only a deterministic TIEBREAK now -- the
    # real, user-facing order is the tree's own containment order, applied
    # next.
    _rank = {"added": 0, "removed": 1, "modified": 2}
    cmap.changes.sort(
        key=lambda c: (c.kind != "structure", _rank.get(c.change, 3),
                       _uid_sort(c.uid))
    )
    _reorder_by_tree(cmap, graph_a, graph_b, va, vb)
    return cmap


def _reorder_by_tree(
    cmap: ChangeMap,
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    va: str, vb: str,
) -> None:
    """Reorder ``cmap.changes`` in place to match the STRUCTURAL (containment)
    order the tree (``_netlist_diff``/``netlist_diff_rows``) actually renders
    changes in -- so the flat ``CHANGES`` list (``cmap.to_dict``), the tree,
    and the on-diagram ``.hl-num`` badges all number ``1, 2, 3, ...`` reading
    top-to-bottom, instead of the tree's containment-first traversal
    scrambling numbers assigned by the structures-then-uid sort above.

    Builds the SAME netlist-diff rows the tree renders (constants and terminals
    now carry real uids and are ordered alongside everything else), takes the
    order in which change uids FIRST appear walking those rows top-to-bottom
    (pre-order containment, per ``_netlist_diff``'s own ``_sort_key``/
    ``source_order`` -- dataflow/topological order is NOT used here), and
    re-sorts ``cmap.changes`` by it. A change with no row at all (should not
    happen once every kind the tree renders is accounted for, but tolerated
    defensively) keeps its relative position from the tiebreak sort above,
    trailing after every change that DOES have a row -- ``list.sort`` is
    stable, so ties (including the shared "no row" bucket) preserve the
    incoming relative order.

    Must run INSIDE ``diff_uid`` (not deferred to ``format_diff``/
    ``netlist_diff_rows``): those helpers -- and the standalone probe script
    used to verify this fix -- read ``cmap.changes`` straight from a
    ``diff_uid()`` call, so the reorder has to be baked into the ChangeMap
    itself for the flat list and the tree to ever agree.
    """
    if not cmap.changes:
        return  # nothing to reorder -- skip building the netlist for nothing
    rows = _netlist_diff(graph_a, graph_b, va, vb, cmap, detailed=False)
    tree_order: dict[str, int] = {}
    for r in rows:
        if r.uid is not None and r.uid not in tree_order:
            tree_order[r.uid] = len(tree_order)
    past_every_uid = len(tree_order)
    cmap.changes.sort(key=lambda c: tree_order.get(c.uid, past_every_uid))


# ── Diff TEXT report: ONE recursive netlist-form tree, concise default +
# --verbose, both projected from the same UID-keyed ChangeMap ──────────
#
# ``format_diff`` is the sole ``lvkit diff`` TEXT entry point. Both tiers
# read the SAME ``diff_uid()`` ChangeMap -- the denoised, UID-keyed engine
# that also backs ``--format json``/``html`` -- so the text can never show a
# change the viewer doesn't (or vice versa), and the SAME containment
# locality (``container_uid``/``frame_path``) drives both the text tree here
# and the viewer's Tree toggle (``render/templates/diff_viewer.html``), so
# they can never drift apart. There are no ``Operations:``/``Wiring:``/
# ``Structures:`` sections any more -- every change reads INLINE (a change
# GUTTER +/-/~ in column 0, then netlist SYNTAX -- an ``instance_line`` for a
# node, a ``scope_header`` for a structure) at whatever depth its own
# containment puts it, recursing into structures/frames exactly like the
# diagram itself nests them (see ``.tmp/netlist-spec.md`` Phase 2). Only the
# DEPTH differs between tiers:
#
#   * concise (default): the tree, changes only.
#   * --verbose: the SAME tree, PLUS a Signature section (the VI's own
#     connector-pane interface -- a distinct concern ``diff_uid`` doesn't
#     cover), a modified constant's old→new detail instead of just its name,
#     and a trailing unchanged-node tally.
#
# Geometry (bounds/path/chain_paths) is never touched by either tier --
# logical change only, exactly like a code diff.

Segment = tuple[str, str]  # one frame_path token: (struct_uid, value)

# The dedicated glyph for a CONSTANT change -- a small circle, giving a constant
# row the same leading kind marker as node (``◻``)/wire (``↔``)/scope (``⬚``)
# rows so the change column stays aligned. Node/structure changes render as
# netlist syntax and a wire change is plain connectivity text; only a constant
# keeps this glyph, via ``_constant_leaf_text``. Constants ARE in ``cmap`` now
# (as ``kind="constant"`` elements at any nesting depth -- ``_constant_changes``).
_CONST_GLYPH = "○"


def _segments(frame_path: str | None) -> tuple[Segment, ...]:
    """Parse a ``frame_path`` string into its ordered ``(struct_uid, value)``
    segments — the SAME containment chain ``_netlist_diff`` recurses by, for
    every change kind (node/wire/structure/frame/value) alike, and the SAME
    segments the viewer's ``data-path``/``frame_path`` tokens already use
    (see ``render/scene.py``'s ``encode_frame_path``). ``()`` for a
    top-level change (``frame_path`` is ``None``)."""
    if not frame_path:
        return ()
    segs = []
    for seg in frame_path.split(";"):
        key, _, value = seg.partition("=")
        segs.append((key, value))
    return tuple(segs)


@dataclass
class NetlistDiffRow:
    """One line of the netlist-form diff tree, STRUCTURED instead of a
    pre-formatted string -- the shared IR ``_netlist_diff`` now returns, so
    text (``_rows_to_text``) and non-text consumers (the HTML viewer's Tree
    view -- ``netlist_diff_rows``/``rows_to_json``) project the SAME rows
    instead of the text renderer and the viewer maintaining two parallel
    tree-builders (see ``.tmp/netlist-spec.md`` Phase 3)."""

    change: str | None  # "added" | "removed" | "modified" | None (context)
    depth: int          # nesting depth -- 2 spaces (text) / one indent (UI)
    text: str           # netlist-syntax content, NO gutter/indent baked in
    uid: str | None     # stable node/structure/wire uid, or None (context/const)
    kind: str           # "scope" | "frame" | "node" | "wire" | "constant"
                         # | "terminal" | "property" | "structure"


def _rows_to_text(rows: list[NetlistDiffRow]) -> list[str]:
    """Render structured rows back to the exact gutter+indent strings the
    text report has always emitted -- ``format_diff``'s output must stay
    byte-identical across this refactor (Phase 2's text is the contract)."""
    return [f"{_gutter(r.change)} {'  ' * r.depth}{r.text}" for r in rows]


def _constant_leaf_text(c: ElementChange, *, detailed: bool) -> str:
    """One constant change leaf's netlist text: the constant KIND glyph
    (``_CONST_GLYPH``) plus the type label. The glyph gives the row the same
    leading marker as node (``◻``)/wire (``↔``)/scope (``⬚``) rows so the change
    column stays aligned. Concise (viewer + default text) shows NO value — the
    row stays short and a string value can be arbitrarily long. ``detailed``
    (``--verbose``) appends the value (added/removed) or the ``old -> new``
    transition (modified) from ``c.detail``, mapped to ASCII via
    ``_ascii_arrows``. The +/-/~ change tag lives in ``NetlistDiffRow.change``."""
    if not detailed or not c.detail:
        return f"{_CONST_GLYPH} {c.label}"
    detail = _ascii_arrows(c.detail)
    sep = ":" if c.change == "modified" else " ="
    return f"{_CONST_GLYPH} {c.label}{sep} {detail}"


# The glyph for an FP control/indicator (terminal) change -- a small box, same
# leading-marker role as ``_CONST_GLYPH``/node/wire/scope so the change column
# stays aligned. ``c.element`` ("control"/"indicator") names the kind of thing.
_TERMINAL_GLYPH = "▭"


def _terminal_leaf_text(c: ElementChange, *, detailed: bool) -> str:
    """One terminal change leaf's netlist text: the terminal glyph + the
    control/indicator word + its name. ``--verbose`` (``detailed``) appends the
    ``old -> new`` retype/rename transition from ``c.detail`` (ASCII arrows).
    The +/-/~ change tag lives in ``NetlistDiffRow.change``."""
    head = f"{_TERMINAL_GLYPH} {c.element or 'terminal'} {c.label}"
    if not detailed or not c.detail:
        return head
    return f"{head}: {_ascii_arrows(c.detail)}"


# Per-kind netlist leaf-text renderer (glyph + text), the second half of the
# change-kind registry above — kept here because it references the helpers just
# defined. ``_netlist_diff``'s leaf switch dispatches through this, so a new
# glyph-leaf kind adds one entry instead of another ``elif`` branch. node/wire
# aren't here: they render as real netlist instance/connectivity lines, not a
# glyph+label (see ``node_content``/``wire_content``).
_LEAF_TEXT: dict[str, Callable[..., str]] = {
    "constant": _constant_leaf_text,
    "terminal": _terminal_leaf_text,
}


_TAG = {"added": "+", "removed": "-", "modified": "~"}


def _gutter(change: str | None) -> str:
    """One gutter char: the change tag, or a space for context (unchanged)."""
    return _TAG[change] if change is not None else " "


def _ascii_arrows(detail: str) -> str:
    """Map the unicode diff arrows a change ``detail`` carries to the netlist's
    locked ASCII syntax (``←`` -> ``=``, ``→`` -> ``->``). The netlist text
    output is ASCII-only, so every ``detail`` spliced into a row (wire changes,
    case/sequence frame value changes) must pass through here."""
    return detail.replace("←", "=").replace("→", "->")


def _walk_netlist_order(items: list[NetlistItem]) -> list[str]:
    """Pre-order uids of every instance/scope in ``items``, recursing into
    each scope's frame bodies -- i.e. the VI's own source/dataflow order
    (``_build_items`` walks operations in ``_node_order_key`` order, see
    the deterministic-node-order rule). Feeds ``_netlist_diff``'s
    ``source_order`` so siblings at a container render interleaved in this
    order instead of structures-first/leaves-second."""
    order: list[str] = []
    for item in items:
        order.append(item.uid)
        if isinstance(item, NetlistScope):
            for frame in item.frames:
                order.extend(_walk_netlist_order(frame.body))
    return order


def _netlist_diff(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    va: str, vb: str,
    cmap: ChangeMap, *, detailed: bool,
) -> list[NetlistDiffRow]:
    """The recursive containment tree, rendered in NETLIST form (see
    ``.tmp/netlist-spec.md`` Phase 2) as STRUCTURED rows (Phase 3) -- replaces
    the earlier unicode-glyph tree (``_composition_tree``, since deleted).
    Kept the SAME grouping skeleton (``by_path``/``child_uids``/
    ``values_of``, over ``_segments(frame_path)``) -- containment locality is
    proven and unchanged; only the emitted TYPE changed (a ``NetlistDiffRow``
    instead of a pre-formatted ``str``), plus Phase 2's own addition: every
    struct_uid at a path (whether it changed itself or merely contains
    changes) gets its own ``scope_header`` row, so nested changes always show
    their enclosing ``case (selector):``/``while (...):``/etc. context -- the
    old tree jumped straight to a bare frame sub-header with no case line
    above it.

    Both netlists are built fresh from the two graphs (the SAME projection
    ``describe --verbose`` uses) and indexed by uid so a changed node/
    structure's full instance/scope (its real inputs/outputs/selector) can be
    rendered as netlist syntax, not just the change-map's own label/detail.

    ``format_diff`` renders these rows to text via ``_rows_to_text`` (byte-
    identical to Phase 2); ``netlist_diff_rows`` exposes them unrendered for
    the HTML viewer's Tree view (Phase 3).

    Sibling order within a container is the VI's own SOURCE/dataflow order,
    not "structures first, then leaves": ``source_order`` is built from a
    pre-order walk of ``mod_b``'s body (head -- the version most changes
    render against), with ``mod_a``'s walk filling in a position for any
    uid ONLY present there (a removed instance/scope, appended after every
    surviving uid in its running-index order). ``render`` then sorts each
    container's structure uids AND leaf node/wire changes together by
    ``source_order.get(uid, <past every known uid>)``, tiebroken by
    ``_uid_sort`` for determinism when a uid has no netlist position at all
    (e.g. a wire change keyed by its own terminal uid, not a node/scope uid).
    """
    mod_b = build_netlist(graph_b, vb)
    mod_a = build_netlist(graph_a, va)
    inst_b, scope_b = index_module(mod_b)
    inst_a, scope_a = index_module(mod_a)
    amb_b = ambiguous_bares(mod_b)
    amb_a = ambiguous_bares(mod_a)

    source_order: dict[str, int] = {
        uid: i for i, uid in enumerate(_walk_netlist_order(mod_b.body))
    }
    for uid in _walk_netlist_order(mod_a.body):
        if uid not in source_order:
            source_order[uid] = len(source_order)
    _past_every_uid = len(source_order)

    def _sort_key(uid: str) -> tuple[int, int, object]:
        rank, tie = _uid_sort(uid)
        return (source_order.get(uid, _past_every_uid), rank, tie)

    elems = [
        c for c in cmap.changes
        if c.kind in _TREE_KINDS
    ]
    frame_elems = [c for c in cmap.changes if c.kind in _FRAME_KINDS]
    by_path: dict[tuple[Segment, ...], list[ElementChange]] = {}
    for c in elems:
        by_path.setdefault(_segments(c.frame_path), []).append(c)
    frame_change_at: dict[tuple[Segment, ...], ElementChange] = {
        _segments(c.frame_path): c for c in frame_elems
    }

    def node_content(c: ElementChange) -> str:
        """A node change leaf's content: the real netlist instance line for
        THIS change's own side (head for added/modified, base for removed),
        falling back to the change-map's own label when the uid isn't a
        netlist Operation at all (a modified CONSTANT -- constants aren't
        netlist instances; this also sidesteps the unicode ``→`` a
        constant's ``detail`` carries, since the fallback never uses it)."""
        if c.change == "removed":
            inst, amb = inst_a.get(c.uid), amb_a
        else:
            inst, amb = inst_b.get(c.uid), amb_b
        if inst is None:
            return c.label
        return instance_line(inst, amb)

    def struct_content(uid: str) -> str:
        """A structure's ``scope_header`` line content, from whichever side
        actually has it (head first, then base -- an unchanged structure
        exists on both; an added one only on head, removed only on base).
        Falls back to the change-map's own label (never crashes) on the
        (should-never-happen) case neither side's netlist has this uid."""
        scope = scope_b.get(uid)
        if scope is not None:
            return scope_header(scope, amb_b)
        scope = scope_a.get(uid)
        if scope is not None:
            return scope_header(scope, amb_a)
        struct_c = next((c for c in elems if c.uid == uid), None)
        return struct_c.label if struct_c is not None else uid

    def frame_label(struct_uid: str, value: str) -> str:
        """The faithful DISPLAY text for one case/disable frame, keyed by its
        RAW selector value (``value`` -- the same token ``_extend_frame_path``
        bakes into ``frame_path``/the SVG ``data-path``, so identity stays
        stable regardless of display label). Looked up from whichever side's
        netlist has this structure -- both sides carry the SAME label for an
        unchanged frame; an added/removed frame only exists on one side.
        Falls back to the raw value (never crashes) if neither side's
        ``NetlistScope`` has it, e.g. a stale/mismatched uid."""
        for scopes in (scope_b, scope_a):
            scope = scopes.get(struct_uid)
            if scope is None:
                continue
            for frame in scope.frames:
                if frame.value == value:
                    return frame.label
        return value

    def wire_content(c: ElementChange, siblings: list[ElementChange]) -> str | None:
        """ASCII connectivity line for a standalone wire change, or ``None``
        to suppress it. Suppressed when a NODE change with the SAME change
        value is ALSO rendered at this exact containment path -- an ADDED
        wire already shown inline by an added node's own ``instance_line``
        (its inputs/outputs ARE the wires), so a separate wire leaf would
        just repeat it. A wire whose change value DIFFERS from the sibling
        node's (e.g. a REMOVED wire sharing a path with an ADDED node) is
        NOT redundant with that node's instance line -- it must still show;
        a deletion must always show. Otherwise: the change-map's own
        label/detail verbatim (never invent a name -- the #13 gap: a
        degenerate ``"x"`` label is rendered as-is), with any unicode arrow
        mapped to the locked ASCII syntax (``<-``/``->`` become ``=``/``->``)."""
        if any(
            s.kind == "node" and s.change == c.change
            for s in siblings if s is not c
        ):
            return None
        detail = _ascii_arrows(c.detail or "")
        return f"{c.label} {detail}" if detail else c.label

    def child_uids(path: tuple[Segment, ...]) -> list[str]:
        """Distinct struct uids appearing at depth ``len(path)`` among every
        change chain that starts with ``path``, ordered by the VI's own
        source/dataflow order (``_sort_key`` / ``source_order``), NOT by
        first-appearance in ``cmap.changes``. ``_reorder_by_tree`` mutates that
        list (it re-sorts by the very rows this builds), so keying ordering off
        it would make the tree -- and the badge numbers derived from it --
        non-idempotent across re-runs."""
        depth = len(path)
        uids: set[str] = set()
        for c in cmap.changes:
            segs = _segments(c.frame_path)
            if len(segs) > depth and segs[:depth] == path:
                uids.add(segs[depth][0])
        return sorted(uids, key=_sort_key)

    def _frame_order(struct_uid: str) -> dict[str, int]:
        """VALUE -> position map for a structure's frames, in the VI's own
        frame order (from whichever side's ``NetlistScope`` has it). Independent
        of which frames happen to carry changes, so ordering ``values_of`` by it
        stays stable under ``_reorder_by_tree`` re-runs."""
        for scopes in (scope_b, scope_a):
            scope = scopes.get(struct_uid)
            if scope is not None:
                return {f.value: i for i, f in enumerate(scope.frames)}
        return {}

    def values_of(path: tuple[Segment, ...], struct_uid: str) -> list[str]:
        """Distinct frame VALUES of ``struct_uid`` carrying changes at ``path``,
        in the VI's own frame order (``_frame_order``) -- again NOT
        first-appearance over the mutated ``cmap.changes`` (see ``child_uids``),
        so nested-frame row order and badge numbers are idempotent."""
        depth = len(path)
        order = _frame_order(struct_uid)
        values: set[str] = set()
        for c in cmap.changes:
            segs = _segments(c.frame_path)
            if (
                len(segs) > depth and segs[:depth] == path
                and segs[depth][0] == struct_uid
            ):
                values.add(segs[depth][1])
        return sorted(values, key=lambda v: (order.get(v, len(order)), v))

    def render_struct_children(
        struct_uid: str, path: tuple[Segment, ...], depth: int,
    ) -> list[NetlistDiffRow]:
        rows: list[NetlistDiffRow] = []
        for value in values_of(path, struct_uid):
            child_path = path + ((struct_uid, value),)
            fc = frame_change_at.get(child_path)
            body = render(child_path, depth + 1)
            if fc is not None or body:
                detail = (
                    f" {_ascii_arrows(fc.detail)}"
                    if (fc is not None and fc.detail) else ""
                )
                rows.append(NetlistDiffRow(
                    change=fc.change if fc is not None else None,
                    depth=depth,
                    text=f'"{frame_label(struct_uid, value)}":{detail}',
                    uid=fc.uid if fc is not None else None,
                    kind="frame",
                ))
                rows.extend(body)
        return rows

    def render(path: tuple[Segment, ...], depth: int) -> list[NetlistDiffRow]:
        rows: list[NetlistDiffRow] = []
        here = by_path.get(path, [])
        struct_by_uid = {
            c.uid: c for c in here
            if c.kind == "structure" and c.change in ("added", "removed")
        }
        # A structure's OWN header must render even with NO descendant at
        # all -- an empty/childless added case, or (always) a Loop/flat
        # Sequence: neither is ``_is_interactive_struct``, so its children
        # never gain its uid as a frame_path segment (they pass its own
        # locality straight through -- see ``_collect_elements``), meaning
        # ``child_uids`` alone would never surface it. So the struct uid SET
        # is the union: every structure genuinely AT this path, plus any
        # further uid only discoverable via a descendant's chain (an
        # UNCHANGED container with changed contents). Dedup order here
        # doesn't matter -- ``_sort_key`` below re-orders everything.
        struct_uids: list[str] = []
        seen: set[str] = set()
        for uid in [*struct_by_uid, *child_uids(path)]:
            if uid not in seen:
                seen.add(uid)
                struct_uids.append(uid)
        # Interleave structures and leaf node/wire changes AT THIS CONTAINER
        # in the VI's own source/dataflow order (``_sort_key`` /
        # ``source_order``) -- a code diff reads top-to-bottom, not
        # structures-first-then-statements.
        siblings: list[tuple[tuple[int, int, object], str, object]] = []
        siblings += ((_sort_key(uid), "struct", uid) for uid in struct_uids)
        siblings += (
            (_sort_key(c.uid), "leaf", c)
            for c in here
            if c.kind in _LEAF_KINDS
        )
        siblings.sort(key=lambda s: s[0])
        for _, tag, payload in siblings:
            if tag == "struct":
                uid = payload
                assert isinstance(uid, str)
                struct_c = struct_by_uid.get(uid)
                rows.append(NetlistDiffRow(
                    change=struct_c.change if struct_c is not None else None,
                    depth=depth,
                    text=struct_content(uid),
                    uid=uid,
                    kind="scope",
                ))
                rows.extend(render_struct_children(uid, path, depth + 1))
                continue
            c = payload
            assert isinstance(c, ElementChange)
            if c.kind == "node":
                rows.append(NetlistDiffRow(
                    change=c.change, depth=depth, text=node_content(c),
                    uid=c.uid, kind="node",
                ))
            elif c.kind == "wire":
                content = wire_content(c, here)
                if content is not None:
                    rows.append(NetlistDiffRow(
                        change=c.change, depth=depth, text=content,
                        uid=c.uid, kind="wire",
                    ))
            elif c.kind in _LEAF_TEXT:
                # constant / terminal (and any future glyph-leaf kind): one
                # ``glyph + text`` renderer per kind, registered in ``_LEAF_TEXT``.
                rows.append(NetlistDiffRow(
                    change=c.change, depth=depth,
                    text=_LEAF_TEXT[c.kind](c, detailed=detailed),
                    uid=c.uid, kind=c.kind,
                ))
        return rows

    return render((), 0)


# Every MetadataChange is a value transition -- the field itself can never be
# added/removed (fixed schema), so it always renders on the "modified" gutter
# (``~``, via ``_TAG``/``_gutter`` below), never "added"/"removed".
_METADATA_CHANGE_TAG = "modified"


def _metadata_change_text(c: MetadataChange) -> str:
    """Netlist-syntax content for one Properties/Structure change leaf --
    always ``name: old -> new`` (a boolean flip renders as
    ``reentrant: false -> true``, same shape as an enum transition like
    ``lock: unlocked -> password_protected``). NO gutter/indent -- the
    caller supplies both (``_format_metadata_section`` for text,
    ``NetlistDiffRow`` for the HTML viewer's Tree)."""
    return f"{c.name}: {c.old} -> {c.new}"


def _format_metadata_section(header: str, changes: list[MetadataChange]) -> str:
    """Render a list of ``MetadataChange`` as a ``<header>:`` section, one
    ``  ~`` line per change -- the same shape ``_format_signature_section``
    renders a ``Signature:`` section in. Every line is ``~`` (see
    ``MetadataChange``'s docstring)."""
    lines = [f"{header}:"]
    for c in changes:
        lines.append(f"  {_TAG[_METADATA_CHANGE_TAG]} {_metadata_change_text(c)}")
    return "\n".join(lines)


def _metadata_rows(changes: list[MetadataChange], kind: str) -> list[NetlistDiffRow]:
    """Project ``MetadataChange``s (Properties/Structure) as top-level
    (``depth=0``) ``NetlistDiffRow``s for the HTML viewer's Tree, so it shows
    the SAME property/structure changes ``format_diff`` prints as text. No
    stable node uid exists for a VI-level setting, so ``uid`` is None -- like
    a module-level change, there is nothing in the diagram to highlight.
    ``change`` is always "modified" (see ``MetadataChange``'s docstring)."""
    return [
        NetlistDiffRow(
            change=_METADATA_CHANGE_TAG, depth=0,
            text=_metadata_change_text(c), uid=None, kind=kind,
        )
        for c in changes
    ]


def _format_signature_section(changes: list[SignatureChange]) -> str:
    lines = ["Signature:"]
    for sig in changes:
        if sig.category == "added":
            lines.append(f"  + {sig.direction}: {sig.name} ({sig.new_type})")
        elif sig.category == "removed":
            lines.append(f"  - {sig.direction}: {sig.name} ({sig.old_type})")
        elif sig.category == "type_changed":
            lines.append(
                f"  ~ {sig.direction}: {sig.name}:"
                f" {sig.old_type} -> {sig.new_type}"
            )
    return "\n".join(lines)


def format_diff(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    vi_name_a: str, vi_name_b: str,
    *, verbose: bool = False,
) -> str:
    """The ``lvkit diff`` TEXT report: ONE recursive composition tree (see
    the module section header above), both tiers over the same ``diff_uid``
    ChangeMap. Empty string means no changes -- the caller (``cmd_diff``)
    prints "No changes detected."
    """
    va = graph_a.resolve_vi_name(vi_name_a)
    vb = graph_b.resolve_vi_name(vi_name_b)
    cmap = diff_uid(graph_a, graph_b, va, vb)

    sections: list[str] = []

    if verbose:
        signature = _diff_signature(graph_a, graph_b, va, vb)
        if signature:
            sections.append(_format_signature_section(signature))

    # Properties/Structure changes surface in BOTH tiers (unlike Signature,
    # verbose-only): a VI going protected or broken is high-signal enough to
    # always show, mirroring the netlist header's own "always summarized"
    # positioning (docs/reference/netlist.md).
    ctx_a, ctx_b = graph_a.get_vi_context(va), graph_b.get_vi_context(vb)
    prop_changes = _diff_vi_properties(ctx_a.properties, ctx_b.properties)
    if prop_changes:
        sections.append(_format_metadata_section("Properties", prop_changes))
    struct_changes = _diff_vi_structure(ctx_a.structure, ctx_b.structure)
    if struct_changes:
        sections.append(_format_metadata_section("Structure", struct_changes))

    rows = _netlist_diff(graph_a, graph_b, va, vb, cmap, detailed=verbose)
    if rows:
        sections.append("\n".join(_rows_to_text(rows)))

    if verbose and sections and cmap.common_node_uids:
        # #26: a pure reposition (same/re-keyed wiring) is already collapsed
        # to unchanged by diff_uid's own matching -- there is no separate
        # "moved" signal to count, so this is an unchanged-node TALLY, never
        # a reposition count. Only shown alongside a REAL change (an
        # untouched VI is "No changes detected.", not a tally of nothing).
        sections.append(f"({len(cmap.common_node_uids)} unchanged nodes)")

    return "\n\n".join(sections)


def diff_to_dict(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    vi_name_a: str, vi_name_b: str,
) -> dict[str, Any]:
    """The full ``lvkit diff`` as a JSON-ready dict: the uid-keyed element
    ``ChangeMap`` PLUS the module-level Signature / Properties / Structure
    sections that live OUTSIDE it (exactly as ``format_diff``'s text output
    carries them). ``--format json`` historically emitted only
    ``ChangeMap.to_dict()``, so those sections were text/viewer-Tree only; this
    makes JSON parallel to the text report. Properties/Structure entries are
    always a ``modified`` old->new pair (a fixed, always-present schema -- a VI
    property can never be added or removed, only changed).
    """
    va = graph_a.resolve_vi_name(vi_name_a)
    vb = graph_b.resolve_vi_name(vi_name_b)
    cmap = diff_uid(graph_a, graph_b, va, vb)
    ctx_a, ctx_b = graph_a.get_vi_context(va), graph_b.get_vi_context(vb)
    return {
        **cmap.to_dict(),
        "signature": [
            {"category": s.category, "direction": s.direction, "name": s.name,
             "old_type": s.old_type, "new_type": s.new_type}
            for s in _diff_signature(graph_a, graph_b, va, vb)
        ],
        "properties": [
            {"field": m.name, "old": m.old, "new": m.new}
            for m in _diff_vi_properties(ctx_a.properties, ctx_b.properties)
        ],
        "structure": [
            {"field": m.name, "old": m.old, "new": m.new}
            for m in _diff_vi_structure(ctx_a.structure, ctx_b.structure)
        ],
    }


def netlist_diff_rows(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    vi_name_a: str, vi_name_b: str, *, detailed: bool = False,
) -> list[NetlistDiffRow]:
    """The SAME structured rows ``format_diff`` renders to text, exposed for
    NON-text consumers -- today the HTML viewer's Tree view (see
    ``.tmp/netlist-spec.md`` Phase 3), so the viewer renders the identical
    netlist-diff tree as ``lvkit diff`` prints, not a client-side
    reconstruction of it. Resolves VI names, builds the ``diff_uid`` change
    map exactly like ``format_diff`` does, then returns ``_netlist_diff``'s
    rows unrendered."""
    va = graph_a.resolve_vi_name(vi_name_a)
    vb = graph_b.resolve_vi_name(vi_name_b)
    cmap = diff_uid(graph_a, graph_b, va, vb)

    ctx_a, ctx_b = graph_a.get_vi_context(va), graph_b.get_vi_context(vb)
    rows = _metadata_rows(
        _diff_vi_properties(ctx_a.properties, ctx_b.properties), "property",
    )
    rows += _metadata_rows(
        _diff_vi_structure(ctx_a.structure, ctx_b.structure), "structure",
    )
    rows += _netlist_diff(graph_a, graph_b, va, vb, cmap, detailed=detailed)
    return rows


def rows_to_json(rows: list[NetlistDiffRow]) -> list[dict]:
    """JSON-ready dicts for the HTML viewer's ``__NETLIST_TREE__`` payload
    (one dict per ``NetlistDiffRow`` field, verbatim)."""
    return [
        {
            "change": r.change, "depth": r.depth, "text": r.text,
            "uid": r.uid, "kind": r.kind,
        }
        for r in rows
    ]


# ── Comparison helpers ────────────────────────────────────────────────


def _diff_signature(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[SignatureChange]:
    changes: list[SignatureChange] = []
    for direction in ("input", "output"):
        if direction == "input":
            terms_a = ga.get_inputs(va)
            terms_b = gb.get_inputs(vb)
        else:
            terms_a = ga.get_outputs(va)
            terms_b = gb.get_outputs(vb)

        map_a = _terminal_map(terms_a)
        map_b = _terminal_map(terms_b)

        for name in sorted(set(map_a) | set(map_b)):
            if name not in map_a:
                tb = map_b[name].lv_type
                changes.append(SignatureChange(
                    "added", direction, name,
                    new_type=tb.lv_label() if tb else "Any",
                ))
            elif name not in map_b:
                ta = map_a[name].lv_type
                changes.append(SignatureChange(
                    "removed", direction, name,
                    old_type=ta.lv_label() if ta else "Any",
                ))
            else:
                ta, tb = map_a[name].lv_type, map_b[name].lv_type
                type_a = ta.lv_label() if ta else "Any"
                type_b = tb.lv_label() if tb else "Any"
                if type_a != type_b:
                    changes.append(SignatureChange(
                        "type_changed", direction, name,
                        old_type=type_a, new_type=type_b,
                    ))
    return changes


# Curated boolean VI Properties/VIStructure flags -> display name, sourced
# from the ONE canonical maps in ``graph.models`` (``CURATED_PROPERTY_FLAGS``/
# ``CURATED_STRUCTURE_FLAGS``) so the diff, the netlist header spec, and the
# render-viewer's status chips can never drift into different flag sets --
# see those maps' docstrings for what's included/excluded and why.
_PROPERTY_BOOL_FIELDS: tuple[tuple[str, str], ...] = tuple(
    CURATED_PROPERTY_FLAGS.items()
)
_STRUCTURE_BOOL_FIELDS: tuple[tuple[str, str], ...] = tuple(
    CURATED_STRUCTURE_FLAGS.items()
)


def _diff_vi_properties(pa: VIProperties, pb: VIProperties) -> list[MetadataChange]:
    """Curated VI Properties changes: ``lock_state`` (enum transition) plus
    the high-signal ``ExecutionProps`` flags in ``_PROPERTY_BOOL_FIELDS``.
    Every field is a fixed part of the VI Properties schema -- a changed
    field is always an old -> new VALUE transition, never an add/remove (see
    ``MetadataChange``). Everything else on ``VIProperties`` (``lv_version``,
    ``vi_type``, window/toolbar/instance settings, numeric priority) is
    deliberately never compared -- see the diff philosophy note on
    ``_PROPERTY_BOOL_FIELDS``."""
    changes: list[MetadataChange] = []
    if pa.lock_state != pb.lock_state:
        changes.append(MetadataChange(
            "lock", pa.lock_state.value, pb.lock_state.value,
        ))
    for field_name, label in _PROPERTY_BOOL_FIELDS:
        old_val = getattr(pa.execution, field_name)
        new_val = getattr(pb.execution, field_name)
        if old_val != new_val:
            changes.append(MetadataChange(label, bool_str(old_val), bool_str(new_val)))
    return changes


def _diff_vi_structure(sa: VIStructure, sb: VIStructure) -> list[MetadataChange]:
    """Curated VIStructure changes: the flags in ``_STRUCTURE_BOOL_FIELDS``
    (``is_broken`` is the derived property -- any ``bad_*`` flag flipping
    shows as one ``broken: false -> true`` change rather than five separate
    ones). Always an old -> new value transition, never an add/remove (every
    VI has every one of these fields -- see ``MetadataChange``)."""
    changes: list[MetadataChange] = []
    for field_name, label in _STRUCTURE_BOOL_FIELDS:
        old_val, new_val = getattr(sa, field_name), getattr(sb, field_name)
        if old_val != new_val:
            changes.append(MetadataChange(label, bool_str(old_val), bool_str(new_val)))
    return changes


# ── Utility functions ─────────────────────────────────────────────────


def _terminal_map(terminals: list[Terminal]) -> dict[str, Terminal]:
    """Map terminals by name."""
    return {t.name: t for t in terminals if t.name}


def _value_disp(value: object) -> str:
    """Readable one-line display of a constant's value for a change detail.
    A plain string shows as-is (no repr quote noise); anything else falls back to
    repr. Newlines/tabs are flattened so the detail stays one line. Carried in
    ``ElementChange.detail`` (JSON + ``--verbose`` text); the viewer LIST omits
    it (a string value can be arbitrarily long)."""
    s = value if isinstance(value, str) else repr(value)
    return " ".join(s.split())


def _const_label(c: Constant) -> str:
    """Short FAITHFUL label for a constant in a frame diff (``lv_label()``
    already handles the error-cluster case internally)."""
    type_str = c.lv_type.lv_label() if c.lv_type else "unknown"
    return f"{type_str} constant"
