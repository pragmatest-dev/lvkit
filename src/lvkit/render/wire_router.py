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
from collections.abc import Iterator
from dataclasses import dataclass, replace

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x1, y1, x2, y2


@dataclass(frozen=True)
class RouterConfig:
    grid: int = 3
    bend_penalty: int = 4
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
        self._bounds = bounds
        self._cfg = config or RouterConfig()
        self._minx, self._miny, maxx, maxy = bounds
        g = self._cfg.grid
        self._cols = int((maxx - self._minx) // g) + 2
        self._rows = int((maxy - self._miny) // g) + 2
        self._blocked: set[tuple[int, int]] = set()  # built lazily per route set

    # -- public API ---------------------------------------------------------
    def route(
        self,
        p1: Point,
        p2: Point,
        endpoints: list[Point],
        p1_owner: Rect | None = None,
        p2_owner: Rect | None = None,
    ) -> list[Point]:
        """Return the polyline (incl. endpoints) connecting p1 → p2.

        ``endpoints`` is every terminal center in the diagram; the grid keeps a
        free channel around each so terminals on node edges stay reachable.

        ``p1_owner``/``p2_owner`` are the bounds of the node each endpoint's
        own terminal sits on (if any) — a wire is allowed to pass through
        exactly that rect near its own endpoint (a terminal legitimately sits
        on its node's edge), but no OTHER obstacle, including one that
        happens to be near an endpoint.
        """
        if not self._blocked:
            self._build_grid(endpoints)
        for cand in self._clean_candidates(p1, p2):
            if not self._crosses(cand, p1_owner, p2_owner):
                return cand
        return self._astar(p1, p2, endpoints, p1_owner, p2_owner)

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

    def _crosses(
        self,
        pts: list[Point],
        p1_owner: Rect | None = None,
        p2_owner: Rect | None = None,
    ) -> bool:
        """True if the polyline passes through a NON-OWNER node interior.

        A wire's own endpoint terminal legitimately sits on (or just outside)
        its own node's edge, so the specific obstacle rect that owns p1/p2
        (``p1_owner``/``p2_owner``) is exempt from the interior test — but
        every OTHER obstacle counts, even one that happens to be close to an
        endpoint (a neighbor node must never be clipped just because it sits
        near the wire's start/end).
        """
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            steps = int(max(abs(x2 - x1), abs(y2 - y1)) / 2) + 1
            for s in range(1, steps):
                x = x1 + (x2 - x1) * s / steps
                y = y1 + (y2 - y1) * s / steps
                for obstacle in self._obstacles:
                    if obstacle == p1_owner or obstacle == p2_owner:
                        continue
                    bx1, by1, bx2, by2 = obstacle
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
        """Mark every grid cell inside an obstacle (minus a 1px margin) as
        blocked.

        ``endpoints`` (every terminal center in the diagram) is accepted for
        API-compatibility with callers but deliberately UNUSED here: an
        earlier version carved a free zone around every terminal in the
        WHOLE diagram, which punched a hole clean through any small
        obstacle that happened to have some unrelated terminal near it —
        exactly the "wire cuts through a neighbor node" bug. The free zone
        a route's own endpoints need to escape their own node is instead
        carved locally, per search, in ``_astar_grid`` (scoped to just that
        route's start/goal), which can't leak into a neighboring obstacle.
        """
        del endpoints
        g = self._cfg.grid
        blocked: set[tuple[int, int]] = set()
        for (bx1, by1, bx2, by2) in self._obstacles:
            gx1 = int((bx1 - self._minx) // g) + 1
            gx2 = int((bx2 - self._minx) // g) - 1
            gy1 = int((by1 - self._miny) // g) + 1
            gy2 = int((by2 - self._miny) // g) - 1
            for gx in range(gx1, gx2 + 1):
                for gy in range(gy1, gy2 + 1):
                    blocked.add((gx, gy))
        self._blocked = blocked or {(-999, -999)}  # sentinel so we don't rebuild

    def _astar(
        self,
        p1: Point,
        p2: Point,
        endpoints: list[Point],
        p1_owner: Rect | None = None,
        p2_owner: Rect | None = None,
    ) -> list[Point]:
        """Grid-search a route, retrying at progressively finer grids before
        giving up. A* on the configured grid can fail to find a path in dense
        diagrams even though a route exists at finer resolution (the coarse
        grid marks a whole cell blocked if any part of an obstacle overlaps
        it). Only if even the finest grid finds nothing do we fall back to a
        geometric detour — never a straight cut through an obstacle.

        Every candidate grid path is re-checked with the same CONTINUOUS
        ``_crosses`` test used for clean candidates before being accepted: a
        coarse grid's blocked cells carry a whole-grid-unit margin around an
        obstacle (so a route can hug its edge), which can let a path graze
        along an obstacle just inside that margin — a real crossing by the
        continuous (pixel-accurate) test even though no grid CELL was
        technically blocked. Rejecting those forces a retry at a finer grid,
        where the margin shrinks.
        """
        path = self._astar_grid(p1, p2, p1_owner, p2_owner)
        if path is not None and not self._crosses(path, p1_owner, p2_owner):
            return path
        for sub in self._finer_routers(p1, p2, endpoints):
            path = sub._astar_grid(p1, p2, p1_owner, p2_owner)
            if path is not None and not self._crosses(path, p1_owner, p2_owner):
                return path
        return self._detour(p1, p2, p1_owner, p2_owner)

    def _finer_routers(
        self, p1: Point, p2: Point, endpoints: list[Point],
    ) -> Iterator[WireRouter]:
        """Progressively finer-grid sub-routers.

        grid=2 searches the FULL diagram (still cheap — only ~1.5x the
        default grid=3 cell count) so it can find a route requiring a
        detour anywhere in the diagram, not just near p1/p2. grid=1 is
        windowed to a LOCAL area around p1/p2 — a full-diagram search at
        1px resolution can be millions of cells, far too slow per wire —
        which is fine because by this point only dense LOCAL clutter (e.g.
        a small obstacle nested inside a large owner) is left to solve; a
        route that would need a diagram-spanning detour at 1px precision
        is not a realistic case.
        """
        if 2 < self._cfg.grid:
            full = WireRouter(self._obstacles, self._bounds, replace(self._cfg, grid=2))
            full._build_grid(endpoints)
            yield full
        if 1 < self._cfg.grid:
            margin = 150.0
            bminx, bminy, bmaxx, bmaxy = self._bounds
            # Clamp the local 1px window to this router's OWN bounds so a
            # CONFINED router (bounds = a container's interior) can't escape
            # its frame through the finer-grid fallback.
            x1 = max(min(p1[0], p2[0]) - margin, bminx)
            y1 = max(min(p1[1], p2[1]) - margin, bminy)
            x2 = min(max(p1[0], p2[0]) + margin, bmaxx)
            y2 = min(max(p1[1], p2[1]) + margin, bmaxy)
            window: Rect = (x1, y1, x2, y2)
            local_obstacles = [
                o for o in self._obstacles
                if o[0] < x2 and o[2] > x1 and o[1] < y2 and o[3] > y1
            ]
            sub = WireRouter(local_obstacles, window, replace(self._cfg, grid=1))
            sub._build_grid(endpoints)
            yield sub

    def _detour(
        self,
        p1: Point,
        p2: Point,
        p1_owner: Rect | None,
        p2_owner: Rect | None,
    ) -> list[Point]:
        """Last-resort route when even the finest grid finds no path: go
        around every non-owner obstacle that overlaps the x-span between the
        endpoints, via a horizontal leg above (or below, whichever is
        shorter) all of them — geometrically guaranteed not to cross any
        obstacle's interior, unlike the old naive midpoint elbow."""
        (x1, y1), (x2, y2) = p1, p2
        lo_x, hi_x = min(x1, x2), max(x1, x2)
        blockers = [
            o for o in self._obstacles
            if o != p1_owner and o != p2_owner and o[0] < hi_x and o[2] > lo_x
        ]
        if not blockers:
            mx = (x1 + x2) / 2
            return [p1, (mx, y1), (mx, y2), p2]
        margin = 20.0
        # Keep the detour leg inside this router's bounds — a CONFINED router
        # must not send its last-resort route out of the container frame.
        _, bminy, _, bmaxy = self._bounds
        top = max(min(o[1] for o in blockers) - margin, bminy)
        bottom = min(max(o[3] for o in blockers) + margin, bmaxy)
        cost_top = abs(y1 - top) + abs(y2 - top)
        cost_bottom = abs(y1 - bottom) + abs(y2 - bottom)
        y = top if cost_top <= cost_bottom else bottom
        return [p1, (x1, y), (x2, y), p2]

    def _owner_free_cells(self, owner: Rect | None) -> set[tuple[int, int]]:
        """Grid cells inside ``owner`` that are free FOR THIS ROUTE ONLY —
        the wire's own endpoint may legitimately travel through its own
        node's interior to reach the node's edge (matching ``_crosses``'
        owner exemption), but a genuinely separate obstacle nested inside
        that same footprint (e.g. an array/cluster constant's own element
        constant, drawn inside the parent's border) must stay blocked."""
        if owner is None:
            return set()
        g = self._cfg.grid
        ox1, oy1, ox2, oy2 = owner
        gx1 = int((ox1 - self._minx) // g) + 1
        gx2 = int((ox2 - self._minx) // g) - 1
        gy1 = int((oy1 - self._miny) // g) + 1
        gy2 = int((oy2 - self._miny) // g) - 1
        cells = {(gx, gy) for gx in range(gx1, gx2 + 1) for gy in range(gy1, gy2 + 1)}
        for other in self._obstacles:
            if other == owner:
                continue
            bx1, by1, bx2, by2 = other
            ngx1 = int((bx1 - self._minx) // g) + 1
            ngx2 = int((bx2 - self._minx) // g) - 1
            ngy1 = int((by1 - self._miny) // g) + 1
            ngy2 = int((by2 - self._miny) // g) - 1
            for gx in range(ngx1, ngx2 + 1):
                for gy in range(ngy1, ngy2 + 1):
                    cells.discard((gx, gy))
        return cells

    def _astar_grid(
        self,
        p1: Point,
        p2: Point,
        p1_owner: Rect | None = None,
        p2_owner: Rect | None = None,
    ) -> list[Point] | None:
        """One A* search at this router's own configured grid. Returns None
        (never a through-obstacle fallback) if no path exists."""
        start, goal = self._to_grid(p1), self._to_grid(p2)
        free = {start, goal}
        for gpt in (start, goal):
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    free.add((gpt[0] + dx, gpt[1] + dy))
        free |= self._owner_free_cells(p1_owner)
        free |= self._owner_free_cells(p2_owner)

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
            return None
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
