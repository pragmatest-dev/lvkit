"""Inspect wire tables vs known terminal endpoints to gauge decodability."""
import xml.etree.ElementTree as ET
from pathlib import Path
from lvkit.extractor import extract_vi_xml

VI = Path(".tmp/array average 1.vi")
bd, _, _ = extract_vi_xml(VI)
root = ET.parse(bd).getroot()

# reuse minimal endpoint mapping: uid -> center from termBounds
def parse_tb(t):
    a,b,c,d = (int(x) for x in t.strip("()").split(","))
    return a,b,c,d  # top,left,bottom,right

# collect ALL term centers with absolute coords by walking with offsets
term_center = {}
def bounds(e):
    b=e.find("bounds")
    if b is None or not b.text: return None
    t,l,btm,r=(int(x) for x in b.text.strip("()").split(",")); return l,t,r,btm
def walk(diag, ox, oy):
    zp=diag.find("zPlaneList")
    if zp is None: return
    for elem in zp.findall("SL__arrayElement"):
        bb=bounds(elem)
        if bb is None: continue
        ax,ay=ox+bb[0],oy+bb[1]
        tl=elem.find("termList")
        if tl is not None:
            for term in tl.findall("SL__arrayElement"):
                tb=term.find(".//termBounds")
                if tb is None or not tb.text: continue
                t,l,b2,r=parse_tb(tb.text)
                cx,cy=ax+(l+r)/2, ay+(t+b2)/2
                for u in [term.get("uid")] + [s.get("uid") for s in term.findall(".//termList/SL__arrayElement")]:
                    if u: term_center[u]=(cx,cy)
                dco=term.find("dco")
                if dco is not None and dco.get("uid"): term_center[dco.get("uid")]=(cx,cy)
        dl=elem.find("diagramList")
        if dl is not None:
            for d in dl.findall("SL__arrayElement"):
                if d.get("class")=="diag": walk(d, ax, ay)
walk(root.find("root") if root.find("root") is not None else root, 0, 0)

# now dump each signal
r2 = root.find("root") if root.find("root") is not None else root
def dump(diag, depth=0):
    sl=diag.find("signalList")
    if sl is not None:
        for sig in sl.findall("SL__arrayElement"):
            if sig.get("class")!="signal": continue
            tl=sig.find("termList")
            uids=[s.get("uid") for s in tl.findall("SL__arrayElement")] if tl is not None else []
            pts=[term_center.get(u) for u in uids]
            cwt=sig.findtext("compressedWireTable")
            wt=sig.findtext("wireTable")
            print(f"signal: {len(uids)} terms")
            print(f"  endpoints: {[ (round(p[0]),round(p[1])) if p else None for p in pts]}")
            print(f"  compressedWireTable: {cwt}")
            if wt: print(f"  wireTable: {wt}")
    zp=diag.find("zPlaneList")
    if zp is not None:
        for elem in zp.findall("SL__arrayElement"):
            dl=elem.find("diagramList")
            if dl is not None:
                for d in dl.findall("SL__arrayElement"):
                    if d.get("class")=="diag": dump(d, depth+1)
dump(r2)
