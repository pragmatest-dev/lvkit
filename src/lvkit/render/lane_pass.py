"""Post-routing interval-coloring lane-assignment pass for wire nets.

THROWAWAY PROTOTYPE (see task instructions) — do not treat as final design.

Routing (``wire_router.py``) already places every wire. This pass runs
strictly AFTER routing and repositions already-routed polyline SEGMENTS only
where two DIFFERENT nets' segments genuinely overlap on the same track.
Segments with no conflict are left essentially where the router put them — a
wire only moves where it truly needs to make room for another net. Two
SAME-net segments are allowed (encouraged) to share a lane — a fan-out's
branches running together for a stretch — and the point where they stop
sharing a lane and diverge is marked as a junction dot.

This is classic interval-graph coloring / left-edge channel routing,
restricted to the axis-aligned segments sliced out of the routed polylines:

  1. SNAP every branch to strictly axis-aligned segments (the plain router's
     ``align_tol`` leaves near-axis endpoint legs off by up to ~2px; snapping
     forward guarantees every sliced segment is exactly H or V so the
     rebuild can never emit a diagonal).
  2. Slice every branch into its maximal H/V segments (track, interval,
     owning net id).
  3. Union-find segments into "conflict clusters", per orientation: two
     segments conflict iff DIFFERENT net, close track (< ``PITCH``), and a
     real interval overlap (> ``OVERLAP_TOL``).
  4. Left-edge color each cluster (size > 1): sort by (lo, track, net_id),
     assign the lowest lane whose current occupants are either same-net or
     non-overlapping with the candidate. Same-net segments freely coalesce.
  5. Re-track: lane k in a cluster -> ``min(original track in cluster) + k
     * PITCH`` — but an offset is REJECTED (segment left at its nominal
     track) if it would push the segment through an obstacle interior it
     didn't cross nominally, or out of its wire's confinement rect. This
     keeps the router's obstacle/containment guarantees.
  6. Rebuild each branch's polyline from its (possibly re-tracked) segments,
     always anchored EXACTLY to the original source/dest terminal points
     with short orthogonal connectors where a re-tracked end segment no
     longer sits on the terminal row/column.
  7. Junctions: per net (a fan-out), the furthest point along which two
     branches' REBUILT polylines coincide before diverging — read
     GEOMETRICALLY (direction + arc-length walk, not exact vertex match) so
     a shared trunk split toward each sink is dotted even when the branches
     subdivide that trunk differently.

Scoped per ``frame_path`` group: only wires ever shown together can
possibly conflict, matching how the rest of the renderer treats hidden
case/sequence frames as mutually exclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .wire_router import Point, Rect, _compress

if TYPE_CHECKING:
    from .scene import FramePath, RenderWireNet

# Lane spacing. Wire base width is _LINE_W (1.2, style.py) and casing is
# Theme.wire_casing (~1.1 half-width per side, ~2.2 total) + a small gap so
# two adjacent lanes' casings don't touch: 1.2 + 2*1.1 + ~0.6 ~= 5.0.
PITCH = 5.0

# Two same-track segments of different nets are only a real conflict if
# their intervals overlap by more than a graze.
OVERLAP_TOL = 2.0

_EPS = 1e-6

# A single-segment near-straight wire whose two terminal centers differ by a
# SUB-PIXEL amount off-axis (e.g. a tunnel's 9px-tall termBounds center at .5
# vs a front-panel terminal's 16px-tall center at .0 -> 0.5px) is drawn STRAIGHT
# rather than bent into a mid-Z jog to force strict orthogonality. LabVIEW draws
# such a wire straight; a jag under ~1px is a visible defect, not fidelity. Only
# an off-axis gap of at least this many px earns an actual orthogonal jog.
_STRAIGHT_TOL = 1.0


@dataclass(frozen=True)
class _Segment:
    net_id: str
    orientation: str  # "H" (fixed y / track is a y-coord) or "V" (fixed x)
    track: float
    lo: float
    hi: float


@dataclass(frozen=True)
class BranchCtx:
    """Per-branch routing context, so the lane offset can honour the same
    obstacle/containment rules the router did for THIS wire."""

    obstacles: tuple[Rect, ...] = ()
    owners: tuple[Rect, ...] = ()  # endpoint node boxes (legitimately touched)
    confine: Rect | None = None
    # Whether the branch's source/dest endpoint is a STRUCTURE BORDER
    # terminal (tunnel / shift register). Its stub leaves PERPENDICULAR to
    # the structure edge and that axis carries the tunnel's inner/outer face,
    # so that stub must NEVER be lane-offset (a connector would swing it to
    # the wrong edge). Plain-node stubs carry no such constraint and stay
    # free to separate.
    src_border: bool = False
    dst_border: bool = False
    # Whether this branch's geometry came from LabVIEW's OWN decoded wire table
    # (``compressedWireTable``) rather than our fallback auto-router. Lanes are
    # an auto-route concept — separating wires OUR router might stack on one
    # track. A decoded wire already carries LabVIEW's real, already-separated
    # geometry, so it bypasses this pass ENTIRELY: no snap, no slice, no
    # re-track, no rebuild. Only auto-routed branches are lane candidates.
    faithful: bool = False


def _snap_orthogonal(pts: list[Point]) -> list[Point]:
    """Force ``pts`` to a strictly axis-aligned polyline, carrying each
    snapped point forward. Segment i is horizontal if |dx|>=|dy| else
    vertical; the off-axis coordinate of the new point is forced equal to
    the previous (snapped) point's. Zero-length steps are dropped. The FIRST
    point is preserved exactly; the last may drift by the sub-pixel snap
    error (the rebuild re-anchors to the true terminal, so this never leaks
    into the output)."""
    if len(pts) < 2:
        return list(pts)
    out: list[Point] = [pts[0]]
    prev = pts[0]
    for cx, cy in pts[1:]:
        px, py = prev
        if abs(cx - px) >= abs(cy - py):
            snapped = (cx, py)  # horizontal: keep prev y
        else:
            snapped = (px, cy)  # vertical: keep prev x
        if abs(snapped[0] - px) > _EPS or abs(snapped[1] - py) > _EPS:
            out.append(snapped)
            prev = snapped
    return out


def _slice_branch(pts: list[Point], net_id: str) -> list[_Segment]:
    """Slice a STRICTLY axis-aligned polyline into maximal H/V segments,
    merging consecutive collinear (same orientation + track) runs so the
    result strictly alternates H/V (the rebuild's corner formula relies on
    that)."""
    segs: list[_Segment] = []
    for i in range(len(pts) - 1):
        (ax, ay), (bx, by) = pts[i], pts[i + 1]
        if abs(ay - by) <= _EPS and abs(ax - bx) <= _EPS:
            continue  # degenerate
        if abs(ay - by) <= _EPS:
            orient, track = "H", ay
            lo, hi = (ax, bx) if ax <= bx else (bx, ax)
        else:
            orient, track = "V", ax
            lo, hi = (ay, by) if ay <= by else (by, ay)
        if (
            segs
            and segs[-1].orientation == orient
            and abs(segs[-1].track - track) <= _EPS
        ):
            prev = segs[-1]
            segs[-1] = _Segment(
                net_id,
                orient,
                track,
                min(prev.lo, lo),
                max(prev.hi, hi),
            )
        else:
            segs.append(_Segment(net_id, orient, track, lo, hi))
    return segs


class _UnionFind:
    """Deterministic union-find: the lower index always becomes the root,
    so cluster identity never depends on traversal/iteration order."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if ra < rb:
            self._parent[rb] = ra
        else:
            self._parent[ra] = rb


