"""A node in a case or Diagram Disable structure belongs to its frame by the
frame's position, so two frames that carry the same name still show only their
own nodes (#238); and the table rule that names the frames of an error case."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph, loading
from lvkit.graph.describe import describe_vi
from lvkit.graph.diff import diff_to_dict, format_diff
from lvkit.graph.load_problems import repeated_frame_problems
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import CaseStructureNode
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.load_mode import LoadMode
from lvkit.models import CaseFrame, DisableStructureKind
from lvkit.parser.models import ParsedCaseStructure, SelectorKind, SelectorTable
from lvkit.parser.nodes.case import _apply_one_table
from lvkit.parser.vi import parse_vi
from lvkit.pipeline import generate_python
from lvkit.render import render_vi
from lvkit.run_report import (
    EXIT_PARTIAL,
    Problem,
    ProblemKind,
    ProblemStage,
    RunProblems,
    RunReport,
)
from lvkit.vi_diff import diff_vi_files
from tests.diff_viewer_cases import WRAPPED_AFTER, WRAPPED_BEFORE, write_versions
from tests.test_disable_graph import _BD_XML as _DISABLE_BD_XML
from tests.test_lvnet_identity import _assert_graph_identity

# Frame 0 waits for a Random Number (0-1), frame 1 for a Tick Count (ms); a
# Random Number on the VI's diagram feeds the selector. With the output tunnel,
# frame 0 sends its Random Number out and frame 1 its Tick Count, and a Wait
# (ms) on the VI's diagram reads the tunnel.
_RANDOM, _TICK, _SELECTOR_SOURCE = "500", "510", "400"
_WAIT_0, _WAIT_1, _WAIT_OUT = "520", "530", "700"
_TUNNEL_OUTER, _TUNNEL_DCO = "640", "641"
_TUNNEL_INNER = ("650", "660")
_WIRES = [
    (f"{_SELECTOR_SOURCE}1", "601"),
    (f"{_RANDOM}1", f"{_WAIT_0}2"),
    (f"{_TICK}1", f"{_WAIT_1}2"),
]
_TUNNEL_WIRES = [
    (f"{_RANDOM}1", _TUNNEL_INNER[0]),
    (f"{_TICK}1", _TUNNEL_INNER[1]),
    (_TUNNEL_OUTER, f"{_WAIT_OUT}2"),
]
# Where each node sits on the diagram: (top, left) of a 32x32 box.
_AT = {
    _SELECTOR_SOURCE: (20, 20),
    _RANDOM: (80, 120),
    _WAIT_0: (80, 180),
    _TICK: (80, 120),
    _WAIT_1: (80, 180),
    _WAIT_OUT: (80, 320),
}


def _box(uid: str) -> str:
    top, left = _AT[uid]
    return f"<bounds>({top}, {left}, {top + 32}, {left + 32})</bounds>"


def _prim(uid: str, prim_id: int, n_terms: int = 1) -> str:
    """A primitive whose terminal ``k`` (its connector index) is uid ``<uid><k+1>``;
    terminal 0 is its output, the others are inputs."""
    terms = "".join(
        f"""
                  <SL__arrayElement class="term" uid="{uid}{k + 1}">
                    <dco class="parm" uid="{uid}{k + 1}0">
                      {"<objFlags>1</objFlags>" if k == 0 else ""}
                      {f"<parmIndex>{k}</parmIndex>" if k else ""}
                    </dco>
                  </SL__arrayElement>"""
        for k in range(n_terms)
    )
    return f"""
              <SL__arrayElement class="prim" uid="{uid}">
                <primResID>{prim_id}</primResID>
                <termList elements="{n_terms}">{terms}
                </termList>
                {_box(uid)}
              </SL__arrayElement>"""


def _range(value: int, diagram: int) -> str:
    return f"""
          <SL__arrayElement class="SelectorRange">
            <start>{value}</start><end>{value}</end>
            <startRangeType>0</startRangeType><endRangeType>0</endRangeType>
            <diagramIdx>{diagram}</diagramIdx>
          </SL__arrayElement>"""


def _signals(wires: list[tuple[str, str]]) -> str:
    return "".join(
        f"""
      <SL__arrayElement class="signal" uid="{900 + i}">
        <termList elements="2">
          <SL__arrayElement uid="{src}" />
          <SL__arrayElement uid="{dst}" />
        </termList>
      </SL__arrayElement>"""
        for i, (src, dst) in enumerate(wires)
    )


def _tunnel_srn(frame: int) -> str:
    """The node holding the output tunnel's inner terminal in ``frame``."""
    return f"""
              <SL__arrayElement class="sRN" uid="{670 + frame}">
                <termList elements="1">
                  <SL__arrayElement class="term" uid="{_TUNNEL_INNER[frame]}">
                    <dco uid="{_TUNNEL_DCO}" />
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>"""


