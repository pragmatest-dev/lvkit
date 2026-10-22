"""A one-case-structure block diagram built in memory, read back as describe,
lvnet and JSON text.

The helper writes a minimal ``*_BDHb.xml`` (one Case structure in the root
diagram) and its sibling main ``.xml`` (the VCTP type table that gives the
selector a real type), loads it with ``InMemoryVIGraph``, and returns the same
text the ``lvkit describe`` CLI prints for ``text``, ``--format lvnet -v`` and
``--format json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict

Selector = Literal["boolean", "enum", "error", "integer", "string"]

_CASE_UID = 600
_SELECTOR_TERM_UID = 601
_INNER_TERM_UID_BASE = 611
_DIAG_UID_BASE = 700
_DIAG_UID_STEP = 10


@dataclass(frozen=True)
class FrameSpec:
    """One case frame's stored selector values.

    ``ranges`` are integer / error-code / Boolean values as inclusive
    ``(start, end)`` pairs; ``strings`` are the values of a string selector.
    A frame with neither is a frame that stores no value. ``no_error_point``
    adds the single open-ended point an error case stores for its No Error frame.
    """

    ranges: tuple[tuple[int, int], ...] = ()
    no_error_point: bool = False
    strings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseViews:
    """The describe, lvnet (verbose) and JSON text of the one case structure."""

    describe: str
    lvnet: str
    json: dict[str, Any]


# The VCTP entries the selector's type id points at. An error selector is the
# error cluster (status Boolean, code I32, source String) at type id 3.
_SCALAR_TYPE_NAMES: dict[str, str] = {
    "boolean": "Boolean",
    "integer": "NumInt32",
    "string": "String",
}
_ERROR_CLUSTER_ID = 3


def _type_table(
    selector: Selector, enum_items: tuple[str, ...]
) -> tuple[int, str, list[str]]:
    """``(selector type id, its name, the VCTP section's flat type entries)``."""
    if selector == "enum":
        labels = "".join(f"<EnumLabel>{item}</EnumLabel>" for item in enum_items)
        return 0, "EnumU16", [f'<TypeDesc Type="EnumU16">{labels}</TypeDesc>']
    if selector == "error":
        return (
            _ERROR_CLUSTER_ID,
            "Cluster",
            [
                '<TypeDesc Type="Boolean" Label="status" />',
                '<TypeDesc Type="NumInt32" Label="code" />',
                '<TypeDesc Type="String" Label="source" />',
                '<TypeDesc Type="Cluster">'
                '<TypeDesc TypeID="0" /><TypeDesc TypeID="1" />'
                '<TypeDesc TypeID="2" /></TypeDesc>',
            ],
        )
    name = _SCALAR_TYPE_NAMES[selector]
    return 0, name, [f'<TypeDesc Type="{name}" />']


def _main_xml(selector: Selector, enum_items: tuple[str, ...]) -> str:
    type_id, type_name, flats = _type_table(selector, enum_items)
    top_level = (
        f'<TypeDesc Index="{type_id}" FlatTypeID="{type_id}" />'
        f"<!-- Heap TypeID {type_id} = Consolidated TypeID {type_id}: {type_name} -->"
    )
    section = "".join(flats)
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<RSRC>\n"
        f"<VCTP><TopLevel>{top_level}</TopLevel><Section>{section}</Section></VCTP>\n"
        "</RSRC>\n"
    )


def _range_xml(start: int, end: int, diag: int, *, open_end: bool = False) -> str:
    return (
        '<SL__arrayElement class="SelectorRange">'
        f"<start>{start}</start><end>{end}</end>"
        f"<startRangeType>0</startRangeType><endRangeType>{3 if open_end else 0}"
        "</endRangeType>"
        f"<diagramIdx>{diag}</diagramIdx></SL__arrayElement>"
    )


