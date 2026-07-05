"""Geometry-aware reader: pylabview block-diagram heap XML → ``DiagramScene``.

lvkit's normal parser discards block-diagram node geometry (it keeps only
front-panel control bounds). Faithful rendering needs that geometry, so this is
a separate, render-only pass over the same ``_BDHb.xml`` heap that
``extractor.extract_vi_xml`` already produces. It never touches code generation.

Model (all coordinates absolute, in LabVIEW pixels):
  - ``SceneNode``      primitive / subVI / constant / front-panel terminal, with bounds
  - ``SceneStructure`` For / While / Case / Sequence, with border terminals (N, i, …)
  - ``SceneWire``      a signal net resolved to terminal-center points
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..extractor import extract_vi_xml

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x1, y1, x2, y2

# DCO class → the loop/structure border glyph it represents.
_DCO_GLYPH = {
    "lMax": "N",          # For-Loop count terminal
    "lCnt": "i",          # iteration terminal (For + While)
    "lTst": "cond",       # While-Loop conditional terminal
    "lSR": "sr_down",     # left shift register
    "rSR": "sr_up",       # right shift register
    "lpTun": "autoindex",  # auto-indexing tunnel
}

_STRUCT_KINDS = {
    "forLoop": "forLoop",
    "whileLoop": "whileLoop",
    "flatSequence": "flatSequence",
    "sequence": "flatSequence",
    "sequenceFrame": "flatSequence",
    "stackedSequence": "stackedSequence",
    "select": "case",          # LabVIEW Case structure (with a diagramList)
    "eventStruct": "event",
}

# SubVI call nodes (static / polymorphic / dynamic-dispatch).
_SUBVI_CLASSES = {"iUse", "polyIUse", "dynIUse"}

# Classes handled elsewhere or intentionally not drawn as generic nodes.
_SKIP_NODE_CLASSES = {
    "prim", "fPTerm", "parm", "overridableParm",
    "stdNum", "stdBool", "stdString", "stdCString", "stdPath",
    "label", "commentNode", "attachment", "bd",
}


@dataclass(frozen=True)
class SceneTerminal:
    uid: str
    center: Point


@dataclass
class SceneNode:
    kind: str  # primitive | subvi | node | constant | fpterm | unknown
    bounds: Rect
    name: str
    prim_res_id: str | None = None
    icon_png: Path | None = None


@dataclass
class SceneBorderTerminal:
    kind: str  # "N" | "i" | "cond" | "sr_down" | "sr_up" | "autoindex" | "selector"
    bounds: Rect


@dataclass
class SceneStructure:
    kind: str  # forLoop | whileLoop | flatSequence | case | event | ...
    bounds: Rect
    border_terms: list[SceneBorderTerminal] = field(default_factory=list)
    label: str = ""  # e.g. the active case label for a Case structure


@dataclass
class SceneWire:
    endpoints: list[Point]


@dataclass
class DiagramScene:
    bounds: Rect
    nodes: list[SceneNode] = field(default_factory=list)
    structures: list[SceneStructure] = field(default_factory=list)
    wires: list[SceneWire] = field(default_factory=list)
    icon_png: Path | None = None

    @property
    def obstacles(self) -> list[Rect]:
        """Node rectangles wires should avoid (structures are not obstacles)."""
        return [n.bounds for n in self.nodes]

    @property
    def endpoints(self) -> list[Point]:
        pts: list[Point] = []
        for w in self.wires:
            pts.extend(w.endpoints)
        return pts


@lru_cache(maxsize=1)
def _prim_data() -> tuple[dict[str, str], dict[str, str]]:
    """(primResID → name, xml-node-class → name) from primitives.json."""
    path = Path(__file__).resolve().parent.parent / "data" / "primitives.json"
    data = json.loads(path.read_text())
    by_id = {rid: e.get("name", f"prim {rid}") for rid, e in data["primitives"].items()}
    by_class = {
        k: e.get("name", k) for k, e in data.get("node_types", {}).items()
    }
    return by_id, by_class


def _prim_name(rid: str | None) -> str:
    if not rid:
        return "prim"
    return _prim_data()[0].get(rid, f"prim {rid}")


# Friendly labels for common block-diagram node classes without a primResID.
_CLASS_LABEL = {
    "propNode": "Property", "select": "Select", "nMux": "Bundle",
    "concat": "Concatenate", "gRef": "Ref", "scriptNode": "Formula",
    "eventDataNode": "Event Data", "dynIUse": "SubVI", "polyIUse": "SubVI",
    "iUse": "SubVI",
}


def _node_label(cls: str, elem: ET.Element) -> str:
    """Friendly display name for a generic node: its own label, else a name from
    primitives.json node_types, else a title-cased class."""
    lbl = (elem.findtext("label") or "").strip()
    if lbl:
        return lbl
    by_class = _prim_data()[1]
    if cls in by_class:
        return by_class[cls]
    if cls in _CLASS_LABEL:
        return _CLASS_LABEL[cls]
    return cls


def _bounds(elem: ET.Element) -> Rect | None:
    b = elem.find("bounds")
    if b is None or not b.text:
        return None
    t, left, btm, r = (int(x) for x in b.text.strip("()").split(","))
    return float(left), float(t), float(r), float(btm)  # x1, y1, x2, y2


def _term_bounds(text: str) -> Rect:
    t, left, btm, r = (int(x) for x in text.strip("()").split(","))
    return float(left), float(t), float(r), float(btm)


class _SceneBuilder:
    def __init__(self) -> None:
        self.nodes: list[SceneNode] = []
        self.structures: list[SceneStructure] = []
        self.wires: list[SceneWire] = []
        self.term_center: dict[str, Point] = {}

    # -- terminal / uid mapping --------------------------------------------
    def _map_terms(self, elem: ET.Element, ox: float, oy: float) -> None:
        tl = elem.find("termList")
        if tl is None:
            return
        for term in tl.findall("SL__arrayElement"):
            if term.get("class") != "term":
                continue
            tb = term.find(".//termBounds")
            if tb is None or not tb.text:
                continue
            x1, y1, x2, y2 = _term_bounds(tb.text)
            cx, cy = ox + (x1 + x2) / 2, oy + (y1 + y2) / 2
            ddo = term.find(".//ddo")
            if ddo is not None:
                cb = _bounds(ddo)
                if cb:
                    self.nodes.append(
                        SceneNode(kind="constant", bounds=cb, name="")
                    )
                    cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
            for u in self._collect_uids(term):
                self.term_center[u] = (cx, cy)

    @staticmethod
    def _collect_uids(elem: ET.Element) -> set[str]:
        uids: set[str] = set()
        if elem.get("uid"):
            uids.add(elem.get("uid", ""))
        dco = elem.find("dco")
        if dco is not None and dco.get("uid"):
            uids.add(dco.get("uid", ""))
        for s in elem.findall(".//termList/SL__arrayElement"):
            if s.get("uid"):
                uids.add(s.get("uid", ""))
        return {u for u in uids if u}

    def _global_terms(self, root: ET.Element) -> None:
        for e in root.iter("SL__arrayElement"):
            cls = e.get("class")
            if cls == "fPTerm" and e.find("bounds") is not None:
                b = _bounds(e)
                if b is None:
                    continue
                c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                for u in self._collect_uids(e):
                    self.term_center[u] = c
            elif cls == "term":
                ddo = e.find(".//ddo")
                if ddo is not None and ddo.find("bounds") is not None:
                    b = _bounds(ddo)
                    if b is None:
                        continue
                    c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                    for u in self._collect_uids(e):
                        self.term_center[u] = c

    # -- loop border terminals ---------------------------------------------
    def _loop_terms(
        self, struct: ET.Element, ox: float, oy: float
    ) -> list[SceneBorderTerminal]:
        out: list[SceneBorderTerminal] = []
        candidates: list[ET.Element] = []
        idx = struct.find("loopIndexDCO")
        if idx is not None:
            candidates.append(idx)
        cnt = struct.find("loopLimitDCO")
        if cnt is not None:
            candidates.append(cnt)
        test = struct.find("loopTestDCO")
        if test is not None:
            candidates.append(test)
        tl = struct.find("termList")
        if tl is not None:
            candidates.extend(tl.findall("SL__arrayElement"))
        for t in candidates:
            dco = t if t.get("class") in _DCO_GLYPH else t.find("dco")
            if dco is None or dco.get("class") not in _DCO_GLYPH:
                continue
            src = t if t.find("termBounds") is not None else dco
            tb = src.find(".//termBounds")
            if tb is None or not tb.text:
                continue
            x1, y1, x2, y2 = _term_bounds(tb.text)
            out.append(
                SceneBorderTerminal(
                    kind=_DCO_GLYPH[dco.get("class", "")],
                    bounds=(ox + x1, oy + y1, ox + x2, oy + y2),
                )
            )
        return out

    # -- recursive walk -----------------------------------------------------
    def walk(self, diag: ET.Element, ox: float, oy: float) -> None:
        sl = diag.find("signalList")
        if sl is not None:
            for sig in sl.findall("SL__arrayElement"):
                if sig.get("class") != "signal":
                    continue
                tl = sig.find("termList")
                refs = (
                    [s.get("uid", "") for s in tl.findall("SL__arrayElement")]
                    if tl is not None
                    else []
                )
                self.wires.append(SceneWire(endpoints=[u for u in refs if u]))  # type: ignore[list-item]
        zp = diag.find("zPlaneList")
        if zp is None:
            return
        for elem in zp.findall("SL__arrayElement"):
            cls = elem.get("class") or ""
            bb = _bounds(elem)
            if bb is None:
                continue
            ax1, ay1 = ox + bb[0], oy + bb[1]
            ax2, ay2 = ox + bb[2], oy + bb[3]
            self._map_terms(elem, ax1, ay1)
            dlist = elem.find("diagramList")
            inner = (
                [d for d in dlist.findall("SL__arrayElement")
                 if d.get("class") == "diag"]
                if dlist is not None
                else []
            )
            if inner:
                kind = _STRUCT_KINDS.get(cls, cls)
                border = self._loop_terms(elem, ax1, ay1) if "Loop" in cls else []
                if kind == "case":
                    border += self._case_selector(elem, ax1, ay1)
                self.structures.append(
                    SceneStructure(
                        kind=kind, bounds=(ax1, ay1, ax2, ay2), border_terms=border,
                        label=(elem.findtext("selString") or "").strip()
                        if kind == "case" else "",
                    )
                )
                for d in inner:
                    self.walk(d, ax1, ay1)
                continue
            seqlist = elem.find("sequenceList")
            if seqlist is not None:
                # Flat/stacked sequence: frames live under sequenceList, each
                # with its own diagramList — recurse so their nodes are captured.
                self.structures.append(
                    SceneStructure(kind="flatSequence", bounds=(ax1, ay1, ax2, ay2))
                )
                for frame in seqlist.findall("SL__arrayElement"):
                    fdl = frame.find("diagramList")
                    if fdl is None:
                        continue
                    for d in fdl.findall("SL__arrayElement"):
                        if d.get("class") == "diag":
                            self.walk(d, ax1, ay1)
                continue
            if cls == "prim":
                self.nodes.append(
                    SceneNode(kind="primitive", bounds=(ax1, ay1, ax2, ay2),
                              name=_prim_name(elem.findtext("primResID")),
                              prim_res_id=elem.findtext("primResID"))
                )
            elif cls in ("fPTerm", "parm", "overridableParm"):
                self.nodes.append(
                    SceneNode(kind="fpterm", bounds=(ax1, ay1, ax2, ay2), name="")
                )
            elif cls in _SUBVI_CLASSES:
                self.nodes.append(
                    SceneNode(kind="subvi", bounds=(ax1, ay1, ax2, ay2),
                              name=_node_label(cls, elem))
                )
            elif cls not in _SKIP_NODE_CLASSES:
                # Any other node with geometry — render as a labeled box so the
                # diagram is complete even without a bespoke glyph.
                self.nodes.append(
                    SceneNode(kind="node", bounds=(ax1, ay1, ax2, ay2),
                              name=_node_label(cls, elem))
                )

    def _case_selector(
        self, struct: ET.Element, ox: float, oy: float
    ) -> list[SceneBorderTerminal]:
        dco = struct.find("caseSelDCO")
        if dco is None:
            return []
        tb = dco.find(".//termBounds")
        if tb is None or not tb.text:
            return []
        x1, y1, x2, y2 = _term_bounds(tb.text)
        return [SceneBorderTerminal(kind="selector",
                                    bounds=(ox + x1, oy + y1, ox + x2, oy + y2))]


def _resolve_wires(builder: _SceneBuilder) -> list[SceneWire]:
    resolved: list[SceneWire] = []
    for w in builder.wires:
        pts = [builder.term_center[u] for u in w.endpoints if u in builder.term_center]  # type: ignore[index]
        if len(pts) >= 2:
            resolved.append(SceneWire(endpoints=pts))
    return resolved


def _scene_bounds(builder: _SceneBuilder, pad: float = 30.0) -> Rect:
    xs: list[float] = []
    ys: list[float] = []
    for grp in (builder.nodes, builder.structures):
        for item in grp:
            x1, y1, x2, y2 = item.bounds
            xs += [x1, x2]
            ys += [y1, y2]
    if not xs:
        return (0.0, 0.0, 100.0, 100.0)
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def build_scene(vi_or_bd: Path) -> DiagramScene:
    """Build a ``DiagramScene`` from a ``.vi`` file or a ``_BDHb.xml`` heap path."""
    if vi_or_bd.suffix.lower() == ".vi":
        bd_path, _, _ = extract_vi_xml(vi_or_bd)
        bd = Path(bd_path)
    else:
        bd = vi_or_bd
    root_elem = ET.parse(bd).getroot()
    root = root_elem.find("root") or root_elem

    builder = _SceneBuilder()
    builder.walk(root, 0.0, 0.0)
    builder._global_terms(root)

    icon = bd.parent / f"{bd.stem.replace('_BDHb', '')}_ICON.png"
    scene = DiagramScene(
        bounds=_scene_bounds(builder),
        nodes=builder.nodes,
        structures=builder.structures,
        wires=_resolve_wires(builder),
        icon_png=icon if icon.exists() else None,
    )
    return scene
