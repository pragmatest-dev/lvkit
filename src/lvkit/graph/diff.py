"""Diff two versions of a LabVIEW VI by comparing their graph representations."""

from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import (
    CaseFrame,
    CaseOperation,
    Frame,
    LoopOperation,
    Operation,
    SelectorRange,
    SequenceFrame,
    SequenceOperation,
    Terminal,
    _is_error_cluster,
)
from .describe import describe_vi
from .models import Constant, Wire, WireEnd

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
class OperationChange:
    category: str  # "added", "removed"
    name: str
    node_type: str | None = None


@dataclass
class ConstantChange:
    category: str  # "added", "removed", "value_changed"
    name: str
    old_value: str | None = None
    new_value: str | None = None


@dataclass
class WiringChange:
    category: str  # "added", "removed"
    description: str  # "NodeA -> NodeB"


@dataclass
class StructureChange:
    category: str  # "added", "removed", "changed"
    name: str
    details: str | None = None


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
    kind: str       # "node" | "structure" | "wire"
    change: str     # "added" | "removed" | "modified"
    label: str      # display name
    # Absolute-pixel bounds (x1, y1, x2, y2) from the owning version's Layout —
    # the SAME coordinate space as the rendered SVG viewBox, so the viewer draws
    # a highlight straight from these with no getBBox scrape. None when the graph
    # was loaded without ``layout=True``. Added → head's layout; removed → base's;
    # modified → head's (the node persists; we point at its current position).
    bounds: Rect | None = None
    # For "modified" only: the SAME node's bounds in the BASE version, so the
    # viewer can highlight it in both panes (old → new before/after). None for
    # added (base has no such node) and removed (base bounds already in `bounds`).
    bounds_base: Rect | None = None
    # For "modified": a short human-readable "old → new" of what changed (e.g. a
    # constant's value transition). None for added/removed (the label says it all).
    detail: str | None = None
    # ── Faithful wire geometry (increment 2a) ────────────────────────────
    # The rendered wire's polyline — the SAME points render/scene.py draws:
    # [source-terminal center, *Layout.wire_by_uid[sink], sink-terminal center],
    # in absolute SVG-viewBox pixels. Set on WIRE changes so the viewer overlays
    # the real colored wire (not a pin). None when layout is absent. For an
    # added/modified wire it's the HEAD routing; for a removed wire the BASE.
    path: list[Point] | None = None
    # For a "modified" WIRE only: the OLD (base-side) routing polyline, so the
    # viewer can draw the dashed old wire beside the new one. None otherwise.
    path_base: list[Point] | None = None
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
                 "bounds_base": list(c.bounds_base)
                 if c.bounds_base is not None else None,
                 "path": _poly(c.path),
                 "path_base": _poly(c.path_base),
                 "chain_paths": [_poly(p) for p in c.chain_paths]
                 if c.chain_paths is not None else None,
                 "container_uid": c.container_uid,
                 "frame_path": c.frame_path}
                for c in self.changes
            ],
            "common_nodes": len(self.common_node_uids),
        }