def _cluster_one_orientation(segs: list[_Segment]) -> dict[int, list[int]]:
    """Union-find conflict clusters among segments of ONE orientation.
    Returns root-local-index -> member local-index list."""
    uf = _UnionFind(len(segs))
    order = sorted(
        range(len(segs)),
        key=lambda i: (segs[i].track, segs[i].lo, segs[i].net_id),
    )
    for a_pos in range(len(order)):
        i = order[a_pos]
        si = segs[i]
        for b_pos in range(a_pos + 1, len(order)):
            j = order[b_pos]
            sj = segs[j]
            if sj.track - si.track >= PITCH:
                break  # sorted ascending by track -- no more candidates
            if si.net_id == sj.net_id:
                continue
            overlap = min(si.hi, sj.hi) - max(si.lo, sj.lo)
            if overlap > OVERLAP_TOL:
                uf.union(i, j)
    clusters: dict[int, list[int]] = {}
    for i in range(len(segs)):
        clusters.setdefault(uf.find(i), []).append(i)
    return clusters


def _color_cluster(member_idxs: list[int], all_segs: list[_Segment]) -> dict[int, int]:
    """Left-edge lane coloring for one cluster. Returns global seg index ->
    lane index (0-based)."""
    order = sorted(
        member_idxs,
        key=lambda i: (all_segs[i].lo, all_segs[i].track, all_segs[i].net_id),
    )
    lanes: list[list[int]] = []
    lane_of: dict[int, int] = {}
    for i in order:
        si = all_segs[i]
        for k, members in enumerate(lanes):
            fits = True
            for j in members:
                sj = all_segs[j]
                if sj.net_id == si.net_id:
                    continue
                overlap = min(si.hi, sj.hi) - max(si.lo, sj.lo)
                if overlap > OVERLAP_TOL:
                    fits = False
                    break
            if fits:
                members.append(i)
                lane_of[i] = k
                break
        else:
            lanes.append([i])
            lane_of[i] = len(lanes) - 1
    return lane_of