def _selector_xml(
    selector: Selector,
    frames: list[FrameSpec],
    stored_values: bool,
) -> str:
    """The ``SelectRangeArray32`` and ``SelectStringArray`` xml holding the
    frames' stored values; empty when the save stored none (LabVIEW 8/9)."""
    if not stored_values:
        return ""
    ranges: list[str] = []
    strings: list[str] = []
    for diag, frame in enumerate(frames):
        if selector == "string":
            for value in frame.strings:
                ranges.append(_range_xml(len(strings), len(strings), diag))
                strings.append(value.encode("utf-8").hex().upper())
        else:
            if frame.no_error_point:
                ranges.append(_range_xml(0, 0, diag, open_end=True))
            ranges.extend(_range_xml(s, e, diag) for s, e in frame.ranges)
    range_xml = f"<SelectRangeArray32>{''.join(ranges)}</SelectRangeArray32>"
    string_xml = "".join(f"<SL__arrayElement>{h}</SL__arrayElement>" for h in strings)
    return range_xml + f"<SelectStringArray>{string_xml}</SelectStringArray>"


def _bd_xml(
    selector: Selector,
    frames: list[FrameSpec],
    *,
    default_frame: int | None,
    stored_values: bool,
) -> str:
    type_id, _, _ = _type_table(selector, ())
    diags = "".join(
        f'<SL__arrayElement class="diag" uid="{_DIAG_UID_BASE + _DIAG_UID_STEP * i}">'
        '<zPlaneList elements="0" /><nodeList elements="0" />'
        '<signalList elements="0" /></SL__arrayElement>'
        for i in range(len(frames))
    )
    inner_refs = "".join(
        f'<SL__arrayElement uid="{_INNER_TERM_UID_BASE + i}" />'
        for i in range(len(frames))
    )
    values = _selector_xml(selector, frames, stored_values)
    default = "FF" if default_frame is None else f"{default_frame:02X}"
    case = (
        f'<SL__arrayElement class="caseStruct" uid="{_CASE_UID}">'
        '<termList elements="1">'
        f'<SL__arrayElement class="term" uid="{_SELECTOR_TERM_UID}">'
        '<dco class="caseSel" uid="602">'
        f'<termList elements="{len(frames) + 1}">{inner_refs}'
        f'<SL__arrayElement uid="{_SELECTOR_TERM_UID}" /></termList>'
        f"<typeDesc>TypeID({type_id})</typeDesc></dco>"
        "</SL__arrayElement></termList>"
        f'<diagramList elements="{len(frames)}">{diags}</diagramList>'
        f"{values}"
        f"<SelectDefaultCase>{default}</SelectDefaultCase>"
        "</SL__arrayElement>"
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<SL__rootObject class="oHExt" uid="1">\n'
        '<root class="diag" uid="2">\n'
        f"<zPlaneList>{case}</zPlaneList>\n"
        "</root>\n"
        "</SL__rootObject>\n"
    )


def case_structure_views(
    tmp_path: Path,
    selector: Selector,
    frames: list[FrameSpec],
    *,
    default_frame: int | None,
    stored_values: bool = True,
    enum_items: tuple[str, ...] = (),
) -> CaseViews:
    """Build the one-case VI in ``tmp_path`` and return its describe, lvnet and
    JSON text.

    ``stored_values=False`` writes a LabVIEW 8/9 save: no ``SelectRangeArray32``
    and no ``SelectStringArray``, so the frames carry no values of their own.
    ``enum_items`` are the item names of an ``"enum"`` selector.
    """
    (tmp_path / "Case.xml").write_text(_main_xml(selector, enum_items))
    bd_xml = tmp_path / "Case_BDHb.xml"
    bd_xml.write_text(
        _bd_xml(
            selector,
            frames,
            default_frame=default_frame,
            stored_values=stored_values,
        )
    )
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    vi_name = graph.list_vis()[0]
    module = build_netlist_from_graph(graph, vi_name)
    return CaseViews(
        describe=describe_vi(graph, vi_name),
        lvnet=render_lvnet(
            module,
            display_name=graph.vi_display_name(vi_name),
            verbose=True,
        ),
        json=netlist_to_dict(module),
    )
