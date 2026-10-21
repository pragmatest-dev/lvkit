"""Sequence locals of a Stacked Sequence (#172): a value one frame writes and
later frames read, carried by one terminal per frame (``seqLocDCOList``).

The block diagram below has one Stacked Sequence of three frames and one
sequence local: frame 0 writes a Random Number (0-1) into it, frame 2 reads it
to select a Case structure.
"""

from __future__ import annotations

import ast
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import SequenceNode
from lvkit.graph.netlist import NetlistScope, build_netlist_from_graph
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.netlist_models import SequenceLocalUnreadable
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.models import SequenceLocal
from lvkit.parser.nodes.sequence import extract_flat_sequences

# Terminal uids of the one sequence local (uid 5046), one per frame.
_WRITE_TERM = "5055"
_MID_TERM = "5060"
_READ_TERM = "5085"
_FRAME_TERMS = [_WRITE_TERM, _MID_TERM, _READ_TERM]


def _case_structure(uid: int, selector_term: int) -> str:
    """A two-frame Case structure whose selector terminal is ``selector_term``."""
    dco = uid + 1
    return f"""
      <SL__arrayElement class="select" uid="{uid}">
        <termList elements="1">
          <SL__arrayElement class="term" uid="{selector_term}">
            <dco class="caseSel" uid="{dco}">
              <termList elements="3">
                <SL__arrayElement uid="{uid + 10}" />
                <SL__arrayElement uid="{uid + 20}" />
                <SL__arrayElement uid="{selector_term}" />
              </termList>
              <typeDesc>TypeID(1)</typeDesc>
            </dco>
          </SL__arrayElement>
        </termList>
        <diagramList elements="2">
          <SL__arrayElement class="diag" uid="{uid + 2}">
            <zPlaneList elements="0" />
            <nodeList elements="1">
              <SL__arrayElement class="sRN" uid="{uid + 3}">
                <termList elements="1">
                  <SL__arrayElement class="term" uid="{uid + 10}">
                    <dco uid="{dco}" />
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>
            </nodeList>
            <signalList elements="0" />
          </SL__arrayElement>
          <SL__arrayElement class="diag" uid="{uid + 4}">
            <zPlaneList elements="0" />
            <nodeList elements="1">
              <SL__arrayElement class="sRN" uid="{uid + 5}">
                <termList elements="1">
                  <SL__arrayElement class="term" uid="{uid + 20}">
                    <dco uid="{dco}" />
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>
            </nodeList>
            <signalList elements="0" />
          </SL__arrayElement>
        </diagramList>
        <caseSelDCO uid="{dco}" />
        <SelectRangeArray32 elements="2">
          <SL__arrayElement class="SelectorRange">
            <start>0</start><end>0</end>
            <startRangeType>0</startRangeType><endRangeType>0</endRangeType>
            <diagramIdx>0</diagramIdx>
          </SL__arrayElement>
          <SL__arrayElement class="SelectorRange">
            <start>1</start><end>1</end>
            <startRangeType>0</startRangeType><endRangeType>0</endRangeType>
            <diagramIdx>1</diagramIdx>
          </SL__arrayElement>
        </SelectRangeArray32>
      </SL__arrayElement>"""


def _bd_xml(
    *,
    write_wire: bool = True,
    read_in_frame: int = 2,
    second_write: bool = False,
    write_frame: int = 0,
) -> str:
    """The Stacked Sequence block diagram. The Case structure sits in frame 2
    and its selector is wired from the local's terminal in ``read_in_frame``;
    the Random Number's value is wired into the terminal of ``write_frame``
    (none when ``write_wire`` is off); ``second_write`` also wires it into the
    frame-1 terminal (two writes)."""
    case = _case_structure(600, 601)
    # The terminal the reader's wire leaves from, per frame.
    read_term = _FRAME_TERMS[read_in_frame]
    frame_z = {0: "", 1: "", 2: case}
    frame_srn = {
        0: f"""<SL__arrayElement class="sRN" uid="210">
                <termList elements="1">
                  <SL__arrayElement class="term" uid="{_WRITE_TERM}">
                    <dco uid="5046" />
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>
              <SL__arrayElement class="prim" uid="500">
                <primResID>1070</primResID>
                <termList elements="1">
                  <SL__arrayElement class="term" uid="501">
                    <dco class="parm" uid="502" />
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>""",
        1: f"""<SL__arrayElement class="sRN" uid="310">
                <termList elements="1">
                  <SL__arrayElement class="term" uid="{_MID_TERM}">
                    <dco uid="5046" />
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>""",
        2: f"""<SL__arrayElement class="sRN" uid="410">
                <termList elements="1">
                  <SL__arrayElement class="term" uid="{_READ_TERM}">
                    <dco uid="5046" />
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>""",
    }
    diags = "".join(
        f"""
          <SL__arrayElement class="diag" uid="{200 + 100 * i}">
            <zPlaneList elements="0">{frame_z[i]}</zPlaneList>
            <nodeList elements="2">{frame_srn[i]}</nodeList>
            <signalList elements="0" />
          </SL__arrayElement>"""
        for i in range(3)
    )
    wires = []
    if write_wire:
        wires.append(("1", "501", _FRAME_TERMS[write_frame]))
    if second_write:
        wires.append(("2", "501", _MID_TERM))
    wires.append(("3", read_term, "601"))
    signals = "".join(
        f"""
      <SL__arrayElement class="signal" uid="{uid}">
        <termList elements="2">
          <SL__arrayElement uid="{src}" />
          <SL__arrayElement uid="{dst}" />
        </termList>
      </SL__arrayElement>"""
        for uid, src, dst in wires
    )
    return f"""<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="1">
      <SL__arrayElement class="sequence" uid="100">
        <termList elements="0" />
        <tunnelList elements="0" />
        <seqLocDCOList elements="1">
          <SL__arrayElement class="sLoc" uid="5046">
            <termList elements="3">
              <SL__arrayElement uid="{_WRITE_TERM}" />
              <SL__arrayElement uid="{_MID_TERM}" />
              <SL__arrayElement uid="{_READ_TERM}" />
            </termList>
            <typeDesc>TypeID(822)</typeDesc>
          </SL__arrayElement>
        </seqLocDCOList>
        <diagramList elements="3">{diags}
        </diagramList>
      </SL__arrayElement>
    </zPlaneList>
    <nodeList elements="1"><SL__arrayElement uid="100" /></nodeList>
    <signalList elements="{len(wires)}">{signals}
    </signalList>
  </root>
</SL__rootObject>
"""