def _interior_hits(
    orientation: str,
    track: float,
    lo: float,
    hi: float,
    ctx: BranchCtx,
) -> set[int]:
    """Indices of ``ctx.obstacles`` whose INTERIOR this axis-aligned segment
    crosses (owners excluded — a terminal legitimately sits on its own node's
    box). Same 1px interior inset the router uses."""
    hits: set[int] = set()
    for k, obstacle in enumerate(ctx.obstacles):
        if obstacle in ctx.owners:
            continue
        ox1, oy1, ox2, oy2 = obstacle
        if orientation == "H":
            if oy1 + 1 < track < oy2 - 1 and hi > ox1 + 1 and lo < ox2 - 1:
                hits.add(k)
        else:
            if ox1 + 1 < track < ox2 - 1 and hi > oy1 + 1 and lo < oy2 - 1:
                hits.add(k)
    return hits


def _offset_blocked(seg: _Segment, target: float, ctx: BranchCtx) -> bool:
    """Whether moving ``seg`` to ``target`` track must be rejected: it would
    (a) newly cross an obstacle interior it doesn't cross nominally, or
    (b) leave the wire's confinement rect. The residual different-net
    conflict is accepted for that one segment (it stays put)."""
    nominal = _interior_hits(seg.orientation, seg.track, seg.lo, seg.hi, ctx)
    moved = _interior_hits(seg.orientation, target, seg.lo, seg.hi, ctx)
    if moved - nominal:
        return True
    if ctx.confine is not None:
        cx1, cy1, cx2, cy2 = ctx.confine
        lo_b, hi_b = (cy1, cy2) if seg.orientation == "H" else (cx1, cx2)
        if not (lo_b <= target <= hi_b):
            return True
    return False


def _lane_assign(
    all_segs: list[_Segment],
    seg_ctx: list[BranchCtx],
    protected: set[int],
) -> dict[int, float]:
    """Global seg index -> new track, for every segment whose track
    genuinely changes AND whose move is not obstacle/confinement-blocked
    (segments not in the dict keep their nominal track).

    ``protected`` = stub segments that touch a STRUCTURE BORDER terminal
    (tunnel / shift register). These still take part in CLUSTERING (so a
    conflicting mid-run of another net moves off THEIR track), but are never
    themselves offset: moving such a stub off the terminal row/column would
    swing it perpendicular via a connector, breaking the tunnel's inner/outer
    face attachment. Plain-node stubs are NOT protected — they carry no face
    constraint and stay free to separate (keeping tangential low)."""
    new_track: dict[int, float] = {}
    for orientation in ("H", "V"):
        idxs = [i for i, s in enumerate(all_segs) if s.orientation == orientation]
        if not idxs:
            continue
        sub = [all_segs[i] for i in idxs]
        local_clusters = _cluster_one_orientation(sub)
        for local_members in local_clusters.values():
            if len(local_members) < 2:
                continue  # no real conflict -- untouched
            global_members = [idxs[m] for m in local_members]
            lane_of = _color_cluster(global_members, all_segs)
            base = min(all_segs[i].track for i in global_members)
            for i, lane in lane_of.items():
                if i in protected:
                    continue
                target = base + lane * PITCH
                if abs(target - all_segs[i].track) <= _EPS:
                    continue
                if _offset_blocked(all_segs[i], target, seg_ctx[i]):
                    continue
                new_track[i] = target
    return new_track