def _bd_xml(second_value: int, *, tunnel: bool = False) -> str:
    """A two-frame Case structure on the VI's diagram: frame 0 matches 1 and
    holds a Random Number, frame 1 matches ``second_value`` and holds a Tick
    Count. ``second_value=1`` gives both frames the name "1"; ``tunnel`` adds the
    output tunnel."""
    frames = "".join(
        f"""
          <SL__arrayElement class="diag" uid="{610 + i}">
            <zPlaneList elements="0" />
            <nodeList>{_prim(uid, prim_id)}{_prim(wait, 1302, 2)}
              {_tunnel_srn(i) if tunnel else ""}
            </nodeList>
            <signalList elements="0" />
          </SL__arrayElement>"""
        for i, (uid, prim_id, wait) in enumerate(
            ((_RANDOM, 1070, _WAIT_0), (_TICK, 1300, _WAIT_1))
        )
    )
    tunnel_term = f"""
          <SL__arrayElement class="term" uid="{_TUNNEL_OUTER}">
            <dco class="selTun" uid="{_TUNNEL_DCO}">
              <termList elements="3">
                <SL__arrayElement uid="{_TUNNEL_INNER[0]}" />
                <SL__arrayElement uid="{_TUNNEL_INNER[1]}" />
                <SL__arrayElement uid="{_TUNNEL_OUTER}" />
              </termList>
              <objFlags>1</objFlags>
            </dco>
          </SL__arrayElement>"""
    wires = _WIRES + (_TUNNEL_WIRES if tunnel else [])
    return f"""<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="1">
      <SL__arrayElement class="select" uid="600">
        <termList>
          <SL__arrayElement class="term" uid="601">
            <dco class="caseSel" uid="602">
              <termList elements="1">
                <SL__arrayElement uid="601" />
              </termList>
              <typeDesc>TypeID(1)</typeDesc>
            </dco>
          </SL__arrayElement>{tunnel_term if tunnel else ""}
        </termList>
        <bounds>(60, 100, 140, 240)</bounds>
        <diagramList elements="2">{frames}
        </diagramList>
        <caseSelDCO uid="602" />
        <SelectRangeArray32 elements="2">{_range(1, 0)}{_range(second_value, 1)}
        </SelectRangeArray32>
      </SL__arrayElement>
    </zPlaneList>
    <nodeList>{_prim(_SELECTOR_SOURCE, 1070)}
      <SL__arrayElement uid="600" />
      {_prim(_WAIT_OUT, 1302, 2) if tunnel else ""}
    </nodeList>
    <signalList elements="{len(wires)}">{_signals(wires)}
    </signalList>
  </root>
</SL__rootObject>
"""


def _load(
    tmp_path: Path, second_value: int, *, tunnel: bool = False, layout: bool = False
) -> tuple[InMemoryVIGraph, str]:
    bd_xml = tmp_path / "SameName_BDHb.xml"
    bd_xml.write_text(_bd_xml(second_value, tunnel=tunnel))
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml, layout=layout)
    return graph, graph.list_vis()[0]


def _case(graph: InMemoryVIGraph, vi_name: str) -> CaseStructureNode:
    case = graph.get_graph_node(f"{vi_name}::600")
    assert isinstance(case, CaseStructureNode)
    return case


def test_each_frame_holds_its_own_nodes_by_position(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=1)
    case = _case(graph, vi_name)
    assert [f.selector_value for f in case.frames] == ["1", "1"]
    frames = {}
    for uid in (_RANDOM, _WAIT_0, _TICK, _WAIT_1):
        node = graph.get_graph_node(f"{vi_name}::{uid}")
        assert node is not None
        frames[uid] = node.frame
    assert frames == {_RANDOM: "0", _WAIT_0: "0", _TICK: "1", _WAIT_1: "1"}


