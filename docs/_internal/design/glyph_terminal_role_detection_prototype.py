"""Reference PROTOTYPE for glyph terminal-role detection — see the companion
`glyph-terminal-role-detection.md`. NOT a shipped module; kept so the validated
method isn't lost. Promotion work (directional wire-tracing to the label, role
name binding, static-terminal sets, tests) is listed in the design note.

Extracts terminal ROLE-position -> parmIndex from an NI doc connector-pane image
by finding where each wire TOUCHES the icon (its drop-in point) and mapping that
to the primitive's termBounds cell that contains it.

Insight (maintainer): the doc icon shares the real primitive icon's bounds, so a
wire's endpoint ON the icon == that terminal's termBounds position; the VI bound
space maps 1:1 to the image once the icon offset is found. Wire routing is
cosmetic (1540's delimiter routes over the top and drops into the top-center =
parmIndex 1, the middle full-height column). Dashed wires are handled by looking
only at the perimeter drop-in, never tracing the dashes.

Validated 2026-08-19 on 1540 (Array To Spreadsheet String, 31x31) and Format
Into String (expanding + error cluster, 31x51). Only dependency is PIL.
"""
from collections import deque

from PIL import Image


def _wire(p):
    r, g, b = p
    # magenta wire (string): green suppressed
    if g < r - 25 and g < b - 25 and (r > 70 or b > 70):
        return "magenta"
    # yellow/olive wire (error cluster, incl. the dark 127,127,0 shade): r~=g,
    # b low relative to them. Distinct from pale-yellow icon FILL (b~192).
    if r > 90 and g > 90 and b < min(r, g) * 0.55 and abs(r - g) < 45:
        return "yellow"
    # orange wire (numeric/array): warm, r>g>b, not the icon fill
    if g < r - 14 and b < g - 28 and r > 150:
        return "orange"
    return None


def _ink(p):
    return not (p[0] > 230 and p[1] > 230 and p[2] > 230)


def icon_bbox(size, px):
    """Icon = largest non-wire-ink CONNECTED component not touching the image
    frame (border+lines+fill form one blob; labels are separate letters; the
    outer frame hugs the edges)."""
    W, H = size

    # wire mask, then EXTEND it to black pixels vertically sandwiched by wire
    # (the black core of an error-cluster wire, which would otherwise bridge the
    # icon to the error terminals as "ink").
    wm = [[_wire(px[x, y]) is not None for x in range(W)] for y in range(H)]
    for y in range(H):
        for x in range(W):
            p = px[x, y]
            if not wm[y][x] and p[0] < 70 and p[1] < 70 and p[2] < 70:
                up = any(y - k >= 0 and wm[y - k][x] for k in (1, 2, 3))
                dn = any(y + k < H and wm[y + k][x] for k in (1, 2, 3))
                if up and dn:
                    wm[y][x] = True

    def body(x, y):
        return _ink(px[x, y]) and not wm[y][x]

    seen = [[False] * W for _ in range(H)]
    best, best_n = None, 0
    for sy in range(H):
        for sx in range(W):
            if seen[sy][sx] or not body(sx, sy):
                continue
            q = deque([(sx, sy)])
            seen[sy][sx] = True
            minx = maxx = sx
            miny = maxy = sy
            n = 0
            touches = False
            while q:
                x, y = q.popleft()
                n += 1
                if x in (0, W - 1) or y in (0, H - 1):
                    touches = True
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        inside = 0 <= nx < W and 0 <= ny < H
                        if inside and not seen[ny][nx] and body(nx, ny):
                            seen[ny][nx] = True
                            q.append((nx, ny))
            if not touches and n > best_n:
                best_n, best = n, (minx, miny, maxx, maxy)
    return best