def _load(tmp_path: Path, **kwargs: bool | int) -> tuple[InMemoryVIGraph, str]:
    bd_xml = tmp_path / "SeqLocal_BDHb.xml"
    bd_xml.write_text(_bd_xml(**kwargs))  # type: ignore[arg-type]
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    return graph, graph.list_vis()[0]


def test_parser_reads_sequence_local() -> None:
    root = ET.fromstring(_bd_xml())
    (seq,) = extract_flat_sequences(root)
    assert seq.sequence_locals == [
        SequenceLocal(
            uid="5046", frame_terminal_uids=[_WRITE_TERM, _MID_TERM, _READ_TERM]
        )
    ]


def test_graph_joins_write_to_read(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    seq = graph.get_graph_node(f"{vi_name}::100")
    assert isinstance(seq, SequenceNode)
    (link,) = seq.sequence_locals
    assert link.write_terminal == f"{vi_name}::{_WRITE_TERM}"
    assert link.write_frame == 0
    assert link.read_terminals == [f"{vi_name}::{_READ_TERM}"]
    sources = graph.incoming_edges(f"{vi_name}::{_READ_TERM}")
    assert [s.terminal_id for s in sources] == [f"{vi_name}::{_WRITE_TERM}"]


def test_lvnet_names_the_local_and_round_trips(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module)
    assert "sequence_100::local0 = " in text
    assert "case sequence_100::local0" in text
    assert text.index("sequence_100::local0 = ") < text.index("frame [1]")
    parsed = parse_lvnet(text)
    assert netlist_signature(parsed) == netlist_signature(module)
    assert render_lvnet(reconstruct_module(parsed)) == text


def test_describe_names_the_sequence_local(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    text = describe_vi(graph, vi_name)
    assert "Stacked sequence (3 frames)" in text
    assert "gated on sequence local (frame 0)" in text


def test_generated_python_runs_frames_in_order(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    tree = ast.parse(source)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    assigned = {
        t.id
        for n in ast.walk(func)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    tested = [
        n.subject.id
        for n in ast.walk(func)
        if isinstance(n, ast.Match) and isinstance(n.subject, ast.Name)
    ]
    assert tested
    assert set(tested) <= assigned
    namespace: dict = {}
    exec(compile(source, "<seqlocal>", "exec"), namespace)  # noqa: S102
    namespace[func.name]()


def _assert_unreadable_everywhere(
    tmp_path: Path, reason_part: str, **kwargs: bool | int
) -> None:
    """The VI loads; the reason shows in describe, lvnet (and survives the
    lvnet round trip) and JSON."""
    graph, vi_name = _load(tmp_path, **kwargs)
    seq = graph.get_graph_node(f"{vi_name}::100")
    assert isinstance(seq, SequenceNode)
    (link,) = seq.sequence_locals
    assert link.unreadable is not None
    assert reason_part in link.unreadable
    for read in link.read_terminals:
        assert graph.incoming_edges(read) == []

    assert reason_part in describe_vi(graph, vi_name)

    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module)
    assert '= (sequence local unreadable "' in text
    assert reason_part in text
    parsed = parse_lvnet(text)
    assert netlist_signature(parsed) == netlist_signature(module)
    assert render_lvnet(reconstruct_module(parsed)) == text

    scope = next(
        item
        for item in module.body
        if isinstance(item, NetlistScope) and item.kind == "sequence"
    )
    assert reason_part in json.dumps(netlist_to_dict(module))
    assert any(isinstance(o, SequenceLocalUnreadable) for o in scope.outputs)


def test_read_without_write_loads_and_flags_the_local(tmp_path: Path) -> None:
    _assert_unreadable_everywhere(tmp_path, "never written", write_wire=False)


def test_two_writes_loads_and_flags_the_local(tmp_path: Path) -> None:
    _assert_unreadable_everywhere(tmp_path, "exactly one write", second_write=True)


def test_read_before_write_loads_and_flags_the_local(tmp_path: Path) -> None:
    _assert_unreadable_everywhere(
        tmp_path, "must come in a later frame", write_frame=2, read_in_frame=1
    )