def _operations(text: str) -> list[str]:
    return text.split("## Operations\n", 1)[1].splitlines()


def test_describe_shows_each_node_once_in_its_own_frame(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=1)
    assert _operations(describe_vi(graph, vi_name)) == [
        "Random Number (0-1) [prim 1070]",
        "Case Structure (2 frames)",
        '  Frame "1":',
        "    Random Number (0-1) [prim 1070]",
        "    Wait (ms) [prim 1302]",
        '  Frame "1":',
        "    Tick Count (ms) [prim 1300]",
        "    Wait (ms) [prim 1302]",
    ]


_LVNET_CASE = """\
    case Random_Number_(0-1)_400::number :
      frame "1" :
        function Random_Number_(0-1)_500 : Random Number (0-1)
          out  number : ?
        function Wait_(ms)_520 : Wait (ms)
          in   milliseconds_to_wait    : ? = Random_Number_(0-1)_500::number
          out  millisecond_timer_value : ?
      frame "1" :
        function Tick_Count_(ms)_510 : Tick Count (ms)
          out  millisecond_timer_value : ?
        function Wait_(ms)_530 : Wait (ms)
          in   milliseconds_to_wait    : ? = Tick_Count_(ms)_510::millisecond_timer_value
          out  millisecond_timer_value : ?
"""  # noqa: E501 (the lvnet line is one long line)


def test_lvnet_shows_each_node_once_in_its_own_frame(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=1)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module)
    assert f"{text}\n".endswith(_LVNET_CASE)
    for verbose in (False, True):
        written = render_lvnet(module, verbose=verbose)
        read_back = reconstruct_module(parse_lvnet(written))
        assert render_lvnet(read_back, verbose=verbose) == written


def test_json_shows_each_node_once_in_its_own_frame(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=1)
    data = netlist_to_dict(build_netlist_from_graph(graph, vi_name))
    (scope,) = [item for item in data["body"] if item["kind"] == "scope"]
    assert [
        (frame["label"], [item["uid"] for item in frame["body"]])
        for frame in scope["frames"]
    ] == [("1", [_RANDOM, _WAIT_0]), ("1", [_TICK, _WAIT_1])]


_GENERATED_BODY = """\
def samename():
    number = random.random()
    match number:
        case 1:
            number_2 = random.random()
            time.sleep(number_2 / 1000)
        case 1:
            millisecond_timer_value = int(time.time() * 1000)
            time.sleep(millisecond_timer_value / 1000)"""


def test_generate_puts_each_node_in_its_own_branch(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=1)
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    assert source.rstrip().endswith(_GENERATED_BODY)
    namespace: dict[str, object] = {}
    exec(compile(source, "samename.py", "exec"), namespace)  # noqa: S102
    function = namespace["samename"]
    assert callable(function)
    assert function() is None


def test_frames_with_different_names_keep_their_names(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=2)
    assert [f.selector_value for f in _case(graph, vi_name).frames] == ["1", "2"]
    assert _operations(describe_vi(graph, vi_name))[2:8] == [
        '  Frame "1":',
        "    Random Number (0-1) [prim 1070]",
        "    Wait (ms) [prim 1302]",
        '  Frame "2":',
        "    Tick Count (ms) [prim 1300]",
        "    Wait (ms) [prim 1302]",
    ]
    text = render_lvnet(build_netlist_from_graph(graph, vi_name))
    assert f"{text}\n".endswith(
        _LVNET_CASE.replace(
            'frame "1" :\n        function Tick', 'frame "2" :\n        function Tick'
        )
    )
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    assert source.rstrip().endswith(
        _GENERATED_BODY.replace(
            "        case 1:\n            milli", "        case 2:\n            milli"
        )
    )


