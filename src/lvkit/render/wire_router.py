"""Orthogonal wire router for faithful LabVIEW block-diagram rendering.

LabVIEW stores every terminal's exact position but no readable wire path (only an
opaque ``compressedWireTable`` blob). This router recovers LabVIEW-style
orthogonal wires from endpoints alone:

  1. Clean routes first — straight, single-elbow (L), or Z — the way LabVIEW
     draws an unobstructed wire. A clean route is rejected only if it enters an
     obstacle interior OR runs *coincident along* an obstacle outline (hugging);
     a brief perpendicular graze is fine.
  2. Otherwise an orthogonal **visibility graph** (Hanan grid built from the
     obstacle edges, each obstacle inflated by a clearance margin) searched with
     Dijkstra + a bend penalty. Because clearance is baked into the graph node
     coordinates, a routed wire can never lie on an outline (no hugging), and the
     sparse, obstacle-derived graph cannot explode the way a uniform-pixel A*
     grid does.

Contract (a drop-in for the old hybrid): ``WireRouter(obstacles, bounds,
config)`` + ``route(p1, p2, endpoints, p1_owner, p2_owner) -> list[Point]`` — an
endpoint-inclusive polyline, never None. ``endpoints`` is accepted for API
compatibility but unused. ``p1_owner``/``p2_owner`` are the rects the endpoints
sit on; a wire may pass through exactly those (a terminal legitimately sits on
its own node's edge) but no other obstacle. All routes are clamped to ``bounds``
(a confined wire's container interior), so a contained wire never leaves its frame.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x1, y1, x2, y2


@dataclass(frozen=True)
class RouterConfig:
    bend_penalty: float = 4.0
    # Endpoints within this many px on one axis are treated as aligned on that
    # axis — LabVIEW draws a plain straight segment rather than a jog for
    # sub-pixel/near-pixel offsets (heap terminal centers are rarely exactly
    # equal even when the diagram clearly intends a straight wire).
    align_tol: float = 2.0
    # Wires keep this many px clear of every non-owner obstacle. Baked into the
    # visibility-graph node coordinates and used by the clean-candidate hug test.
    clearance: float = 4.0


class WireRouter:
    """Routes orthogonal wires over a fixed set of rectangular obstacles."""

    def __init__(
        self,
        obstacles: list[Rect],
        bounds: Rect,
        config: RouterConfig | None = None,
    ) -> None:
        self._obstacles = list(obstacles)
        self._bounds = bounds
        self._cfg = config or RouterConfig()

    # -- public API ---------------------------------------------------------
    def route(
        self,
        p1: Point,
        p2: Point,
        endpoints: list[Point],
        p1_owner: Rect | None = None,
        p2_owner: Rect | None = None,
    ) -> list[Point]:
        del endpoints  # unused; kept for API compatibility
        for cand in self._clean_candidates(p1, p2):
            if not self._crosses(cand, p1_owner, p2_owner):
                return cand
        vg = self._visibility_route(p1, p2, p1_owner, p2_owner)
        if vg is not None:
            return vg
        # Last resort (no path found even in the visibility graph): the
        # least-bad clean candidate, so a wire is always drawn.
        return self._clean_candidates(p1, p2)[0]

    # -- clean routes -------------------------------------------------------
    def _clean_candidates(self, p1: Point, p2: Point) -> list[list[Point]]:
        """Candidate routes in ascending bend count: straight (0) < L (1) < Z (2).

        Endpoints within ``align_tol`` on an axis are treated as aligned (a wire
        LabVIEW means to be straight isn't jogged over a sub-pixel offset). A
        "feedback" route (``p2`` left of ``p1``) drops its vertical leg at the
        source's own exit x so it clears the source vertically instead of
        doubling back through the source's row.
        """
        (x1, y1), (x2, y2) = p1, p2
        tol = self._cfg.align_tol
        cands: list[list[Point]] = []
        if abs(y1 - y2) <= tol or abs(x1 - x2) <= tol:
            cands.append([p1, p2])                      # straight (0 bends)
        if x2 < x1 - tol:
            cands.append([p1, (x1, y2), p2])
            cands.append([p1, (x2, y1), p2])
        else:
            cands.append([p1, (x2, y1), p2])            # horizontal-first L
            cands.append([p1, (x1, y2), p2])            # vertical-first L
        jog = 9.0
        if x2 >= x1:
            jx = min(x1 + jog, (x1 + x2) / 2)
        else:
            jx = max(x1 - jog, (x1 + x2) / 2)
        cands.append([p1, (jx, y1), (jx, y2), p2])       # Z (2 bends)
        return cands

    def _crosses(
        self,
        pts: list[Point],
        p1_owner: Rect | None = None,
        p2_owner: Rect | None = None,
    ) -> bool:
        """True if the polyline enters a non-owner obstacle INTERIOR, or runs
        COINCIDENT ALONG an obstacle outline (hugging) for more than a graze.

        The wire's own endpoint terminal legitimately sits on its own node's
        edge, so ``p1_owner``/``p2_owner`` are exempt. A perpendicular crossing
        that briefly clips an obstacle's clearance band is allowed — only a
        segment running parallel-and-adjacent to an edge (the "wire lies on the
        outline" defect) is rejected.
        """
        c = self._cfg.clearance
        for i in range(len(pts) - 1):
            (ax, ay), (bx, by) = pts[i], pts[i + 1]
            horizontal = abs(by - ay) <= abs(bx - ax)
            for obstacle in self._obstacles:
                if obstacle == p1_owner or obstacle == p2_owner:
                    continue
                bx1, by1, bx2, by2 = obstacle
                if horizontal:
                    lo, hi = min(ax, bx), max(ax, bx)
                    # (1) through the interior
                    if by1 + 1 < ay < by2 - 1 and hi > bx1 + 1 and lo < bx2 - 1:
                        return True
                    # (2) parallel-adjacent to the top/bottom edge (hugging)
                    overlap = min(hi, bx2) - max(lo, bx1)
                    if overlap > c and (
                        by1 - c < ay < by1 + c or by2 - c < ay < by2 + c
                    ):
                        return True
                else:
                    lo, hi = min(ay, by), max(ay, by)
                    if bx1 + 1 < ax < bx2 - 1 and hi > by1 + 1 and lo < by2 - 1:
                        return True
                    overlap = min(hi, by2) - max(lo, by1)
                    if overlap > c and (
                        bx1 - c < ax < bx1 + c or bx2 - c < ax < bx2 + c
                    ):
                        return True
        return False

    # -- visibility graph ---------------------------------------------------
    def _visibility_route(
        self,
        p1: Point,
        p2: Point,
        p1_owner: Rect | None,
        p2_owner: Rect | None,
    ) -> list[Point] | None:
        """Route over an orthogonal visibility graph (Hanan grid).

        Nodes are the intersections of candidate grid lines — the endpoints'
        coordinates plus every non-owner obstacle's edges inflated OUTWARD by
        ``clearance`` — that don't fall inside an inflated obstacle (so ordinary
        routing keeps its distance). Edges connect adjacent collinear nodes whose
        segment doesn't cross an obstacle INTERIOR (real rect). The endpoints are
        always nodes and may escape a clearance band they sit in (a tunnel on a
        structure border), but never cross an interior. Dijkstra minimises length
        plus a per-turn bend penalty.
        """
        c = self._cfg.clearance
        bx1, by1, bx2, by2 = self._bounds

        def is_owner(o: Rect) -> bool:
            return o == p1_owner or o == p2_owner

        interiors = [o for o in self._obstacles if not is_owner(o)]
        inflated = [
            (o[0] - c, o[1] - c, o[2] + c, o[3] + c) for o in interiors
        ]

        # Spatial buckets over the (small) per-route obstacle lists so the
        # point-in-rect and segment-vs-rect tests below scan only nearby rects
        # rather than every obstacle. Results are identical: both tests are an
        # OR over rects, so which rects get *checked* cannot change the answer,
        # and bucketing by full rect extent is a safe superset of any hit.
        cell = 48.0

        def _index(rects: list[Rect]) -> dict[tuple[int, int], list[Rect]]:
            grid: dict[tuple[int, int], list[Rect]] = {}
            for r in rects:
                for cx in range(int(r[0] // cell), int(r[2] // cell) + 1):
                    for cy in range(int(r[1] // cell), int(r[3] // cell) + 1):
                        grid.setdefault((cx, cy), []).append(r)
            return grid

        interior_grid = _index(interiors)
        inflated_grid = _index(inflated)

        def clamp(v: float, lo: float, hi: float) -> float:
            return lo if v < lo else hi if v > hi else v

        xs: set[float] = {p1[0], p2[0]}
        ys: set[float] = {p1[1], p2[1]}
        for (ix1, iy1, ix2, iy2) in inflated:
            for x in (ix1, ix2):
                if bx1 <= x <= bx2:
                    xs.add(x)
            for y in (iy1, iy2):
                if by1 <= y <= by2:
                    ys.add(y)
        xs_l = sorted(xs)
        ys_l = sorted(ys)

        def strictly_inside_inflated(x: float, y: float) -> bool:
            cell_key = (int(x // cell), int(y // cell))
            for (ix1, iy1, ix2, iy2) in inflated_grid.get(cell_key, ()):
                if ix1 < x < ix2 and iy1 < y < iy2:
                    return True
            return False

        # Grid nodes that sit in free space (outside every inflated rect), plus
        # the two endpoints (which may legitimately be inside a clearance band).
        nodes: list[Point] = [
            (x, y)
            for x in xs_l
            for y in ys_l
            if not strictly_inside_inflated(x, y)
        ]
        node_set = set(nodes)
        node_set.add(p1)
        node_set.add(p2)

        def seg_hits_interior(a: Point, b: Point) -> bool:
            (sx, sy), (tx, ty) = a, b
            lo_x, hi_x = min(sx, tx), max(sx, tx)
            lo_y, hi_y = min(sy, ty), max(sy, ty)
            seen: set[Rect] = set()
            for cx in range(int(lo_x // cell), int(hi_x // cell) + 1):
                for cy in range(int(lo_y // cell), int(hi_y // cell) + 1):
                    for obstacle in interior_grid.get((cx, cy), ()):
                        if obstacle in seen:
                            continue
                        seen.add(obstacle)
                        ox1, oy1, ox2, oy2 = obstacle
                        # axis-aligned segment vs rect interior (1px inset so
                        # touching an edge is allowed)
                        if hi_x > ox1 + 1 and lo_x < ox2 - 1 and \
                                hi_y > oy1 + 1 and lo_y < oy2 - 1:
                            return True
            return False

        # Build orthogonal adjacency: each node connects to its immediate
        # neighbour along +x/-x/+y/-y among the grid lines, if the connecting
        # segment is interior-free.
        by_col: dict[float, list[float]] = {}
        by_row: dict[float, list[float]] = {}
        for (x, y) in node_set:
            by_col.setdefault(x, []).append(y)
            by_row.setdefault(y, []).append(x)
        for x in by_col:
            by_col[x].sort()
        for y in by_row:
            by_row[y].sort()

        adj: dict[Point, list[Point]] = {n: [] for n in node_set}

        def link(a: Point, b: Point) -> None:
            if not seg_hits_interior(a, b):
                adj[a].append(b)
                adj[b].append(a)

        for x, col in by_col.items():
            for i in range(len(col) - 1):
                link((x, col[i]), (x, col[i + 1]))
        for y, row in by_row.items():
            for i in range(len(row) - 1):
                link((row[i], y), (row[i + 1], y))

        # An endpoint whose x/y isn't shared by a free node still needs to reach
        # the grid: connect each endpoint to the nearest node on its own row and
        # column (interior-checked), covering the escape from a clearance band.
        for ep in (p1, p2):
            self._link_endpoint(ep, node_set, adj, seg_hits_interior)

        path = self._dijkstra(p1, p2, adj, self._cfg.bend_penalty)
        if path is None:
            return None
        return _compress(path)

    @staticmethod
    def _link_endpoint(
        ep: Point,
        node_set: set[Point],
        adj: dict[Point, list[Point]],
        seg_hits_interior,
    ) -> None:
        ex, ey = ep
        # nearest node with same x (vertical escape) and same y (horizontal),
        # in both directions, reachable without crossing an interior.
        best: dict[str, tuple[float, Point]] = {}
        for n in node_set:
            if n == ep:
                continue
            nx, ny = n
            if nx == ex and ny != ey:
                key = "up" if ny < ey else "down"
                d = abs(ny - ey)
                if key not in best or d < best[key][0]:
                    best[key] = (d, n)
            elif ny == ey and nx != ex:
                key = "left" if nx < ex else "right"
                d = abs(nx - ex)
                if key not in best or d < best[key][0]:
                    best[key] = (d, n)
        for _d, n in best.values():
            if not seg_hits_interior(ep, n):
                adj[ep].append(n)
                adj.setdefault(n, []).append(ep)

    @staticmethod
    def _dijkstra(
        start: Point, goal: Point, adj: dict[Point, list[Point]],
        bend_penalty: float,
    ) -> list[Point] | None:
        """Shortest orthogonal path start→goal with a bend penalty. State carries
        the incoming direction so a turn costs extra, favouring straight runs."""
        bend = bend_penalty

        def direction(a: Point, b: Point) -> int:
            return 0 if a[0] == b[0] else 1  # 0 vertical, 1 horizontal

        # state = (node, dir); dir 2 = none (at start)
        start_state = (start, 2)
        pq: list[tuple[float, int, Point, int]] = [(0.0, 0, start, 2)]
        best: dict[tuple[Point, int], float] = {start_state: 0.0}
        came: dict[tuple[Point, int], tuple[Point, int]] = {}
        counter = 1
        goal_state: tuple[Point, int] | None = None
        while pq:
            cost, _c, node, d = heapq.heappop(pq)
            state = (node, d)
            if cost > best.get(state, float("inf")):
                continue
            if node == goal:
                goal_state = state
                break
            for nb in adj.get(node, ()):
                nd = direction(node, nb)
                step = abs(nb[0] - node[0]) + abs(nb[1] - node[1])
                turn = bend if d != 2 and nd != d else 0.0
                ncost = cost + step + turn
                ns = (nb, nd)
                if ncost < best.get(ns, float("inf")):
                    best[ns] = ncost
                    came[ns] = state
                    heapq.heappush(pq, (ncost, counter, nb, nd))
                    counter += 1
        if goal_state is None:
            return None
        pts: list[Point] = []
        s: tuple[Point, int] | None = goal_state
        while s is not None:
            pts.append(s[0])
            s = came.get(s)
        pts.reverse()
        return pts


def _compress(pts: list[Point], tol: float = 0.75) -> list[Point]:
    """Drop interior points that lie on a straight axis-aligned run, keeping real
    bends.

    Collinearity is measured against the LAST KEPT point (``out[-1]``), not the
    raw previous point — otherwise a sub-pixel step just before a corner (the
    visibility graph emits nodes at obstacle edges ± clearance, which can be a
    fraction of a pixel apart) makes the corner look collinear with the wrong
    axis and gets dropped, collapsing an L into a diagonal.
    """
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        px, py = out[-1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        if (abs(px - bx) <= tol and abs(bx - cx) <= tol) or (
            abs(py - by) <= tol and abs(by - cy) <= tol
        ):
            continue
        out.append(pts[i])
    out.append(pts[-1])
    return out


def path_d(pts: list[Point]) -> str:
    """SVG path 'd' string for an orthogonal polyline."""
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
