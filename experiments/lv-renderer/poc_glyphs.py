import xml.etree.ElementTree as ET
from pathlib import Path
from lvkit.extractor import extract_vi_xml
bd,_,_ = extract_vi_xml(Path(".tmp/array average 1.vi"))
root = ET.parse(bd).getroot()
for e in root.iter("SL__arrayElement"):
    if e.get("class")=="forLoop":
        lb = e.findtext("bounds")
        print("loop", lb)
        # index (i) and limit (N) dcos
        for tag in ("loopIndexDCO","loopLimitDCO"):
            d = e.find(tag)
            if d is not None:
                tb=d.find(".//termBounds")
                print(" ",tag, d.get("class"), tb.text if tb is not None else None)
        # border terms incl tunnels + shift regs
        for t in e.find("termList").findall("SL__arrayElement"):
            dco=t.find("dco"); tb=t.find(".//termBounds")
            # detect auto-index on tunnel
            ai = t.find(".//*[@class='innerLpTun']")
            print("  ", dco.get("class") if dco is not None else None, tb.text if tb is not None else None,
                  "indexing" if dco is not None and dco.findtext("indexing") else "", 
                  "hasInner" if ai is not None else "")