_LVNET_TUNNEL_CASE = """\
      frame "1" :
        function Random_Number_(0-1)_500 : Random Number (0-1)
          out  number : ?
        function Wait_(ms)_520 : Wait (ms)
          in   milliseconds_to_wait    : ? = Random_Number_(0-1)_500::number
          out  millisecond_timer_value : ?
        case_600::out0 = Random_Number_(0-1)_500::number
      frame "1" :
        function Tick_Count_(ms)_510 : Tick Count (ms)
          out  millisecond_timer_value : ?
        function Wait_(ms)_530 : Wait (ms)
          in   milliseconds_to_wait    : ? = Tick_Count_(ms)_510::millisecond_timer_value
          out  millisecond_timer_value : ?
        case_600::out0 = Tick_Count_(ms)_510::millisecond_timer_value
    function Wait_(ms)_700 : Wait (ms)
      in   milliseconds_to_wait    : ? = case_600::out0
      out  millisecond_timer_value : ?
"""  # noqa: E501 (the lvnet line is one long line)


def test_lvnet_each_frame_drives_its_own_value_onto_an_output_tunnel(
    tmp_path: Path,
) -> None:
    graph, vi_name = _load(tmp_path, second_value=1, tunnel=True)
    module = build_netlist_from_graph(graph, vi_name)
    for verbose in (False, True):
        text = render_lvnet(module, verbose=verbose)
        if not verbose:
            assert f"{text}\n".endswith(_LVNET_TUNNEL_CASE)
        read_back = reconstruct_module(parse_lvnet(text))
        _assert_graph_identity(module, read_back, "SameName")
        assert render_lvnet(read_back, verbose=verbose) == text


def test_generate_merges_each_frames_own_value_on_an_output_tunnel(
    tmp_path: Path,
) -> None:
    graph, vi_name = _load(tmp_path, second_value=1, tunnel=True)
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    assert "            case_output = number_2\n" in source
    assert "            case_output = millisecond_timer_value\n" in source
    assert source.rstrip().endswith("    time.sleep(case_output / 1000)")


def _frame_label_texts(svg: str) -> dict[str, str]:
    """Each frame's selector label group's ``data-path`` and its text."""
    texts: dict[str, str] = {}
    for g in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}g"):
        classes = (g.get("class") or "").split()
        if "lv-label" in classes:
            texts[g.get("data-path", "")] = "".join(
                t.text or ""
                for t in g.iter("{http://www.w3.org/2000/svg}text")
            )
    return texts


def _frame_groups(svg: str) -> dict[str, list[str]]:
    """Each frame group's ``data-path`` and the nodes drawn inside it."""
    groups: dict[str, list[str]] = {}
    for g in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}g"):
        classes = (g.get("class") or "").split()
        if "lv-frame" in classes and "lv-label" not in classes:
            groups[g.get("data-path", "")] = sorted(
                n.get("data-node", "") for n in g.iter() if n.get("data-node")
            )
    return groups


def test_render_draws_each_frame_with_only_its_own_nodes(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=1, layout=True)
    svg = render_vi(graph, vi_name)
    assert svg is not None
    assert _frame_groups(svg) == {
        "600:0": [_RANDOM, _WAIT_0],
        "600:1": [_TICK, _WAIT_1],
    }
    assert 'data-lv-frames="0;1"' in svg
    assert 'data-lv-default="0"' in svg
    # A person reads each frame's value; the position is never shown.
    assert _frame_label_texts(svg) == {"600:0": "1", "600:1": "1"}
    assert not re.findall(r'data-(?:path|lv-frames|lv-default|lv-value)="[^"]*#', svg)


def test_render_names_frames_by_their_values_when_they_differ(
    tmp_path: Path,
) -> None:
    graph, vi_name = _load(tmp_path, second_value=2, layout=True)
    svg = render_vi(graph, vi_name)
    assert svg is not None
    assert _frame_groups(svg) == {
        "600:0": [_RANDOM, _WAIT_0],
        "600:1": [_TICK, _WAIT_1],
    }
    assert _frame_label_texts(svg) == {"600:0": "1", "600:1": "2"}
    assert 'data-lv-frames="0;1"' in svg


_REPEATED = (
    'case structure 600 has two frames for "1"; lvkit could not read their values'
)


def test_two_frames_for_one_value_is_one_load_problem(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, second_value=1)
    (problem,) = graph.problems()
    assert (problem.stage, problem.kind, problem.reason) == (
        ProblemStage.LOAD,
        ProblemKind.VALUE_NOT_READ,
        _REPEATED,
    )
    assert problem.line == (
        f"warning: {graph.vi_label(tmp_path / 'SameName_BDHb.xml')}: load: {_REPEATED}"
    )
    assert not problem.is_note
    assert RunReport.of(graph.problems()).exit_code() == EXIT_PARTIAL


