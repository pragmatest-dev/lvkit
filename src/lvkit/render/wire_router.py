"""Orthogonal wire router for faithful LabVIEW block-diagram rendering.

LabVIEW does not store readable wire paths (only an opaque ``compressedWireTable``
blob), but it does store every terminal's exact position. This router recovers
LabVIEW-style wires from endpoints alone:

  1. Try clean routes first — straight, single-elbow, or Z — leaving terminals
     the way LabVIEW draws them.
  2. Fall back to obstacle-avoiding A* only when a node actually blocks the route.

Experiment 1 (``experiments/lv-renderer/wire_router_demo.py``) showed this hybrid
beats both a naive midpoint router and pure A* — fewest bends *and* it stops
cutting through unrelated nodes.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x1, y1, x2, y2


@dataclass(frozen=True)
class RouterConfig:
    grid: int = 3
    bend_penalty: int = 4
    # Ignore obstacle hits within this distance of a wire's own endpoints, since
    # terminals sit on the edge of their node.
    endpoint_ignore: float = 10.0
    # Endpoints within this many px on one axis are treated as aligned on that
    # axis — LabVIEW draws a plain straight segment rather than a jog for
    # sub-pixel/near-pixel offsets (heap terminal centers are rarely exactly
    # equal even when the diagram clearly intends a straight wire).
    align_tol: float = 2.0


class WireRouter:
    """Routes orthogonal wires over a fixed set of rectangular node obstacles."""

    def __init__(
        self,
        obstacles: list[Rect],
        bounds: Rect,
        config: RouterConfig | None = None,
    ) -> None:
        self._obstacles = obstacles
        self._cfg = config or RouterConfig()
        self._minx, self._miny, maxx, maxy = bounds
        g = self._cfg.grid
        self._cols = int((maxx - self._minx) // g) + 2
        self._rows = int((maxy - self._miny) // g) + 2
        self._blocked: set[tuple[int, int]] = set()  # built lazily per route set

    # -- public API ---------------------------------------------------------
    def route(self, p1: Point, p2: Point, endpoints: list[Point]) -> list[Point]:
        """Return the polyline (incl. endpoints) connecting p1 → p2.

        ``endpoints`` is every terminal center in the diagram; the grid keeps a
        free channel around each so terminals on node edges stay reachable.
        """
        if not self._blocked:
            self._build_grid(endpoints)
        for cand in self._clean_candidates(p1, p2):
            if not self._crosses(cand):
                return cand
        return self._astar(p1, p2)

    # -- clean routes -------------------------------------------------------
    def _clean_candidates(self, p1: Point, p2: Point) -> list[list[Point]]:
        """Candidate routes, tried in order of ascending bend count.

        Straight (0 bends) beats a single elbow/L (1 bend) beats a Z
        (2 bends) — the first one that doesn't cross an obstacle wins, so a Z
        is only ever used when both L orientations are blocked. Endpoints
        within ``align_tol`` on an axis are treated as aligned, so a wire
        LabVIEW clearly means to be straight isn't drawn with a jog just
        because heap-derived centers differ by a sub-pixel amount.

        A "feedback" route (``p2`` to the left of ``p1`` — e.g. a
        shift-register value wiring back to a terminal on its own left) is
        handled separately: an elbow anchored at the exit stub's own x
        clears the source node vertically before heading back left, instead
        of doubling back through the row the source sits on.
        """
        (x1, y1), (x2, y2) = p1, p2
        tol = self._cfg.align_tol
        cands: list[list[Point]] = []
        if abs(y1 - y2) <= tol or abs(x1 - x2) <= tol:
            cands.append([p1, p2])                      # straight (0 bends)
        if x2 < x1 - tol:
            # Feedback wire: go vertical at the source's own exit x (already
            # clear of the source node via the stub), then straight back
            # into the destination — never re-crosses the source's row.
            cands.append([p1, (x1, y2), p2])
            cands.append([p1, (x2, y1), p2])
        else:
            cands.append([p1, (x2, y1), p2])            # horizontal-first L
            cands.append([p1, (x1, y2), p2])            # vertical-first L
        # LabVIEW drops the vertical leg right after the source's exit stub,
        # not at the horizontal midpoint — a short stub out, then straight in.
        jog = 9.0
        if x2 >= x1:
            jx = min(x1 + jog, (x1 + x2) / 2)
        else:
            jx = max(x1 - jog, (x1 + x2) / 2)
        cands.append([p1, (jx, y1), (jx, y2), p2])       # Z (2 bends)
        return cands

    def _crosses(self, pts: list[Point]) -> bool:
        """True if the polyline passes through an unrelated node interior."""
        a, b = pts[0], pts[-1]
        near = self._cfg.endpoint_ignore
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            steps = int(max(abs(x2 - x1), abs(y2 - y1)) / 2) + 1
            for s in range(1, steps):
                x = x1 + (x2 - x1) * s / steps
                y = y1 + (y2 - y1) * s / steps
                if (abs(x - a[0]) + abs(y - a[1]) < near
                        or abs(x - b[0]) + abs(y - b[1]) < near):
                    continue
                for (bx1, by1, bx2, by2) in self._obstacles:
                    if bx1 + 1 < x < bx2 - 1 and by1 + 1 < y < by2 - 1:
                        return True
        return False

    # -- grid + A* ----------------------------------------------------------
    def _to_grid(self, p: Point) -> tuple[int, int]:
        g = self._cfg.grid
        return (round((p[0] - self._minx) / g), round((p[1] - self._miny) / g))

    def _to_world(self, c: tuple[int, int]) -> Point:
        g = self._cfg.grid
        return (self._minx + c[0] * g, self._miny + c[1] * g)

    def _build_grid(self, endpoints: list[Point]) -> None:
        g = self._cfg.grid
        free: set[tuple[int, int]] = set()
        for p in endpoints:
            gx, gy = self._to_grid(p)
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    free.add((gx + dx, gy + dy))
        blocked: set[tuple[int, int]] = set()
        for (bx1, by1, bx2, by2) in self._obstacles:
            gx1 = int((bx1 - self._minx) // g) + 1
            gx2 = int((bx2 - self._minx) // g) - 1
            gy1 = int((by1 - self._miny) // g) + 1
            gy2 = int((by2 - self._miny) // g) - 1
            for gx in range(gx1, gx2 + 1):
                for gy in range(gy1, gy2 + 1):
                    if (gx, gy) not in free:
                        blocked.add((gx, gy))
        self._blocked = blocked or {(-999, -999)}  # sentinel so we don't rebuild

    def _astar(self, p1: Point, p2: Point) -> list[Point]:
        start, goal = self._to_grid(p1), self._to_grid(p2)
        free = {start, goal}
        for gpt in (start, goal):
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    free.add((gpt[0] + dx, gpt[1] + dy))

        def blocked(c: tuple[int, int]) -> bool:
            if c in free:
                return False
            if not (0 <= c[0] <= self._cols and 0 <= c[1] <= self._rows):
                return True
            return c in self._blocked

        bend = self._cfg.bend_penalty
        # state = (x, y, dir): dir 0=none, 1=horizontal, 2=vertical
        start_state = (start[0], start[1], 0)
        pq: list[tuple[int, int, tuple[int, int, int]]] = [(0, 0, start_state)]
        best: dict[tuple[int, int, int], int] = {start_state: 0}
        came: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        goal_state: tuple[int, int, int] | None = None
        while pq:
            _f, gcost, (x, y, d) = heapq.heappop(pq)
            if (x, y) == goal:
                goal_state = (x, y, d)
                break
            if gcost > best.get((x, y, d), 1 << 30):
                continue
            for dx, dy, nd in ((1, 0, 1), (-1, 0, 1), (0, 1, 2), (0, -1, 2)):
                nx, ny = x + dx, y + dy
                if blocked((nx, ny)):
                    continue
                ng = gcost + 1 + (bend if d and nd != d else 0)
                ns = (nx, ny, nd)
                if ng < best.get(ns, 1 << 30):
                    best[ns] = ng
                    came[ns] = (x, y, d)
                    h = abs(nx - goal[0]) + abs(ny - goal[1])
                    heapq.heappush(pq, (ng + h, ng, ns))
        if goal_state is None:
            # No route found — degrade to a simple midpoint elbow.
            mx = (p1[0] + p2[0]) / 2
            return [p1, (mx, p1[1]), (mx, p2[1]), p2]
        cells: list[tuple[int, int]] = []
        s: tuple[int, int, int] | None = goal_state
        while s in came:
            cells.append((s[0], s[1]))
            s = came[s]
        cells.append((start[0], start[1]))
        cells.reverse()
        pts = [self._to_world(c) for c in cells]
        pts[0], pts[-1] = p1, p2
        return _compress(pts)


def _compress(pts: list[Point], tol: float = 0.75) -> list[Point]:
    """Drop collinear (within ``tol`` px) interior points, keeping only real
    bend vertices.

    ``tol`` absorbs the sub-pixel jogs that appear when a stub point and a
    real terminal center are meant to read as one straight run but differ by
    a fraction of a pixel (heap-derived centers are rarely exactly equal even
    when the diagram clearly intends a straight wire) — real bends in this
    router are always many pixels apart, so this never merges an intentional
    turn.
    """
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i - 1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        if (abs(ax - bx) <= tol and abs(bx - cx) <= tol) or (
            abs(ay - by) <= tol and abs(by - cy) <= tol
        ):
            continue
        out.append(pts[i])
    out.append(pts[-1])
    return out


def path_d(pts: list[Point]) -> str:
    """SVG path 'd' string for an orthogonal polyline."""
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
