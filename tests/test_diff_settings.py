"""The diff of two handmade VIs that differ only in a node's or structure's
settings: the node or structure is modified, and the text says what changed."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.diff import diff_to_dict, format_diff
from tests.structure_diagrams import (
    BOOL,
    Views,
    constant,
    dco,
    load,
    root_diagram,
    srn,
    term,
    wire,
)
from tests.structure_xml_cases import loop_xml

_AND, _OR = 2 << 16, 3 << 16


def _compound_arithmetic(uid: int, mode_flags: int) -> str:
    """A Compound Arithmetic node of one Boolean input (constant 705, terminal
    ``uid + 3``) and one output (terminal ``uid + 1``)."""
    out = term(
        uid + 1,
        dco("cpdArithDCO", uid + 2, type_id=BOOL, flags=0x20001),
    )
    inp = term(
        uid + 3,
        dco("cpdArithDCO", uid + 4, type_id=BOOL, flags=0x40000),
    )
    return (
        f'<SL__arrayElement class="cpdArith" uid="{uid}">'
        f"<objFlags>{mode_flags}</objFlags>"
        f'<termList elements="2">{out}{inp}</termList>'
        f'<dcoList elements="1"><SL__arrayElement uid="{uid + 4}" /></dcoList>'
        f'<dcoAgg uid="{uid + 2}" /></SL__arrayElement>'
    )


def _compound_vi(mode_flags: int) -> str:
    return root_diagram(
        [],
        [srn(800, [constant(705, BOOL, True)]), _compound_arithmetic(500, mode_flags)],
        [wire(901, 705, 503)],
    )


def _two(tmp_path: Path, before: str, after: str) -> tuple[Views, Views]:
    views = []
    for side, xml in (("before", before), ("after", after)):
        folder = tmp_path / side
        folder.mkdir()
        views.append(load(folder, xml))
    return views[0], views[1]


def test_a_compound_arithmetic_mode_change_is_a_modified_node(tmp_path: Path) -> None:
    before, after = _two(tmp_path, _compound_vi(_AND), _compound_vi(_OR))
    assert "Compound Arithmetic (mode=and)" in before.diagram
    assert "Compound Arithmetic (mode=or)" in after.diagram
    data = diff_to_dict(before.graph, after.graph, before.vi, after.vi)
    (change,) = data["changes"]
    assert (change["uid"], change["kind"], change["change"]) == (
        "500",
        "node",
        "modified",
    )
    assert change["detail"] == "mode: and -> or"
    text = format_diff(before.graph, after.graph, before.vi, after.vi)
    assert text.splitlines() == [
        "~ Compound Arithmetic [mode: or](1=True) -> Compound Arithmetic.0 "
        "(mode: and -> or)"
    ]


def test_a_while_loop_stop_condition_change_is_a_modified_structure(
    tmp_path: Path,
) -> None:
    before, after = _two(
        tmp_path,
        loop_xml("whileLoop", stop_if_true=True),
        loop_xml("whileLoop", stop_if_true=False),
    )
    data = diff_to_dict(before.graph, after.graph, before.vi, after.vi)
    (change,) = data["changes"]
    assert (change["uid"], change["kind"], change["change"]) == (
        "400",
        "structure",
        "modified",
    )
    assert change["detail"] == "stop: if-true -> if-false"
    text = format_diff(before.graph, after.graph, before.vi, after.vi)
    assert text.splitlines() == ["~ while (False): (stop: if-true -> if-false)"]


def test_an_unchanged_pair_reports_nothing(tmp_path: Path) -> None:
    before, after = _two(tmp_path, _compound_vi(_AND), _compound_vi(_AND))
    assert diff_to_dict(before.graph, after.graph, before.vi, after.vi)["changes"] == []