def test_generate_with_a_load_problem_says_partial(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bd_xml = tmp_path / "SameName_BDHb.xml"
    bd_xml.write_text(_bd_xml(1))
    generate_python(bd_xml, tmp_path / "out", mode=LoadMode.NONE)
    out = capsys.readouterr().out
    assert (
        "Partial: 1 VIs generated, 0 with gaps (0 gaps), 1 other problem(s) "
        "— see warnings"
    ) in out
    assert "Complete:" not in out


def test_diff_names_a_repeated_frame_by_its_value(tmp_path: Path) -> None:
    graphs = []
    for side in ("before", "after"):
        bd_xml = tmp_path / side / "SameName_BDHb.xml"
        bd_xml.parent.mkdir()
        text = _bd_xml(1)
        if side == "after":  # the second frame's Wait (ms) is deleted
            start = text.index(f'<SL__arrayElement class="prim" uid="{_WAIT_1}">')
            end = text.index("</SL__arrayElement>", text.index("<bounds>", start))
            text = text[:start] + text[end + len("</SL__arrayElement>") :]
        bd_xml.write_text(text)
        graph = InMemoryVIGraph()
        graph.load_vi(bd_xml)
        graphs.append((graph, graph.list_vis()[0]))
    (graph_a, vi_a), (graph_b, vi_b) = graphs
    assert format_diff(graph_a, graph_b, vi_a, vi_b).splitlines()[:3] == [
        "  case (Random Number (0-1)#1.number):",
        '    "1":',
        "-     Wait (ms)#2(milliseconds_to_wait="
        "Tick Count (ms).millisecond_timer_value)"
        " -> millisecond_timer_value",
    ]
    data = diff_to_dict(graph_a, graph_b, vi_a, vi_b)
    (change,) = data["changes"]
    # Each side's location names the frame by position and value.
    assert (change["uid"], change["label"], change["location_after"]) == (
        _WAIT_1,
        "Wait (ms)",
        None,
    )
    assert change["location_before"] == [
        {"structure": "600", "frame": 1, "value": ["1"]}
    ]
    assert data["frame_pairs"] == [
        {"before": "600", "after": "600", "pairs": [[0, 0], [1, 1]]}
    ]


def test_frames_with_their_own_values_are_no_problem(tmp_path: Path) -> None:
    graph, _ = _load(tmp_path, second_value=2)
    assert graph.problems() == []


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        (DisableStructureKind.DIAGRAM, "Diagram Disable structure"),
        (DisableStructureKind.CONDITIONAL, "Conditional Disable structure"),
        (DisableStructureKind.TYPE_SPEC, "Type Specialization structure"),
    ],
)
def test_two_disable_frames_for_one_value_name_the_structure(
    tmp_path: Path, kind: DisableStructureKind, name: str
) -> None:
    bd_xml = tmp_path / "Disable_BDHb.xml"
    bd_xml.write_text(_DISABLE_BD_XML)
    bd = parse_vi(bd_xml=bd_xml).block_diagram
    (disable,) = bd.disable_structures
    assert repeated_frame_problems(bd, "Disable.vi") == []
    disable.kind = kind
    for frame in disable.frames:
        frame.selector_value = "Enabled"
    (problem,) = repeated_frame_problems(bd, "Disable.vi")
    assert problem.reason == (
        f'{name} 100 has two frames for "Enabled"; lvkit could not read their values'
    )


def test_generate_counts_a_problem_found_twice_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The same unreadable file found at two stages is one problem in the run's
    # summary; generate's own summary line counts it the same way.
    def twice(bd: object, vi: str) -> list[Problem]:
        return [
            Problem(vi, stage, ProblemKind.UNREADABLE, "cannot be read")
            for stage in (ProblemStage.EXTRACT, ProblemStage.LOAD)
        ]

    monkeypatch.setattr(loading, "repeated_frame_problems", twice)
    bd_xml = tmp_path / "SameName_BDHb.xml"
    bd_xml.write_text(_bd_xml(2))
    run = RunProblems()
    generate_python(bd_xml, tmp_path / "out", mode=LoadMode.NONE, run=run)
    out = capsys.readouterr().out
    assert "1 other problem(s)" in out
    assert run.report().summary_lines()[0] == "Partial: 1 problem(s)"