def attach_points(size, px, bbox, reach=3):
    """Wire pixels within `reach` px just OUTSIDE each icon edge = drop-in points.
    Cluster contiguous ones per edge; return (edge, along-frac, color)."""
    W, H = size
    x0, y0, x1, y1 = bbox
    hits = {"top": [], "bot": [], "left": [], "right": []}
    for x in range(max(0, x0 - reach), min(W, x1 + reach + 1)):
        for y in range(max(0, y0 - reach), min(H, y1 + reach + 1)):
            c = _wire(px[x, y])
            if not c:
                continue
            if y0 - reach <= y < y0 and x0 <= x <= x1:
                hits["top"].append((x, c))
            elif y1 < y <= y1 + reach and x0 <= x <= x1:
                hits["bot"].append((x, c))
            elif x0 - reach <= x < x0 and y0 <= y <= y1:
                hits["left"].append((y, c))
            elif x1 < x <= x1 + reach and y0 <= y <= y1:
                hits["right"].append((y, c))
    iw, ih = (x1 - x0) or 1, (y1 - y0) or 1
    out = []
    for edge, lst in hits.items():
        if not lst:
            continue
        lst.sort()
        # cluster contiguous coords (gap > 3 splits)
        cluster = [lst[0]]
        groups = [cluster]
        for coord, c in lst[1:]:
            if coord - cluster[-1][0] <= 4:
                cluster.append((coord, c))
            else:
                cluster = [(coord, c)]
                groups.append(cluster)
        for g in groups:
            coord = sum(v for v, _ in g) / len(g)
            col = max(set(c for _, c in g), key=[c for _, c in g].count)
            if edge in ("top", "bot"):
                x = (coord - x0) / iw
                y = 0.0 if edge == "top" else 1.0
            else:
                y = (coord - y0) / ih
                x = 0.0 if edge == "left" else 1.0
            out.append({"edge": edge, "pos": (round(x, 2), round(y, 2)), "color": col})
    return out


def match_parmindex(pos, termbounds, node=32):
    """termbounds: (parmIndex, top,left,bottom,right) in node-space. Clamp the
    drop-in to [0,1] (it's on an edge) and return the parmIndex whose rect
    CONTAINS it (smallest such); else nearest center."""
    ex = min(max(pos[0], 0.0), 1.0)
    ey = min(max(pos[1], 0.0), 1.0)
    contained = [
        (pi, (rgt - lft) * (bot - top))
        for pi, top, lft, bot, rgt in termbounds
        if lft / node <= ex <= rgt / node and top / node <= ey <= bot / node
    ]
    if contained:
        return min(contained, key=lambda z: z[1])[0]
    best, bd = None, 9.0
    for pi, top, lft, bot, rgt in termbounds:
        cx, cy = (lft + rgt) / (2 * node), (top + bot) / (2 * node)
        d = (cx - ex) ** 2 + (cy - ey) ** 2
        if d < bd:
            bd, best = d, pi
    return best


def extract(path, termbounds):
    im = Image.open(path).convert("RGB")
    size, px = im.size, im.load()
    bbox = icon_bbox(size, px)
    if bbox is None:
        print("no icon component found")
        return []
    x0, y0, x1, y1 = bbox
    print(f"icon bbox x[{x0},{x1}] y[{y0},{y1}]  ({x1 - x0}x{y1 - y0})")
    rows = []
    for a in attach_points(size, px, bbox):
        rows.append({**a, "parmIndex": match_parmindex(a["pos"], termbounds)})
    ordered = sorted(
        rows, key=lambda r: (r["edge"] != "right", r["pos"][1], r["pos"][0])
    )
    for r in ordered:
        pi = r["parmIndex"]
        print(f"  {r['edge']:5} @ {r['pos']} [{r['color']:7}] -> parmIndex {pi}")
    return rows


if __name__ == "__main__":
    # termbounds = (parmIndex, top, left, bottom, right) from the BD <dco>
    TB = [
        (0, 0, 21, 32, 32),
        (1, 0, 11, 32, 21),
        (2, 16, 0, 32, 11),
        (3, 0, 0, 16, 11),
    ]
    print("=== 1540 Array To Spreadsheet String ===")
    print("ground truth: delimiter=1(top) array=2(low-L) format=3(up-L) out=0(right)")
    extract("/tmp/a2s.png", TB)
