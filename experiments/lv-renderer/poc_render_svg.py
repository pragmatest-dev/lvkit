"""POC: render a LabVIEW block diagram to SVG straight from pylabview heap XML.

No LabVIEW required. Reads absolute geometry (bounds), terminal offsets
(termBounds), wire connectivity (signal -> termList -> term uids), and the
VI icon that pylabview already extracts. Wires are self-routed orthogonally.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.extractor import extract_vi_xml

VI = Path(".tmp/array average 1.vi")
OUT = Path("outputs/array_average_1_diagram.svg")

PRIMS = json.load(open("src/lvkit/data/primitives.json"))["primitives"]


def prim_name(rid):
    e = PRIMS.get(str(rid))
    return e["name"] if e else f"prim {rid}"


def bounds(elem):
    b = elem.find("bounds")
    if b is None or not b.text:
        return None
    t, l, btm, r = (int(x) for x in b.text.strip("()").split(","))
    return l, t, r, btm  # x1,y1,x2,y2


# Collected drawables
structures, nodes, consts, fpterms, wires = [], [], [], [], []
glyphs = []       # (x1,y1,x2,y2, kind) loop terminals / tunnels / shift regs
term_center = {}  # uid -> (x,y)
term_box = []     # (x1,y1,x2,y2) small terminal squares

PRIM_SYMBOL = {"Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷"}


def parse_tb(text):
    t, l, b, r = (int(x) for x in text.strip("()").split(","))
    return l, t, r, b


def process_terms(elem, ox, oy):
    """Map every uid that a wire might reference -> a terminal center."""
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
        term_box.append((x1, y1, x2, y2))
        # constant rendered from a ddo (stdNum) with its own absolute bounds
        ddo = term.find(".//ddo")
        if ddo is not None:
            cb = bounds(ddo)
            if cb:
                consts.append(cb)
                cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
        # map term uid, dco uid, and all nested inner/outer paired uids
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
    """FP terminals and constants are stored once with absolute display bounds."""
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


DCO_GLYPH = {"lMax": "N", "lCnt": "i", "lSR": "sr_down", "rSR": "sr_up",
             "lpTun": "autoindex"}


def collect_loop_glyphs(loop, lx, ly):
    """N/i terminals, auto-indexing tunnels, shift registers (positions from termBounds)."""
    seen = []
    # index terminal (i) lives in loopIndexDCO, not the termList
    idx = loop.find("loopIndexDCO")
    if idx is not None:
        seen.append(idx)
    seen += loop.find("termList").findall("SL__arrayElement")
    for t in seen:
        dco = t if t.get("class") in DCO_GLYPH else t.find("dco")
        if dco is None or dco.get("class") not in DCO_GLYPH:
            continue
        tb = (idx if t is idx else dco).find(".//termBounds")
        if tb is None:
            continue
        gl, gt, gr, gb = parse_tb(tb.text)  # left, top, right, bottom
        glyphs.append((lx + gl, ly + gt, lx + gr, ly + gb, DCO_GLYPH[dco.get("class")]))


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
        inner_diags = ([d for d in dlist.findall("SL__arrayElement") if d.get("class") == "diag"]
                       if dlist is not None else [])
        if inner_diags:  # structure (loop / case / sequence)
            structures.append((ax1, ay1, ax2, ay2, "For Loop" if cls == "forLoop" else cls))
            if cls == "forLoop":
                collect_loop_glyphs(elem, ax1, ay1)
            for d in inner_diags:
                walk(d, ax1, ay1)
        elif cls == "prim":
            nodes.append((ax1, ay1, ax2, ay2, prim_name(elem.findtext("primResID"))))
        elif cls in ("fPTerm", "parm", "overridableParm"):
            fpterms.append((ax1, ay1, ax2, ay2))


bd, _, _ = extract_vi_xml(VI)
root = ET.parse(bd).getroot().find("root")
walk(root, 0, 0)
global_terms(root)

# ---- compute viewBox ----
allpts = [(s[0], s[1], s[2], s[3]) for s in structures + [(n[0], n[1], n[2], n[3]) for n in nodes]]
xs = [v for s in structures for v in (s[0], s[2])] + [v for n in nodes for v in (n[0], n[2])] \
     + [v for c in consts for v in (c[0], c[2])] + [v for f in fpterms for v in (f[0], f[2])]
ys = [v for s in structures for v in (s[1], s[3])] + [v for n in nodes for v in (n[1], n[3])] \
     + [v for c in consts for v in (c[1], c[3])] + [v for f in fpterms for v in (f[1], f[3])]
pad = 30
minx, maxx, miny, maxy = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
W, H = maxx - minx, maxy - miny


DBL = "#e8821e"  # LabVIEW orange = floating-point scalar


def route(p1, p2):
    (x1, y1), (x2, y2) = p1, p2
    mx = (x1 + x2) / 2
    return f"M{x1},{y1} L{mx},{y1} L{mx},{y2} L{x2},{y2}"


svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx} {miny} {W} {H}" font-family="sans-serif">']
svg.append('<rect x="%d" y="%d" width="%d" height="%d" fill="#fbfbf5"/>' % (minx, miny, W, H))
# structures (For Loop: square-cornered like LabVIEW)
for x1, y1, x2, y2, name in structures:
    svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" '
               f'fill="none" stroke="#6c6c8a" stroke-width="2"/>')
# wires (orange = DBL; type-driven width/colour is the productisation step)
for refs in wires:
    pts = [term_center[u] for u in refs if u in term_center]
    for i in range(len(pts) - 1):
        svg.append(f'<path d="{route(pts[i], pts[i+1])}" fill="none" stroke="{DBL}" '
                   f'stroke-width="2.5" stroke-linejoin="round"/>')
# FP terminals (DBL orange; thick border = control)
for x1, y1, x2, y2 in fpterms:
    svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="2" '
               f'fill="#fff3e2" stroke="{DBL}" stroke-width="3"/>')
    svg.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2+4}" font-size="9" text-anchor="middle" fill="{DBL}">[1.23]</text>')
# constants
for x1, y1, x2, y2 in consts:
    svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="2" '
               f'fill="#fff3e2" stroke="{DBL}" stroke-width="2"/>')
    svg.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2+4}" font-size="10" text-anchor="middle">0</text>')
# loop glyphs: N / i terminals, auto-index tunnels, shift registers
for x1, y1, x2, y2, kind in glyphs:
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    if kind in ("N", "i"):
        svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" '
                   f'fill="#1f3fbf"/>')
        svg.append(f'<text x="{cx}" y="{cy+4}" font-size="11" text-anchor="middle" fill="#fff" font-style="italic">{kind}</text>')
    elif kind == "autoindex":  # array auto-indexing tunnel: [ ] bracket box
        svg.append(f'<rect x="{x1-3}" y="{y1-3}" width="{x2-x1+6}" height="{y2-y1+6}" '
                   f'fill="#fff" stroke="#333" stroke-width="1.2"/>')
        svg.append(f'<text x="{cx}" y="{cy+4}" font-size="10" text-anchor="middle">[ ]</text>')
    else:  # shift register: filled box + arrow (down on left, up on right)
        arrow = "▼" if kind == "sr_down" else "▲"
        svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" '
                   f'fill="#cfcfcf" stroke="#555"/>')
        svg.append(f'<text x="{cx}" y="{cy+4}" font-size="10" text-anchor="middle">{arrow}</text>')
# primitive nodes drawn as LabVIEW-style triangles with the operator symbol
for x1, y1, x2, y2, name in nodes:
    sym = PRIM_SYMBOL.get(name, "")
    if sym:
        svg.append(f'<polygon points="{x1},{y1} {x2},{(y1+y2)/2} {x1},{y2}" '
                   f'fill="#fff6d8" stroke="#b07d10" stroke-width="1.5"/>')
        svg.append(f'<text x="{x1+(x2-x1)*0.32}" y="{(y1+y2)/2+5}" font-size="15" text-anchor="middle">{sym}</text>')
    else:
        svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" fill="#fff6d8" stroke="#b07d10"/>')
        svg.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2+4}" font-size="10" text-anchor="middle">{name}</text>')
# VI icon (corner)
icon = Path(bd).parent / f"{VI.stem}_ICON.png"
if icon.exists():
    svg.append(f'<image href="{icon}" x="{minx+5}" y="{miny+5}" width="32" height="32"/>')
svg.append("</svg>")

OUT.parent.mkdir(exist_ok=True)
OUT.write_text("\n".join(svg))
print("structures:", len(structures), "nodes:", [n[4] for n in nodes],
      "consts:", len(consts), "fpterms:", len(fpterms), "wires:", len(wires),
      "terms mapped:", len(term_center))
unresolved = sum(1 for refs in wires for u in refs if u not in term_center)
print("unresolved wire endpoints:", unresolved)
print("wrote", OUT)