def _error_case() -> ParsedCaseStructure:
    return ParsedCaseStructure(
        uid="1",
        frames=[CaseFrame(selector_value=str(i)) for i in range(2)],
        selector_type=SelectorKind.ERROR,
    )


def _statuses(ranges: list[tuple[int, int, int]]) -> list[tuple[str, bool]]:
    case = _error_case()
    _apply_one_table(case, SelectorTable(type_id=0, displayed_frame=0, ranges=ranges))
    assert not any(f.is_default for f in case.frames)
    return [(str(f.selector_value), f.is_no_error) for f in case.frames]


def test_an_error_table_listing_only_the_error_frame() -> None:
    assert _statuses([(1, 1, 1)]) == [("0", True), ("1", False)]


def test_an_error_table_listing_only_the_no_error_frame() -> None:
    assert _statuses([(-2147483648, 0, 0)]) == [("0", True), ("1", False)]


def test_an_error_table_listing_the_error_frame_first() -> None:
    assert _statuses([(1, 1, 0)]) == [("1", False), ("0", True)]


def _step(structure: str, frame: int, *values: str) -> dict[str, object]:
    return {"structure": structure, "frame": frame, "value": list(values)}


def test_a_diff_across_an_inserted_and_a_renamed_frame(tmp_path: Path) -> None:
    before, after = write_versions(tmp_path)
    body = diff_vi_files(before, after, fmt="json", warm_index=False)
    assert body is not None
    data = json.loads(body.body)
    # Before frames "1", "2" are after frames "1" (moved to position 1 by the
    # new frame "5") and "3" (renamed, same contents); the nested case, given a
    # new uid (650 -> 655) and matched by the wire into its selector, keeps
    # frames "7" and "8" and loses "9".
    assert data["frame_pairs"] == [
        {"before": "600", "after": "600", "pairs": [[0, 1], [1, 2]]},
        {"before": "650", "after": "655", "pairs": [[0, 0], [1, 1]]},
    ]
    located = {
        (c["uid"], c["change"]): (c["location_before"], c["location_after"])
        for c in data["changes"]
    }
    assert located == {
        ("~5", "added"): (None, [_step("600", 0, "5")]),
        ("520", "added"): (None, [_step("600", 0, "5")]),
        ("~3", "modified"): ([_step("600", 1, "2")], [_step("600", 2, "3")]),
        ("685", "added"): (None, [_step("600", 2, "3"), _step("655", 0, "7")]),
        ("675", "removed"): ([_step("600", 1, "2"), _step("650", 1, "8")], None),
        ("~9", "removed"): ([_step("600", 1, "2"), _step("650", 2, "9")], None),
        ("690", "removed"): ([_step("600", 1, "2"), _step("650", 2, "9")], None),
    }
    # One block for the nested case, under its after uid, with the removed
    # frame "9" in it.
    text = diff_vi_files(before, after, fmt="text", warm_index=False)
    assert text is not None
    assert text.body.splitlines() == [
        "  case:",
        '+   "5":',
        "+     Tick Count (ms)#1() -> millisecond_timer_value",
        '~   "3": 2 -> 3',
        "      case (Tick Count (ms)#2.millisecond_timer_value):",
        '        "7":',
        "+         Tick Count (ms)#3() -> millisecond_timer_value",
        '        "8":',
        "-         Random Number (0-1)#3() -> number",
        '-       "9":',
        "-         Tick Count (ms)#3() -> millisecond_timer_value",
    ]


def test_a_diff_of_a_case_wrapped_in_a_new_case(tmp_path: Path) -> None:
    before, after = write_versions(tmp_path, WRAPPED_BEFORE, WRAPPED_AFTER)
    body = diff_vi_files(before, after, fmt="json", warm_index=False)
    assert body is not None
    data = json.loads(body.body)
    # The new case 700 is added, so only 600 is matched.
    assert data["frame_pairs"] == [
        {"before": "600", "after": "600", "pairs": [[0, 0], [1, 1]]}
    ]
    (renamed,) = [c for c in data["changes"] if c["uid"] == "~3"]
    assert renamed["location_before"] == [_step("600", 1, "2")]
    assert renamed["location_after"] == [_step("700", 0, "4"), _step("600", 1, "3")]