def _rebuild_branch(
    orig_pts: list[Point],
    segs: list[_Segment],
    seg_idxs: list[int],
    new_track: dict[int, float],
) -> list[Point]:
    """Rebuild one branch's polyline from its (possibly re-tracked)
    segments. ``orig_pts[0]``/``orig_pts[-1]`` (the fixed source/dest
    terminal points) are ALWAYS preserved exactly. A short orthogonal
    connector is inserted only where a re-tracked END segment no longer sits
    on the terminal row/column; a NON-offset end segment absorbs the
    sub-pixel snap drift onto the exact terminal coordinate (no spurious
    jog). Strictly orthogonal by construction."""
    n = len(segs)
    if n == 0:
        return list(orig_pts)
    tracks = [new_track.get(gi, seg.track) for gi, seg in zip(seg_idxs, segs)]
    orients = [seg.orientation for seg in segs]
    src, dst = orig_pts[0], orig_pts[-1]

    # A SINGLE-segment branch whose terminals differ sub-pixel on the
    # off-axis (a near-straight wire the router drew slightly diagonal) can't
    # be both straight AND orthogonal. Absorbing the drift at either end
    # would swing THAT terminal's stub perpendicular — wrong for a border
    # tunnel, whose stub axis carries its inner/outer face. Put the tiny jog
    # in the MIDDLE (a Z) so BOTH end stubs keep the dominant axis and both
    # terminal faces stay correct.
    if n == 1 and seg_idxs[0] not in new_track:
        if orients[0] == "H":
            if abs(src[1] - dst[1]) <= _STRAIGHT_TOL:
                return _compress([src, dst], tol=_EPS)
            midx = (src[0] + dst[0]) / 2
            return _compress(
                [src, (midx, src[1]), (midx, dst[1]), dst],
                tol=_EPS,
            )
        if abs(src[0] - dst[0]) <= _STRAIGHT_TOL:
            return _compress([src, dst], tol=_EPS)
        midy = (src[1] + dst[1]) / 2
        return _compress([src, (src[0], midy), (dst[0], midy), dst], tol=_EPS)

    # Absorb sub-pixel snap drift on NON-offset end segments onto the exact
    # terminal coordinate, so a straight wire meets its terminal with no jog.
    if seg_idxs[0] not in new_track:
        tracks[0] = src[1] if orients[0] == "H" else src[0]
    if seg_idxs[-1] not in new_track:
        tracks[-1] = dst[1] if orients[-1] == "H" else dst[0]

    out: list[Point] = [src]
    # start connector (only if the first segment moved off the terminal)
    if orients[0] == "H":
        if abs(tracks[0] - src[1]) > _EPS:
            out.append((src[0], tracks[0]))
    else:
        if abs(tracks[0] - src[0]) > _EPS:
            out.append((tracks[0], src[1]))

    # interior corners (strict H/V alternation guaranteed by _slice_branch)
    for i in range(n - 1):
        if orients[i] == "H":
            out.append((tracks[i + 1], tracks[i]))
        else:
            out.append((tracks[i], tracks[i + 1]))

    # end connector
    if orients[-1] == "H":
        if abs(tracks[-1] - dst[1]) > _EPS:
            out.append((dst[0], tracks[-1]))
    else:
        if abs(tracks[-1] - dst[0]) > _EPS:
            out.append((tracks[-1], dst[1]))
    out.append(dst)

    # Strict-tolerance compress: drop only EXACTLY collinear points. The
    # default 0.75px tol would collapse a sub-pixel orthogonal jog back into
    # a diagonal, violating the hard orthogonality invariant.
    return _compress(out, tol=_EPS)