@dataclass
class DiffReport:
    signature: list[SignatureChange] = field(default_factory=list)
    operations: list[OperationChange] = field(default_factory=list)
    constants: list[ConstantChange] = field(default_factory=list)
    wiring: list[WiringChange] = field(default_factory=list)
    structures: list[StructureChange] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([
            self.signature, self.operations, self.constants,
            self.wiring, self.structures,
        ])

    def format(self) -> str:
        sections: list[str] = []

        if self.signature:
            lines = ["Signature:"]
            for sig in self.signature:
                if sig.category == "added":
                    lines.append(f"  + {sig.direction}: {sig.name} ({sig.new_type})")
                elif sig.category == "removed":
                    lines.append(f"  - {sig.direction}: {sig.name} ({sig.old_type})")
                elif sig.category == "type_changed":
                    lines.append(
                        f"  ~ {sig.direction}: {sig.name}:"
                        f" {sig.old_type} -> {sig.new_type}"
                    )
            sections.append("\n".join(lines))

        if self.operations:
            lines = ["Operations:"]
            for op in self.operations:
                tag = "+" if op.category == "added" else "-"
                node_label = f" [{op.node_type}]" if op.node_type else ""
                lines.append(f"  {tag} {op.name}{node_label}")
            sections.append("\n".join(lines))

        if self.constants:
            lines = ["Constants:"]
            for con in self.constants:
                if con.category == "added":
                    lines.append(f"  + {con.name} = {con.new_value}")
                elif con.category == "removed":
                    lines.append(f"  - {con.name} = {con.old_value}")
                elif con.category == "value_changed":
                    lines.append(f"  ~ {con.name}: {con.old_value} -> {con.new_value}")
            sections.append("\n".join(lines))

        if self.wiring:
            lines = ["Wiring:"]
            for w in self.wiring:
                tag = "+" if w.category == "added" else "-"
                lines.append(f"  {tag} {w.description}")
            sections.append("\n".join(lines))

        if self.structures:
            lines = ["Structures:"]
            for s in self.structures:
                if s.category in ("added", "removed"):
                    tag = "+" if s.category == "added" else "-"
                    line = f"  {tag} {s.name}"
                    if s.details:
                        line += f" ({s.details})"
                    lines.append(line)
                else:
                    lines.append(f"  ~ {s.name}: {s.details}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)


# ── Short form: text diff ─────────────────────────────────────────────


def diff_text(
    graph_a: InMemoryVIGraph,
    graph_b: InMemoryVIGraph,
    vi_name_a: str,
    vi_name_b: str,
    *,
    label_a: str = "a",
    label_b: str = "b",
) -> str:
    """Unified text diff of two VI descriptions."""
    text_a = describe_vi(graph_a, vi_name_a)
    text_b = describe_vi(graph_b, vi_name_b)

    diff_lines = list(difflib.unified_diff(
        text_a.splitlines(keepends=True),
        text_b.splitlines(keepends=True),
        fromfile=label_a,
        tofile=label_b,
    ))
    return "".join(diff_lines)


# ── Long form: structured diff ────────────────────────────────────────


def diff_structured(
    graph_a: InMemoryVIGraph,
    graph_b: InMemoryVIGraph,
    vi_name_a: str,
    vi_name_b: str,
) -> DiffReport:
    """Compare two VIs and return a categorized change report."""
    vi_name_a = graph_a.resolve_vi_name(vi_name_a)
    vi_name_b = graph_b.resolve_vi_name(vi_name_b)

    report = DiffReport()
    report.signature = _diff_signature(graph_a, graph_b, vi_name_a, vi_name_b)
    report.operations = _diff_operations(graph_a, graph_b, vi_name_a, vi_name_b)
    report.constants = _diff_constants(graph_a, graph_b, vi_name_a, vi_name_b)
    report.wiring = _diff_wiring(graph_a, graph_b, vi_name_a, vi_name_b)
    report.structures = _diff_structures(graph_a, graph_b, vi_name_a, vi_name_b)
    return report


# ── UID-keyed change-map (matches by stable node UID, not name) ────────

_STRUCT_OPS = (CaseOperation, LoopOperation, SequenceOperation)


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
    return op.node_type or "structure"


def _elem_label(op: Operation, kind: str) -> str:
    """Display label — structures name their kind, nodes use their own name."""
    if kind == "structure":
        return _struct_label(op)
    return op.name or op.node_type or "node"


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
    """Whether ``op`` is a CASE structure or a STACKED sequence — the only
    structure kinds render/scene.py wraps in a togglable ``<g class="lv-frame"
    data-path=...>`` group (see ``_frame_path`` there). A While/For loop shows
    its body unconditionally, and a FLAT sequence shows every frame at once in
    a film-strip — neither is ever hidden, so their children keep the
    enclosing context's locality unchanged rather than gaining a new frame
    segment nothing in the SVG would ever match."""
    if isinstance(op, CaseOperation):
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
    sequence (flat or stacked). Matches exactly what
    render/scene.py::_frame_path stores as ``cur.frame`` for the same node,
    so ``str()``-ing it here reproduces the identical token."""
    if isinstance(frame, CaseFrame):
        return frame.selector_value
    if isinstance(frame, SequenceFrame):
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
        if isinstance(op, (CaseOperation, SequenceOperation)):
            struct_uid = _uid_of(op.id)
            interactive = _is_interactive_struct(op)
            for frame in op.frames:
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
        return (op.name or "", op.node_type or "") if op is not None else ("?", "?")

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
    -- ``ElementChange.bounds``/``bounds_base`` are ``Rect`` (node bounding
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


def _incident_chain_paths(
    layout: Layout | None, wires: list[Wire], node_uid: str,
) -> list[list[Point]] | None:
    """Polylines of every non-internal wire incident to ``node_uid`` (as source
    OR sink) — the node's wire "chain". For an added/removed node, every
    incident wire is itself new/gone, so the whole chain belongs to that node's
    add/remove. Keyed per wire by its sink terminal (an input takes one wire, so
    sinks are unique across the incident set). None when layout is absent or no
    incident wire is drawable."""
    if layout is None:
        return None
    paths: list[list[Point]] = []
    for w in wires:
        if node_uid not in (_uid_of(w.source.node_id), _uid_of(w.dest.node_id)):
            continue
        path = _wire_path(layout, wires, _uid_of(w.dest.terminal_id))
        if path is not None:
            paths.append(path)
    return paths or None


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
    # Every node id that is the SAME logical node on both sides, in
    # base-space canonical form: common (identical uid) or matched
    # (exact/fuzzy dataflow match). The VI's own connector-pane/self node is
    # always present on both sides by construction.
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

    consts_a = {c.id: (c.name or _const_label(c)) for c in graph_a.get_constants(va)}
    consts_b = {c.id: (c.name or _const_label(c)) for c in graph_b.get_constants(vb)}
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
                owner_label = entry.op.name or entry.op.node_type
        for t in terminals:
            match = (
                t.index == term_key if isinstance(term_key, int)
                else t.name == term_key
            )
            if match and (t.display_name or t.name):
                return t.display_name or t.name  # type: ignore[return-value]
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

    changes: list[ElementChange] = []
    for key in sorted(set(sinks_a) | set(sinks_b), key=_sink_sort_key):
        node_key, _term_key = key
        if node_key not in unchanged:
            continue  # sink itself belongs to an added/removed node

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

        # Reserve "modified" for rewires between two UNCHANGED nodes. If a
        # source's node is itself added/removed, downgrade that endpoint to
        # "no source" -- that node's own add/remove entry already tells its
        # story (don't-describe-twice), and what's LEFT is the genuine
        # add/remove of the OTHER endpoint (e.g. an unchanged sink losing its
        # only real producer when something new displaces it).
        if (
            entry_a is not None and src_id_a is not None
            and src_id_a[0] not in unchanged
        ):
            entry_a = None
        if (
            entry_b is not None and src_id_b is not None
            and src_id_b[0] not in unchanged
        ):
            entry_b = None
        if entry_a is None and entry_b is None:
            continue  # both endpoints are already their own added/removed story

        if entry_a is None:
            change = "added"
        elif entry_b is None:
            change = "removed"
        else:
            change = "modified"

        # Faithful wire polyline: the wire lives in the version matching its
        # change — removed → base, added & modified → head. A "modified" wire
        # ALSO carries the OLD (base) routing so the viewer can dash it.
        path_base: list[Point] | None = None
        if change == "removed":
            assert entry_a is not None
            dest_end = entry_a[3]
            sink_label = label_of(dest_end, va, a, self_terms_a, consts_a)
            old_label = label_of(entry_a[2], va, a, self_terms_a, consts_a)
            bounds = _point_rect(layout_a, _uid_of(dest_end.terminal_id))
            bounds_base = _point_rect(layout_a, _uid_of(entry_a[2].terminal_id))
            detail = f"(was ← {old_label})"
            path = _wire_path(layout_a, wires_a, _uid_of(dest_end.terminal_id))
        else:
            assert entry_b is not None
            dest_end = entry_b[3]
            sink_label = label_of(dest_end, vb, b, self_terms_b, consts_b)
            new_label = label_of(entry_b[2], vb, b, self_terms_b, consts_b)
            bounds = _point_rect(layout_b, _uid_of(dest_end.terminal_id))
            path = _wire_path(layout_b, wires_b, _uid_of(dest_end.terminal_id))
            if change == "added":
                bounds_base = None
                detail = f"← {new_label}"
            else:
                assert entry_a is not None
                old_label = label_of(entry_a[2], va, a, self_terms_a, consts_a)
                bounds_base = _point_rect(
                    layout_a, _uid_of(entry_a[2].terminal_id),
                )
                detail = f"← {new_label} (was {old_label})"
                path_base = _wire_path(
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
            sink_label, bounds, bounds_base=bounds_base, detail=detail,
            path=path, path_base=path_base,
            container_uid=container_uid, frame_path=frame_path,
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


def _format_selector_ranges(ranges: list[SelectorRange]) -> str:
    """Render numeric selector ranges the way LabVIEW builds the label:
    singles as the bare value, closed ranges as ``a..b``, open ranges as
    ``a..``/``..b``, joined with ``, `` — a TYPE-UNAWARE subset of
    render/scene.py's ``_format_ranges`` (enum-name resolution needs a
    resolved ``LVType`` this layer doesn't have, and diff.py must not import
    render — see the module's layering). Good enough for a diff DETAIL
    string; the faithful enum-aware label is the renderer's job."""
    parts: list[str] = []
    for r in ranges:
        if r.open_start:
            parts.append(f"..{r.end}")
        elif r.open_end:
            parts.append(f"{r.start}..")
        elif r.is_single:
            parts.append(str(r.start))
        else:
            parts.append(f"{r.start}..{r.end}")
    return ", ".join(parts)


def _frame_display(frame: Frame) -> str:
    """Human label for one frame — its selector token (case) or index
    (sequence). Mirrors the simple ``str(selector_value)``/``str(index)``
    convention ``_compare_frames``/``_frame_content_delta`` already use to
    key/label frames elsewhere in this module, plus ``Default``/ranges/
    strings formatting for a case frame's richer selector shapes."""
    if isinstance(frame, CaseFrame):
        if frame.is_default:
            return "Default"
        if frame.selector_ranges:
            return _format_selector_ranges(frame.selector_ranges)
        if frame.selector_strings:
            return ", ".join(f'"{s}"' for s in frame.selector_strings)
        return str(frame.selector_value)
    if isinstance(frame, SequenceFrame):
        return str(frame.index)
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
    present."""
    if frame.uid is not None:
        return frame.uid
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


def _struct_frame_changes(
    entry_a: _ElemInfo, entry_b: _ElemInfo,
) -> list[ElementChange]:
    """Diff one matched Case/Sequence structure's FRAME SET across versions:
    a whole frame added/removed, or the same frame's selector/index value
    changed. Does NOT touch the frame's contents (nodes/wires) — those are
    already reported by the node/wire passes; collapsing them here would be
    exactly the double-report ``diff_uid`` elsewhere goes out of its way to
    avoid (don't-describe-twice)."""
    op_a, op_b = entry_a.op, entry_b.op
    if type(op_a) is not type(op_b):
        return []  # UID recycle across kinds — not a frame-set change
    if not isinstance(op_a, (CaseOperation, SequenceOperation)):
        return []
    if not isinstance(op_b, (CaseOperation, SequenceOperation)):
        return []

    base_struct_uid = _uid_of(op_a.id)
    head_struct_uid = _uid_of(op_b.id)
    frames_a: list[Frame] = list(op_a.frames)
    frames_b: list[Frame] = list(op_b.frames)
    map_a = {_frame_key(f): f for f in frames_a}
    map_b = {_frame_key(f): f for f in frames_b}

    changes: list[ElementChange] = []
    for key in sorted(set(map_a) | set(map_b), key=_uid_sort):
        fa, fb = map_a.get(key), map_b.get(key)
        if fa is None:
            assert fb is not None
            container_uid, frame_path = _frame_locality(
                head_struct_uid, op_b, entry_b.frame_path, _frame_value(fb),
            )
            changes.append(ElementChange(
                key, f"{op_b.id}::frame::{key}", "frame", "added",
                _frame_display(fb),
                container_uid=container_uid, frame_path=frame_path,
            ))
        elif fb is None:
            container_uid, frame_path = _frame_locality(
                base_struct_uid, op_a, entry_a.frame_path, _frame_value(fa),
            )
            changes.append(ElementChange(
                key, f"{op_a.id}::frame::{key}", "frame", "removed",
                _frame_display(fa),
                container_uid=container_uid, frame_path=frame_path,
            ))
        elif _frame_value_changed(fa, fb):
            container_uid, frame_path = _frame_locality(
                head_struct_uid, op_b, entry_b.frame_path, _frame_value(fb),
            )
            changes.append(ElementChange(
                key, f"{op_b.id}::frame::{key}", "value", "modified",
                _frame_display(fb),
                detail=f"{_frame_display(fa)} → {_frame_display(fb)}",
                container_uid=container_uid, frame_path=frame_path,
            ))
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
        if isinstance(ea.op, (CaseOperation, SequenceOperation)):
            pairs.append((ea, eb))
    h2b = {**exact, **fuzzy}
    for base_uid in sorted(h2b, key=_uid_sort):
        head_uid = h2b[base_uid]
        ea, eb = a.get(base_uid), b.get(head_uid)
        if ea is None or eb is None:
            continue
        if type(ea.op) is not type(eb.op):
            continue
        if isinstance(ea.op, (CaseOperation, SequenceOperation)):
            pairs.append((ea, eb))
    return pairs


def diff_uid(
    graph_a: InMemoryVIGraph, graph_b: InMemoryVIGraph,
    vi_name_a: str, vi_name_b: str,
) -> ChangeMap:
    """Build a UID-keyed change-map for two VI versions.

    Unlike ``diff_structured`` (which matches operations by name/count and so
    misses added/removed instances of repeated names), this matches every node
    and structure by its stable LabVIEW UID. The UIDs are exactly those the
    renderer emits as ``data-node`` / ``data-lv-struct``, so the map binds onto
    the rendered SVG with no reconciliation. Wire endpoints and modified-node
    detection layer on top of this (see tasks #10/#11).
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

    def _bounds(layout: Layout | None, uid: str) -> Rect | None:
        return layout.node_bounds.get(uid) if layout is not None else None

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
        cmap.changes.append(
            ElementChange(uid, op.id, kind, "added", _elem_label(op, kind),
                          _bounds(layout_b, uid),
                          chain_paths=_incident_chain_paths(layout_b, wires_b, uid),
                          container_uid=entry.container_uid,
                          frame_path=entry.frame_path)
        )
    # Removed: base-only node/structure with no dataflow counterpart in head.
    for uid in a.keys() - b.keys() - matched_a:
        entry = a[uid]
        op, kind = entry.op, entry.kind
        cmap.changes.append(
            ElementChange(uid, op.id, kind, "removed", _elem_label(op, kind),
                          _bounds(layout_a, uid),
                          chain_paths=_incident_chain_paths(layout_a, wires_a, uid),
                          container_uid=entry.container_uid,
                          frame_path=entry.frame_path)
        )
    # Modified: a constant present in BOTH versions by stable UID whose VALUE
    # changed — the canonical node-config change (e.g. a path/string/number the
    # author edited in place). Matched by UID, not dataflow: a same-UID constant
    # is the same constant, so this needs no fuzzy pass. Guarded by unchanged type
    # (a differing type at a shared UID is a UID recycle, not an edit — skip it,
    # it surfaces via the node add/remove of whatever now owns the UID). Wire and
    # operation-config modifications are separate passes (#10, and a future
    # operation "modified" that must first distinguish a genuine reconfigure from
    # a UID recycle — no test pair for that exists in the corpus yet).
    consts_a = {_uid_of(c.id): c for c in graph_a.get_constants(va)}
    consts_b = {_uid_of(c.id): c for c in graph_b.get_constants(vb)}
    for uid in consts_a.keys() & consts_b.keys():
        ca, cb = consts_a[uid], consts_b[uid]
        type_a = ca.lv_type.to_python() if ca.lv_type else None
        type_b = cb.lv_type.to_python() if cb.lv_type else None
        if type_a == type_b and repr(ca.value) != repr(cb.value):
            container_uid, frame_path = _constant_locality(cb, b)
            cmap.changes.append(ElementChange(
                uid, cb.id, "node", "modified", _const_label(cb),
                _bounds(layout_b, uid),
                bounds_base=_bounds(layout_a, uid),
                detail=f"{_value_disp(ca.value)} → {_value_disp(cb.value)}",
                container_uid=container_uid, frame_path=frame_path,
            ))

    # Wire endpoint changes (#10): for every input terminal on an unchanged
    # node, compare its effective (tunnel-contracted) source across versions.
    # Reuses the exact/fuzzy node matching computed above.
    cmap.changes.extend(_wire_changes(
        graph_a, graph_b, va, vb, a, b, exact, fuzzy, layout_a, layout_b,
    ))

    # Frame set changes: within every Case/Sequence structure matched across
    # versions (same uid, or exact/fuzzy dataflow match), a whole frame
    # added/removed, or the same frame's selector/index value changed.
    for entry_a, entry_b in _matched_struct_pairs(a, b, exact, fuzzy):
        cmap.changes.extend(_struct_frame_changes(entry_a, entry_b))

    # Common UIDs and all matched pairs are unchanged at the node level — a node
    # wrapped in a new case, moved, re-keyed, or with only a wire added/removed is
    # not itself a changed node. Operation-config changes are a future pass (an
    # operation "modified" that must first distinguish a genuine reconfigure
    # from a UID recycle — no test pair for that exists in the corpus yet).
    cmap.common_node_uids = sorted(
        (uid for uid in a.keys() & b.keys() if a[uid].kind == "node"),
        key=_uid_sort,
    )
    # stable display order: structures first, then added < removed < modified,
    # then by UID
    _rank = {"added": 0, "removed": 1, "modified": 2}
    cmap.changes.sort(
        key=lambda c: (c.kind != "structure", _rank.get(c.change, 3),
                       _uid_sort(c.uid))
    )
    return cmap


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
                changes.append(SignatureChange(
                    "added", direction, name,
                    new_type=map_b[name].python_type(),
                ))
            elif name not in map_b:
                changes.append(SignatureChange(
                    "removed", direction, name,
                    old_type=map_a[name].python_type(),
                ))
            else:
                type_a = map_a[name].python_type()
                type_b = map_b[name].python_type()
                if type_a != type_b:
                    changes.append(SignatureChange(
                        "type_changed", direction, name,
                        old_type=type_a, new_type=type_b,
                    ))
    return changes


def _diff_operations(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[OperationChange]:
    ops_a = ga.get_operations(va)
    ops_b = gb.get_operations(vb)

    counts_a = _op_counts(ops_a)
    counts_b = _op_counts(ops_b)

    changes: list[OperationChange] = []
    all_keys = sorted(set(counts_a) | set(counts_b))
    for key in all_keys:
        name, node_type = key
        ca = counts_a.get(key, 0)
        cb = counts_b.get(key, 0)
        display = name or f"(unnamed {node_type})"
        for _ in range(max(0, cb - ca)):
            changes.append(OperationChange("added", display, node_type))
        for _ in range(max(0, ca - cb)):
            changes.append(OperationChange("removed", display, node_type))
    return changes


def _diff_constants(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[ConstantChange]:
    # Top-level constants only. Constants nested inside a structure frame
    # are positioned by the Structures section (mirrors how nested
    # operations are reported under Structures, not flat Operations).
    consts_a = [c for c in ga.get_constants(va) if c.parent is None]
    consts_b = [c for c in gb.get_constants(vb) if c.parent is None]

    changes: list[ConstantChange] = []

    # Named constants — match by name.
    named_a = {c.name: c for c in consts_a if c.name}
    named_b = {c.name: c for c in consts_b if c.name}
    for name in sorted(set(named_a) | set(named_b)):
        if name not in named_a:
            changes.append(ConstantChange(
                "added", name, new_value=repr(named_b[name].value),
            ))
        elif name not in named_b:
            changes.append(ConstantChange(
                "removed", name, old_value=repr(named_a[name].value),
            ))
        else:
            va_val = repr(named_a[name].value)
            vb_val = repr(named_b[name].value)
            if va_val != vb_val:
                changes.append(ConstantChange(
                    "value_changed", name,
                    old_value=va_val, new_value=vb_val,
                ))

    # Unnamed constants — match by (value, type) multiset.
    unnamed_a = [c for c in consts_a if not c.name]
    unnamed_b = [c for c in consts_b if not c.name]
    keys_a = Counter(_const_key(c) for c in unnamed_a)
    keys_b = Counter(_const_key(c) for c in unnamed_b)
    for key in sorted(set(keys_a) | set(keys_b)):
        diff = keys_b.get(key, 0) - keys_a.get(key, 0)
        val_repr, type_str = key
        label = f"(unnamed {type_str})"
        for _ in range(max(0, diff)):
            changes.append(ConstantChange("added", label, new_value=val_repr))
        for _ in range(max(0, -diff)):
            changes.append(ConstantChange("removed", label, old_value=val_repr))

    return changes


def _diff_wiring(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[WiringChange]:
    # Relabel unnamed-constant endpoints by type/value (e.g. "error cluster")
    # instead of an opaque raw UID.
    const_labels = _const_label_by_id(ga.get_constants(va))
    const_labels.update(_const_label_by_id(gb.get_constants(vb)))
    return _wiring_changes(ga.get_wires(va), gb.get_wires(vb), const_labels)


def _wiring_changes(
    wires_a: list[Wire], wires_b: list[Wire],
    const_labels: Mapping[str, str],
) -> list[WiringChange]:
    # Drop structure-internal edges (tunnel/selector/sRN plumbing — always
    # self-loops on the structure node). They're implied by the structure
    # add/remove and are surfaced in the Structures section instead.
    wires_a = [w for w in wires_a if not _is_internal_wire(w)]
    wires_b = [w for w in wires_b if not _is_internal_wire(w)]

    # A wire is only noteworthy if it touches a node present in BOTH
    # versions (a genuine rewire of unchanged topology — a "splice"). A
    # wire whose endpoints are all new/removed is dragged along by the
    # node change that the Operations/Constants/Structures sections already
    # report, so it's redundant noise here.
    shared = _endpoint_names(wires_a) & _endpoint_names(wires_b)

    keys_a = Counter(_wire_key(w, const_labels) for w in wires_a)
    keys_b = Counter(_wire_key(w, const_labels) for w in wires_b)
    raw_names: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for w in (*wires_a, *wires_b):
        raw_names.setdefault(
            _wire_key(w, const_labels), (w.source.name, w.dest.name),
        )

    changes: list[WiringChange] = []
    for key in sorted(set(keys_a) | set(keys_b)):
        diff = keys_b.get(key, 0) - keys_a.get(key, 0)
        if diff == 0:
            continue
        src_name, dst_name = raw_names[key]
        if src_name not in shared and dst_name not in shared:
            continue  # implied by a node add/remove — suppress
        src, dst = key
        desc = f"{src} -> {dst}"
        for _ in range(max(0, diff)):
            changes.append(WiringChange("added", desc))
        for _ in range(max(0, -diff)):
            changes.append(WiringChange("removed", desc))
    return changes


def _diff_structures(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[StructureChange]:
    structs_a = _collect_structures(ga.get_operations(va))
    structs_b = _collect_structures(gb.get_operations(vb))
    consts_a = ga.get_constants(va)
    consts_b = gb.get_constants(vb)
    wires_a = ga.get_wires(va)
    wires_b = gb.get_wires(vb)
    labels = _const_label_by_id(consts_a)
    labels.update(_const_label_by_id(consts_b))

    map_a = {(s.name, type(s).__name__): s for s in structs_a}
    map_b = {(s.name, type(s).__name__): s for s in structs_b}

    changes: list[StructureChange] = []
    for key in sorted(set(map_a) | set(map_b)):
        name, kind = key
        label = name or kind
        if key not in map_a:
            changes.append(StructureChange(
                "added",
                label,
                _structure_content_summary(map_b[key], consts_b, wires_b, labels),
            ))
        elif key not in map_b:
            changes.append(StructureChange(
                "removed",
                label,
                _structure_content_summary(map_a[key], consts_a, wires_a, labels),
            ))
        else:
            detail = _compare_structure(
                map_a[key], map_b[key], consts_a, consts_b,
                wires_a, wires_b, labels,
            )
            if detail:
                changes.append(StructureChange("changed", label, detail))
    return changes


def _selector_source(
    case: CaseOperation, wires: list[Wire], const_labels: Mapping[str, str],
) -> str | None:
    """The external node feeding a case structure's selector terminal —
    the splice that drives which frame runs. Recovered here because that
    wire (new node -> new case) is suppressed in the Wiring section."""
    sel = case.selector_terminal
    if not sel:
        return None
    for w in wires:
        if w.dest.terminal_id == sel and not _is_internal_wire(w):
            return _endpoint_label(w.source, const_labels)
    return None


def _structure_content_summary(
    s: Operation, constants: list[Constant],
    wires: list[Wire] | None = None,
    const_labels: Mapping[str, str] | None = None,
) -> str | None:
    """Enumerate a structure's selector source + per-frame operations and
    constants, so an added/removed structure shows what's inside it (incl.
    nested constants excluded from the flat Constants section) and what
    drives it (the selector, whose wire the Wiring section suppresses)."""
    by_frame = _consts_by_frame(constants, s.id)
    if isinstance(s, CaseOperation):
        frames = [(str(f.selector_value), f) for f in s.frames]
    elif isinstance(s, SequenceOperation):
        frames = [(str(i), f) for i, f in enumerate(s.frames)]
    else:
        return None

    parts: list[str] = []
    if isinstance(s, CaseOperation):
        sel = _selector_source(s, wires or [], const_labels or {})
        if sel:
            parts.append(f"selector <- {sel}")
    for fkey, frame in frames:
        items = [op.name or op.node_type or "op" for op in frame.operations]
        items += [_const_label(c) for c in by_frame.get(fkey, [])]
        if items:
            parts.append(f"frame {fkey}: {', '.join(items)}")
    return "; ".join(parts) if parts else None


# ── Utility functions ─────────────────────────────────────────────────


def _terminal_map(terminals: list[Terminal]) -> dict[str, Terminal]:
    """Map terminals by name, skipping error clusters."""
    return {
        t.name: t for t in terminals
        if t.name and not t.is_error_cluster
    }


def _op_key(op: Operation) -> tuple[str, str | None]:
    return (op.name or "?", op.node_type)


def _op_counts(ops: list[Operation]) -> Counter[tuple[str, str | None]]:
    return Counter(_op_key(op) for op in ops)


def _const_key(c: Constant) -> tuple[str, str]:
    type_str = c.lv_type.to_python() if c.lv_type else "unknown"
    return (repr(c.value), type_str)


def _is_internal_wire(w: Wire) -> bool:
    """A structure-internal edge (tunnel inner<->outer, selector, sRN
    in->out pairing) — always a self-loop on the structure node."""
    return w.source.node_id == w.dest.node_id


def _endpoint_names(wires: list[Wire]) -> set[str]:
    """Named nodes appearing as a wire endpoint (identity across versions)."""
    names: set[str] = set()
    for w in wires:
        if w.source.name:
            names.add(w.source.name)
        if w.dest.name:
            names.add(w.dest.name)
    return names


def _const_label_by_id(constants: list[Constant]) -> dict[str, str]:
    """Map each unnamed constant's node id to a type/value display label."""
    return {c.id: _const_label(c) for c in constants if not c.name}


def _endpoint_label(end: WireEnd, const_labels: Mapping[str, str]) -> str:
    return end.name or const_labels.get(end.node_id) or end.node_id.split("::")[-1]


def _wire_key(
    w: Wire, const_labels: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    labels = const_labels or {}
    return (_endpoint_label(w.source, labels), _endpoint_label(w.dest, labels))


def _collect_structures(ops: list[Operation]) -> list[Operation]:
    return [
        op for op in ops
        if isinstance(op, CaseOperation | LoopOperation | SequenceOperation)
    ]


def _consts_by_frame(
    constants: list[Constant], parent_id: str,
) -> dict[str, list[Constant]]:
    """Group a structure's nested constants by frame key (as str)."""
    grouped: dict[str, list[Constant]] = {}
    for c in constants:
        if c.parent == parent_id:
            grouped.setdefault(str(c.frame), []).append(c)
    return grouped


def _value_disp(value: object) -> str:
    """Readable one-line display of a constant's value for a modified detail.
    A plain string shows as-is (no repr quote noise); anything else falls back to
    repr. Newlines/tabs are flattened so the detail stays one line."""
    s = value if isinstance(value, str) else repr(value)
    return " ".join(s.split())


def _const_label(c: Constant) -> str:
    """Short label for a constant in a frame diff."""
    if c.lv_type and _is_error_cluster(c.lv_type):
        return "error cluster"
    type_str = c.lv_type.to_python() if c.lv_type else "unknown"
    return f"{type_str} constant"


def _frame_content_delta(
    ops_a: list[Operation], consts_a: list[Constant],
    ops_b: list[Operation], consts_b: list[Constant],
) -> list[str]:
    """Per-frame additions/removals of operations and constants."""
    parts: list[str] = []

    oa, ob = _op_counts(ops_a), _op_counts(ops_b)
    for key in sorted(set(oa) | set(ob), key=lambda k: (k[0] or "", k[1] or "")):
        delta = ob.get(key, 0) - oa.get(key, 0)
        label = key[0] or f"(unnamed {key[1]})"
        if delta > 0:
            parts.append(f"+{delta} {label}")
        elif delta < 0:
            parts.append(f"-{-delta} {label}")

    ka = Counter(_const_key(c) for c in consts_a)
    kb = Counter(_const_key(c) for c in consts_b)
    label_for = {_const_key(c): _const_label(c) for c in (*consts_a, *consts_b)}
    for key in sorted(set(ka) | set(kb)):
        delta = kb.get(key, 0) - ka.get(key, 0)
        label = label_for[key]
        if delta > 0:
            parts.append(f"+{delta} {label}")
        elif delta < 0:
            parts.append(f"-{-delta} {label}")

    return parts


def _compare_structure(
    a: Operation, b: Operation,
    consts_a: list[Constant], consts_b: list[Constant],
    wires_a: list[Wire] | None = None,
    wires_b: list[Wire] | None = None,
    const_labels: Mapping[str, str] | None = None,
) -> str | None:
    wa, wb, labels = wires_a or [], wires_b or [], const_labels or {}
    if isinstance(a, CaseOperation) and isinstance(b, CaseOperation):
        parts: list[str] = []
        sel_a = _selector_source(a, wa, labels)
        sel_b = _selector_source(b, wb, labels)
        if sel_a != sel_b:
            parts.append(f"selector {sel_a} -> {sel_b}")
        if len(a.frames) != len(b.frames):
            parts.append(f"{len(a.frames)} frames -> {len(b.frames)} frames")
        else:
            frame_detail = _compare_frames(
                {str(f.selector_value): f for f in a.frames},
                {str(f.selector_value): f for f in b.frames},
                _consts_by_frame(consts_a, a.id),
                _consts_by_frame(consts_b, b.id),
            )
            if frame_detail:
                parts.append(frame_detail)
        return "; ".join(parts) if parts else None
    if isinstance(a, LoopOperation) and isinstance(b, LoopOperation):
        if a.loop_type != b.loop_type:
            return f"{a.loop_type} -> {b.loop_type}"
    if isinstance(a, SequenceOperation) and isinstance(b, SequenceOperation):
        if len(a.frames) != len(b.frames):
            return f"{len(a.frames)} frames -> {len(b.frames)} frames"
        return _compare_frames(
            {str(i): f for i, f in enumerate(a.frames)},
            {str(i): f for i, f in enumerate(b.frames)},
            _consts_by_frame(consts_a, a.id),
            _consts_by_frame(consts_b, b.id),
        )
    return None


def _compare_frames(
    frames_a: Mapping[str, Frame], frames_b: Mapping[str, Frame],
    consts_a: Mapping[str, list[Constant]], consts_b: Mapping[str, list[Constant]],
) -> str | None:
    """Per-frame content diff, attributed to each frame key."""
    details: list[str] = []
    for key in sorted(set(frames_a) | set(frames_b)):
        fa, fb = frames_a.get(key), frames_b.get(key)
        ops_a = list(fa.operations) if fa is not None else []
        ops_b = list(fb.operations) if fb is not None else []
        delta = _frame_content_delta(
            ops_a, consts_a.get(key, []),
            ops_b, consts_b.get(key, []),
        )
        if delta:
            details.append(f"frame {key}: {', '.join(delta)}")
    return "; ".join(details) if details else None
