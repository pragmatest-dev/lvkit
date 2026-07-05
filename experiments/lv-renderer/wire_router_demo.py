"""Part-1 experiment: how close can an orthogonal auto-router get to LabVIEW?

Reuses the POC scene extraction, then routes every wire two ways:
  - naive:    midpoint elbow (the original POC router)
  - improved: direction-aware A* on a grid, avoiding node obstacles and
              minimizing bends, with horizontal stubs at terminals (LabVIEW
              wires enter/leave terminals horizontally).

Emits two SVGs into this folder and prints bend/crossing stats so we can
judge fidelity. No LabVIEW required.
"""
from __future__ import annotations

import heapq
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.extractor import extract_vi_xml

VI = Path(".tmp/array average 1.vi")
HERE = Path("experiments/lv-renderer")
PRIMS = json.load(open("src/lvkit/data/primitives.json"))["primitives"]
PRIM_SYMBOL = {"Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷"}


def prim_name(rid: str) -> str:
    e = PRIMS.get(str(rid))
    return e["name"] if e else f"prim {rid}"


def bounds(elem):
    b = elem.find("bounds")
    if b is None or not b.text:
        return None
    t, l, btm, r = (int(x) for x in b.text.strip("()").split(","))
    return l, t, r, btm  # x1,y1,x2,y2


def parse_tb(text):
    t, l, b, r = (int(x) for x in text.strip("()").split(","))
    return l, t, r, b


# ---- scene extraction (condensed from poc_render_svg.py) --------------------
structures, nodes, consts, fpterms, wires = [], [], [], [], []
term_center: dict[str, tuple[float, float]] = {}
node_boxes: list[tuple[int, int, int, int]] = []  # obstacles


def process_terms(elem, ox, oy):
    tl = elem.find("termList")
    if tl is None:
        return
    for term in tl.findall("SL__arrayElement"):
        if term.get("class") != "term":
            continue
        tb = term.find(".//termBounds")
        if tb is None or not tb.text:
            continue
        l, t, r, b = parse_tb(tb.text)
        x1, y1, x2, y2 = ox + l, oy + t, ox + r, oy + b
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ddo = term.find(".//ddo")
        if ddo is not None:
            cb = bounds(ddo)
            if cb:
                consts.append(cb)
                cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
        uids = {term.get("uid")}
        dco = term.find("dco")
        if dco is not None:
            uids.add(dco.get("uid"))
        for sub in term.findall(".//termList/SL__arrayElement"):
            if sub.get("uid"):
                uids.add(sub.get("uid"))
        for u in uids:
            if u:
                term_center[u] = (cx, cy)


def collect_uids(elem):
    uids = {elem.get("uid")}
    dco = elem.find("dco")
    if dco is not None:
        uids.add(dco.get("uid"))
    for s in elem.findall(".//termList/SL__arrayElement"):
        uids.add(s.get("uid"))
    return {u for u in uids if u}


def global_terms(root):
    for e in root.iter("SL__arrayElement"):
        cls = e.get("class")
        if cls == "fPTerm" and e.find("bounds") is not None:
            b = bounds(e)
            fpterms.append(b)
            c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            for u in collect_uids(e):
                term_center[u] = c
        elif cls == "term":
            ddo = e.find(".//ddo")
            if ddo is not None and ddo.find("bounds") is not None:
                b = bounds(ddo)
                consts.append(b)
                c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                for u in collect_uids(e):
                    term_center[u] = c


def walk(diag, ox, oy):
    sl = diag.find("signalList")
    if sl is not None:
        for sig in sl.findall("SL__arrayElement"):
            if sig.get("class") != "signal":
                continue
            tl = sig.find("termList")
            refs = [s.get("uid") for s in tl.findall("SL__arrayElement")] if tl is not None else []
            wires.append([u for u in refs if u])
    zp = diag.find("zPlaneList")
    if zp is None:
        return
    for elem in zp.findall("SL__arrayElement"):
        cls = elem.get("class")
        bb = bounds(elem)
        if bb is None:
            continue
        x1, y1, x2, y2 = bb
        ax1, ay1, ax2, ay2 = ox + x1, oy + y1, ox + x2, oy + y2
        process_terms(elem, ax1, ay1)
        dlist = elem.find("diagramList")
        inner = ([d for d in dlist.findall("SL__arrayElement") if d.get("class") == "diag"]
                 if dlist is not None else [])
        if inner:
            structures.append((ax1, ay1, ax2, ay2, "For Loop" if cls == "forLoop" else cls))
            for d in inner:
                walk(d, ax1, ay1)
        elif cls == "prim":
            nodes.append((ax1, ay1, ax2, ay2, prim_name(elem.findtext("primResID"))))
            node_boxes.append((ax1, ay1, ax2, ay2))
        elif cls in ("fPTerm", "parm", "overridableParm"):
            fpterms.append((ax1, ay1, ax2, ay2))
            node_boxes.append((ax1, ay1, ax2, ay2))


bd, _, _ = extract_vi_xml(VI)
root = ET.parse(bd).getroot().find("root")
walk(root, 0, 0)
global_terms(root)