def _pt_eq(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6


def _seg_dir(a: Point, b: Point) -> tuple[int, int]:
    """Unit axis direction from a to b (b assumed axis-aligned w.r.t. a)."""
    if abs(b[1] - a[1]) <= _EPS:
        return (1 if b[0] > a[0] else -1, 0)
    return (0, 1 if b[1] > a[1] else -1)


def _last_common(a: list[Point], b: list[Point]) -> Point | None:
    """Furthest point along which two polylines that start at the SAME source
    coincide, before diverging — walked GEOMETRICALLY by direction + step
    length so a shared run is found even when the two branches subdivide it
    into different vertices. Returns None if they split at the source (no
    shared trunk)."""
    if not a or not b or not _pt_eq(a[0], b[0]):
        return None
    ia = ib = 0
    cur = a[0]
    last = cur
    while ia < len(a) - 1 and ib < len(b) - 1:
        na, nb = a[ia + 1], b[ib + 1]
        if _seg_dir(cur, na) != _seg_dir(cur, nb):
            break  # diverge here
        da = abs(na[0] - cur[0]) + abs(na[1] - cur[1])
        db = abs(nb[0] - cur[0]) + abs(nb[1] - cur[1])
        if abs(da - db) <= _EPS:
            cur = na
            ia += 1
            ib += 1
        elif da < db:
            cur = na  # a's vertex lands mid-b-segment; b keeps its index
            ia += 1
        else:
            cur = nb
            ib += 1
        last = cur
    if _pt_eq(last, a[0]):
        return None
    return last


def _find_junctions(branches: list[list[Point]]) -> list[Point]:
    """Per-net junction dots: for every pair of same-net branches, the
    furthest point of their shared trunk before they diverge (geometric
    walk). A 3-way fan-out whose branches share a trunk and peel off one at a
    time yields one dot per peel-off (~2 dots). Deduped + sorted for
    determinism."""
    if len(branches) < 2:
        return []
    seen: set[tuple[float, float]] = set()
    out: list[Point] = []
    for bi in range(len(branches)):
        for bj in range(bi + 1, len(branches)):
            pt = _last_common(branches[bi], branches[bj])
            if pt is None:
                continue
            key = (round(pt[0], 3), round(pt[1], 3))
            if key not in seen:
                seen.add(key)
                out.append(pt)
    out.sort(key=lambda p: (p[0], p[1]))
    return out


def _net_id(net: RenderWireNet, net_idx: int) -> str:
    if net.source is not None:
        return net.source.source.terminal_id
    return f"__net{net_idx}__"


def apply_lane_pass(
    nets: list[RenderWireNet],
    branch_ctx: dict[tuple[int, int], BranchCtx] | None = None,
) -> list[RenderWireNet]:
    """Run the lane-assignment pass over already-routed ``RenderWireNet``s.

    ``branch_ctx`` maps (net index in ``nets``, branch index) -> the routing
    context (obstacles/owners/confinement) that wire used, so an offset never
    violates the router's obstacle/containment guarantees. Returns a NEW list
    of ``RenderWireNet`` with ``branches`` snapped orthogonal + repositioned
    only where needed and ``junctions`` populated from the rebuilt geometry.
    """
    if not nets:
        return nets
    ctx_map = branch_ctx or {}

    by_path: dict[FramePath, list[int]] = {}
    for i, net in enumerate(nets):
        by_path.setdefault(net.frame_path, []).append(i)

    rebuilt: dict[tuple[int, int], list[Point]] = {}
    junctions_by_net: dict[int, list[Point]] = {}

    def _path_key(path: FramePath) -> str:
        return ";".join(f"{s}={v}" for s, v in path)

    for path in sorted(by_path, key=_path_key):
        net_idxs = by_path[path]

        all_segs: list[_Segment] = []
        seg_ctx: list[BranchCtx] = []
        branch_segs: dict[tuple[int, int], list[int]] = {}
        # Stub segments that touch a BORDER terminal — never offset (see
        # _lane_assign / BranchCtx.src_border). Plain-node stubs stay free.
        protected: set[int] = set()
        for ni in net_idxs:
            net = nets[ni]
            net_id = _net_id(net, ni)
            for bi, branch in enumerate(net.branches):
                ctx = ctx_map.get((ni, bi), BranchCtx())
                if ctx.faithful:
                    # Decoded wire: LabVIEW already placed it. Bypass the pass
                    # entirely — keep its geometry verbatim and contribute NO
                    # segments, so it is invisible to lane assignment and never
                    # snapped/rebuilt (which would jag a sub-pixel diagonal).
                    rebuilt[(ni, bi)] = list(branch)
                    continue
                snapped = _snap_orthogonal(branch)
                idxs: list[int] = []
                for seg in _slice_branch(snapped, net_id):
                    idxs.append(len(all_segs))
                    all_segs.append(seg)
                    seg_ctx.append(ctx)
                branch_segs[(ni, bi)] = idxs
                if idxs:
                    if ctx.src_border:
                        protected.add(idxs[0])
                    if ctx.dst_border:
                        protected.add(idxs[-1])

        new_track = _lane_assign(all_segs, seg_ctx, protected)

        for (ni, bi), seg_idxs in branch_segs.items():
            orig = nets[ni].branches[bi]
            segs = [all_segs[i] for i in seg_idxs]
            rebuilt[(ni, bi)] = _rebuild_branch(orig, segs, seg_idxs, new_track)

        for ni in net_idxs:
            n_branches = len(nets[ni].branches)
            branches = [rebuilt[(ni, bi)] for bi in range(n_branches)]
            junctions_by_net[ni] = _find_junctions(branches)

    out: list[RenderWireNet] = []
    for i, net in enumerate(nets):
        branches = [rebuilt[(i, bi)] for bi in range(len(net.branches))]
        out.append(
            replace(
                net,
                branches=branches,
                junctions=junctions_by_net.get(i, []),
            )
        )
    return out
