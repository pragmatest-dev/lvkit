"""Probe raw pylabview heap XML for facts a faithful renderer needs.

Answers:
  1. Do wire signals store bend/segment geometry, or only terminal uid refs?
  2. How are whileLoop conditional terminal + case selector + sequence stored?
  3. What geometry/labels do front-panel controls carry (for NiceGUI angle)?
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.extractor import extract_vi_xml

SAMPLES = {
    "forloop": Path(".tmp/array average 1.vi"),
}
# find a VI with a while loop and one with a case, from repo samples
import subprocess


def first_with(classname, limit=400):
    root_dir = Path("samples")
    n = 0
    for vi in root_dir.rglob("*.vi"):
        n += 1
        if n > limit:
            break
        try:
            bd, _, _ = extract_vi_xml(vi)
        except Exception:
            continue
        txt = Path(bd).read_text(errors="ignore")
        if f'class="{classname}"' in txt:
            return vi, bd
    return None, None


def show_signal(bd_path, label):
    root = ET.parse(bd_path).getroot()
    sig = None
    for s in root.iter("SL__arrayElement"):
        if s.get("class") == "signal":
            sig = s
            break
    print(f"\n### [{label}] first <signal> raw structure:")
    if sig is None:
        print("  (no signal found)")
        return
    # print immediate children tags + any wire/point geometry
    for child in sig:
        geom = ""
        if child.tag in ("wireManager", "wire", "wireTable", "points", "cosmetic"):
            geom = " <-- possible geometry"
        print(f"  child: <{child.tag}> class={child.get('class')}{geom}")
    # dump any element mentioning 'wire' or 'point' or 'coord'
    for e in sig.iter():
        if any(k in e.tag.lower() for k in ("wire", "point", "bend", "coord", "cosmet")):
            print(f"    geom-elem <{e.tag}> = {(e.text or '').strip()[:60]}")


print("=" * 60)
fbd, _, _ = extract_vi_xml(SAMPLES["forloop"])
show_signal(fbd, "forloop-sample")

for cls in ("whileLoop", "mux", "flatSequence", "stackedSequence"):
    vi, bd = first_with(cls)
    print(f"\n### structure class '{cls}': {'FOUND in ' + str(vi) if vi else 'not found in first 400 samples'}")
    if not bd:
        continue
    root = ET.parse(bd).getroot()
    for e in root.iter("SL__arrayElement"):
        if e.get("class") == cls:
            print(f"  <bounds> = {e.findtext('bounds')}")
            # list distinctive child tags
            kids = [c.tag for c in e]
            print(f"  child tags: {sorted(set(kids))}")
            # conditional terminal / selector
            for tag in ("loopTestDCO", "condTerm", "selDCO", "selector", "loopIndexDCO"):
                d = e.find(tag)
                if d is not None:
                    tb = d.find(".//termBounds")
                    print(f"    {tag}: class={d.get('class')} termBounds={tb.text if tb is not None else None}")
            break

# front panel controls (from FP heap)
print("\n" + "=" * 60)
print("### front-panel controls (for NiceGUI angle):")
_, fp, _ = extract_vi_xml(SAMPLES["forloop"])
fproot = ET.parse(fp).getroot()
seen = 0
for e in fproot.iter("SL__arrayElement"):
    cls = e.get("class") or ""
    b = e.find("bounds")
    if b is not None and b.text and ("Num" in cls or "Bool" in cls or "String" in cls or "Ring" in cls or cls.startswith("std") or cls.startswith("tab")):
        label = e.findtext(".//label/SL__arrayElement/label//text") or e.findtext(".//label//text")
        print(f"  class={cls:18} bounds={b.text} label={label}")
        seen += 1
        if seen >= 12:
            break