xs = [v for s in structures for v in (s[0], s[2])] + [v for n in nodes for v in (n[0], n[2])] \
     + [v for c in consts for v in (c[0], c[2])] + [v for f in fpterms for v in (f[0], f[2])]
ys = [v for s in structures for v in (s[1], s[3])] + [v for n in nodes for v in (n[1], n[3])] \
     + [v for c in consts for v in (c[1], c[3])] + [v for f in fpterms for v in (f[1], f[3])]
pad = 30
minx, maxx = min(xs) - pad, max(xs) + pad
miny, maxy = min(ys) - pad, max(ys) + pad
W, H = maxx - minx, maxy - miny
DBL = "#e8821e"


# ---- routers ---------------------------------------------------------------
def route_naive(p1, p2):
    (x1, y1), (x2, y2) = p1, p2
    mx = (x1 + x2) / 2
    return [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]


GRID = 3
STUB = 6  # horizontal stub length leaving a terminal
COLS = int(W // GRID) + 2
ROWS = int(H // GRID) + 2


def to_grid(p):
    return (int(round((p[0] - minx) / GRID)), int(round((p[1] - miny) / GRID)))


def to_world(g):
    return (minx + g[0] * GRID, miny + g[1] * GRID)


def all_endpoints():
    eps = []
    for refs in wires:
        for u in refs:
            if u in term_center:
                eps.append(term_center[u])
    return eps


def build_obstacles(endpoints):
    """Block node interiors, but always leave a free channel around every
    terminal so wires can connect (terminals sit on node edges)."""
    free_zones = {to_grid(p) for p in endpoints}
    for p in endpoints:  # 2-cell radius around each endpoint stays free
        g = to_grid(p)
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                free_zones.add((g[0] + dx, g[1] + dy))
    blocked = set()
    for (bx1, by1, bx2, by2) in node_boxes:
        # erode by 1 grid cell so a free ring hugs every node
        gx1 = int((bx1 - minx) // GRID) + 1
        gx2 = int((bx2 - minx) // GRID) - 1
        gy1 = int((by1 - miny) // GRID) + 1
        gy2 = int((by2 - miny) // GRID) - 1
        for gx in range(gx1, gx2 + 1):
            for gy in range(gy1, gy2 + 1):
                if (gx, gy) not in free_zones:
                    blocked.add((gx, gy))
    return blocked


OBST = build_obstacles(all_endpoints())
_fallbacks = 0


def route_astar(p1, p2):
    """Direction-aware A*: unit step cost + bend penalty, avoid obstacles."""
    start, goal = to_grid(p1), to_grid(p2)
    # free the endpoints and their neighborhood so terminals on node edges work
    free = {start, goal}
    for g in (start, goal):
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                free.add((g[0] + dx, g[1] + dy))

    def blocked(c):
        if c in free:
            return False
        if not (0 <= c[0] <= COLS and 0 <= c[1] <= ROWS):
            return True
        return c in OBST

    BEND = 4  # cost of a 90-degree turn (favor few bends, like LabVIEW)
    # state: (x, y, dir) dir in {0:none,1:H,2:V}; pq holds (f, g, state)
    startstate = (start[0], start[1], 0)
    pq = [(0, 0, startstate)]
    best = {startstate: 0}
    came = {}
    goalstate = None
    while pq:
        _f, g, (x, y, d) = heapq.heappop(pq)
        if (x, y) == goal:
            goalstate = (x, y, d)
            break
        if g > best.get((x, y, d), 1e9):
            continue
        for dx, dy, nd in ((1, 0, 1), (-1, 0, 1), (0, 1, 2), (0, -1, 2)):
            nx, ny = x + dx, y + dy
            if blocked((nx, ny)):
                continue
            ng = g + 1 + (BEND if d and nd != d else 0)
            ns = (nx, ny, nd)
            if ng < best.get(ns, 1e9):
                best[ns] = ng
                came[ns] = (x, y, d)
                h = abs(nx - goal[0]) + abs(ny - goal[1])
                heapq.heappush(pq, (ng + h, ng, ns))
    if goalstate is None:
        global _fallbacks
        _fallbacks += 1
        return route_naive(p1, p2)  # fallback
    # reconstruct, then compress collinear runs into bend points
    path = []
    s = goalstate
    while s in came:
        path.append((s[0], s[1]))
        s = came[s]
    path.append((start[0], start[1]))
    path.reverse()
    pts = [to_world(g) for g in path]
    pts[0], pts[-1] = p1, p2  # exact terminal coords
    return compress(pts)


def _crosses_any(pts):
    """True if the polyline passes through an unrelated node's interior."""
    return crossings(pts, deep=True) > 0


def route_hybrid(p1, p2):
    """LabVIEW-style: prefer clean straight/elbow/Z routes leaving terminals
    horizontally; fall back to obstacle-avoiding A* only when blocked."""
    (x1, y1), (x2, y2) = p1, p2
    mx = (x1 + x2) / 2
    candidates = []
    if y1 == y2 or x1 == x2:
        candidates.append([p1, p2])                       # straight
    candidates.append([p1, (x2, y1), p2])                 # horizontal-first L
    candidates.append([p1, (mx, y1), (mx, y2), p2])       # Z (horizontal legs)
    candidates.append([p1, (x1, y2), p2])                 # vertical-first L
    for c in candidates:
        if not _crosses_any(c):
            return c
    return route_astar(p1, p2)


def compress(pts):
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i - 1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        # keep only direction-change points
        if (ax == bx == cx) or (ay == by == cy):
            continue
        out.append(pts[i])
    out.append(pts[-1])
    return out


def with_stubs(p1, p2, router):
    """Add short horizontal stubs so wires leave/enter terminals horizontally."""
    a = (p1[0] + STUB, p1[1])
    b = (p2[0] - STUB, p2[1])
    mid = router(a, b)
    return [p1] + mid + [p2]


def path_d(pts):
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def count_bends(pts):
    n = 0
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i - 1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        if not ((ax == bx == cx) or (ay == by == cy)):
            n += 1
    return n


def crossings(pts, deep=False):
    """Count sample points inside a node box. With deep=True, ignore points
    near the wire's own endpoints (terminals sit on nodes) so we measure only
    real 'routed through an unrelated node' failures."""
    hits = 0
    a, b = pts[0], pts[-1]
    NEAR = 10
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        steps = int(max(abs(x2 - x1), abs(y2 - y1)) / 2) + 1
        for s in range(1, steps):
            x = x1 + (x2 - x1) * s / steps
            y = y1 + (y2 - y1) * s / steps
            if deep and (abs(x - a[0]) + abs(y - a[1]) < NEAR
                         or abs(x - b[0]) + abs(y - b[1]) < NEAR):
                continue
            for (bx1, by1, bx2, by2) in node_boxes:
                if bx1 + 1 < x < bx2 - 1 and by1 + 1 < y < by2 - 1:
                    hits += 1
                    break
    return hits


# ---- render ----------------------------------------------------------------
def draw(router, stub):
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx} {miny} {W} {H}" '
           f'font-family="sans-serif">']
    svg.append(f'<rect x="{minx}" y="{miny}" width="{W}" height="{H}" fill="#fbfbf5"/>')
    for x1, y1, x2, y2, _ in structures:
        svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" '
                   f'fill="none" stroke="#6c6c8a" stroke-width="2"/>')
    total_bends = 0
    total_cross = 0
    for refs in wires:
        pts_ep = [term_center[u] for u in refs if u in term_center]
        for i in range(len(pts_ep) - 1):
            if stub:
                route = with_stubs(pts_ep[i], pts_ep[i + 1], router)
            else:
                route = router(pts_ep[i], pts_ep[i + 1])
            total_bends += count_bends(route)
            total_cross += crossings(route, deep=True)
            svg.append(f'<path d="{path_d(route)}" fill="none" stroke="{DBL}" '
                       f'stroke-width="2.5" stroke-linejoin="round"/>')
    for x1, y1, x2, y2 in fpterms:
        svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="2" '
                   f'fill="#fff3e2" stroke="{DBL}" stroke-width="3"/>')
    for x1, y1, x2, y2 in consts:
        svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="2" '
                   f'fill="#fff3e2" stroke="{DBL}" stroke-width="2"/>')
        svg.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2+4}" font-size="10" '
                   f'text-anchor="middle">0</text>')
    for x1, y1, x2, y2, name in nodes:
        sym = PRIM_SYMBOL.get(name, "")
        if sym:
            svg.append(f'<polygon points="{x1},{y1} {x2},{(y1+y2)/2} {x1},{y2}" '
                       f'fill="#fff6d8" stroke="#b07d10" stroke-width="1.5"/>')
            svg.append(f'<text x="{x1+(x2-x1)*0.32}" y="{(y1+y2)/2+5}" font-size="15" '
                       f'text-anchor="middle">{sym}</text>')
        else:
            svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" '
                       f'fill="#fff6d8" stroke="#b07d10"/>')
            svg.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2+4}" font-size="9" '
                       f'text-anchor="middle">{name}</text>')
    svg.append("</svg>")
    return "\n".join(svg), total_bends, total_cross


naive_svg, naive_bends, naive_cross = draw(route_naive, stub=False)
astar_svg, astar_bends, astar_cross = draw(route_astar, stub=False)
hybrid_svg, hy_bends, hy_cross = draw(route_hybrid, stub=False)
(HERE / "router_naive.svg").write_text(naive_svg)
(HERE / "router_astar.svg").write_text(astar_svg)
(HERE / "router_hybrid.svg").write_text(hybrid_svg)
n_wires = sum(max(0, len([u for u in refs if u in term_center]) - 1) for refs in wires)
print(f"wires drawn:             {n_wires}")
print(f"naive   bends/crossings: {naive_bends} / {naive_cross}")
print(f"astar   bends/crossings: {astar_bends} / {astar_cross}  (fallbacks: {_fallbacks})")
print(f"hybrid  bends/crossings: {hy_bends} / {hy_cross}")
print(f"obstacle cells: {len(OBST)}  grid: {COLS}x{ROWS}")
print("wrote router_naive.svg + router_astar.svg + router_hybrid.svg")
