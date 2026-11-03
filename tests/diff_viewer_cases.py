"""Two versions of a handmade VI for driving the diff viewer: a case structure
(uid 600) whose before version has frames ``1`` and ``2`` and whose after
version gains a frame ``5`` in front and renames ``2`` to ``3`` (same
contents). Frame ``2``/``3`` holds a nested case whose selector the frame's
Tick Count (uid 510) feeds; LabVIEW re-keyed it (uid 650 before, 655 after),
so the diff matches it by that wiring. Its frame ``8`` gains a Random Number
(uid 680) in the after version."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

RANDOM, TICK = 1070, 1300


@dataclass
class Case:
    uid: int
    top: int
    left: int
    frames: list[tuple[int, list[tuple[int, int]]]]  # (value, [(uid, prim id)])
    nested: dict[int, Case] = field(default_factory=dict)  # frame position -> case
    # The node whose output feeds the selector, or None for an unwired one.
    selector_source: int | None = None


def _prim(uid: int, prim_id: int, top: int, left: int) -> str:
    return f"""
<SL__arrayElement class="prim" uid="{uid}">
  <primResID>{prim_id}</primResID>
  <termList elements="1">
    <SL__arrayElement class="term" uid="{uid}1">
      <dco class="parm" uid="{uid}10"><objFlags>1</objFlags></dco>
    </SL__arrayElement>
  </termList>
  <bounds>({top}, {left}, {top + 32}, {left + 32})</bounds>
</SL__arrayElement>"""


def _range(value: int, diagram: int) -> str:
    return f"""
<SL__arrayElement class="SelectorRange">
  <start>{value}</start><end>{value}</end>
  <startRangeType>0</startRangeType><endRangeType>0</endRangeType>
  <diagramIdx>{diagram}</diagramIdx>
</SL__arrayElement>"""


def _case(case: Case) -> str:
    frames = []
    for i, (_, prims) in enumerate(case.frames):
        nested = case.nested.get(i)
        items = "".join(
            _prim(uid, prim_id, case.top + 30, case.left + 20 + 50 * k)
            for k, (uid, prim_id) in enumerate(prims)
        )
        frames.append(
            f"""
<SL__arrayElement class="diag" uid="{case.uid * 10 + i}">
  <zPlaneList>{_case(nested) if nested else ""}</zPlaneList>
  <nodeList>{items}{f'<SL__arrayElement uid="{nested.uid}" />' if nested else ""}
  </nodeList>
  <signalList elements="0" />
</SL__arrayElement>"""
        )
    ranges = "".join(_range(value, i) for i, (value, _) in enumerate(case.frames))
    width = 400 if case.nested else 200
    return f"""
<SL__arrayElement class="select" uid="{case.uid}">
  <termList elements="1">
    <SL__arrayElement class="term" uid="{case.uid}1">
      <dco class="caseSel" uid="{case.uid}2">
        <termList elements="1"><SL__arrayElement uid="{case.uid}1" /></termList>
      </dco>
    </SL__arrayElement>
  </termList>
  <bounds>({case.top}, {case.left}, {case.top + 200}, {case.left + width})</bounds>
  <diagramList elements="{len(case.frames)}">{"".join(frames)}</diagramList>
  <caseSelDCO uid="{case.uid}2" />
  <SelectRangeArray32 elements="{len(case.frames)}">{ranges}</SelectRangeArray32>
</SL__arrayElement>"""


def _wires(case: Case) -> list[tuple[str, str]]:
    """Every (source terminal, sink terminal) wire of ``case`` and its nested
    cases."""
    own = (
        [(f"{case.selector_source}1", f"{case.uid}1")]
        if case.selector_source is not None
        else []
    )
    return [*own, *(w for nested in case.nested.values() for w in _wires(nested))]


def bd_xml(case: Case) -> str:
    wires = _wires(case)
    signals = "".join(
        f"""
<SL__arrayElement class="signal" uid="{900 + i}">
  <termList elements="2">
    <SL__arrayElement uid="{src}" /><SL__arrayElement uid="{dst}" />
  </termList>
</SL__arrayElement>"""
        for i, (src, dst) in enumerate(wires)
    )
    return f"""<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="1">{_case(case)}</zPlaneList>
    <nodeList elements="1"><SL__arrayElement uid="{case.uid}" /></nodeList>
    <signalList elements="{len(wires)}">{signals}</signalList>
  </root>
</SL__rootObject>
"""


def _nested(uid: int, *, added: bool) -> Case:
    frame_8 = [(670, TICK), *([(680, RANDOM)] if added else [])]
    return Case(
        uid, 120, 140, [(7, [(660, RANDOM)]), (8, frame_8)], selector_source=510
    )


BEFORE = Case(
    600,
    60,
    100,
    [(1, [(500, RANDOM)]), (2, [(510, TICK)])],
    {1: _nested(650, added=False)},
)
AFTER = Case(
    600,
    60,
    100,
    [(5, [(520, TICK)]), (1, [(500, RANDOM)]), (3, [(510, TICK)])],
    {2: _nested(655, added=True)},
)


def write_versions(folder: Path) -> tuple[Path, Path]:
    """Write the before and after block diagrams; returns their paths."""
    paths = []
    for side, case in (("before", BEFORE), ("after", AFTER)):
        path = folder / side / "Frames_BDHb.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bd_xml(case))
        paths.append(path)
    return paths[0], paths[1]
