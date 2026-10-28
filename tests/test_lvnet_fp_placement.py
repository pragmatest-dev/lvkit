"""A control or indicator whose terminal sits inside a structure is a line of
that structure's body in lvnet, and a ``scope`` of its entry in JSON (#180).

Each case is a tiny Case-structure diagram with front-panel terminals put where
the case names (``fp_placement_fixture.py``): what lvnet and JSON print, and
that the text reads back to the same graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.lvnet_parse import LvnetParseError, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from tests.fp_placement_fixture import Placed, case_with_terminals
from tests.test_lvnet_identity import _assert_graph_identity


def _lvnet_body(tmp_path: Path, controls: list[Placed], indicators: list[Placed]):
    graph, vi = case_with_terminals(tmp_path, controls, indicators)
    module = build_netlist_from_graph(graph, vi)
    text = render_lvnet(module, display_name="Placed.vi", verbose=True)
    return module, text


def _diagram(text: str) -> str:
    return text.split("  block-diagram :\n", 1)[1]


def test_a_terminal_in_a_frame_is_a_line_of_that_frame(tmp_path: Path) -> None:
    module, text = _lvnet_body(
        tmp_path,
        [Placed("Gain"), Placed("Limit", "True")],
        [
            Placed("Rate", "True", "5"),
            Placed("Total", None, "9"),
            Placed("Hidden", "False", "3", on_pane=False),
        ],
    )
    assert _diagram(text) == (
        "    case ? (id 600) :\n"
        '      frame "False" :\n'
        "        Hidden = 3\n"
        '      frame "True" :\n'
        "        read Limit\n"
        "        Rate = 5\n"
        "    Total = 9"
    )
    assert "    out  Hidden : I32\n" in text  # off-pane: a row with no pane slot
    _assert_graph_identity(
        module, reconstruct_module(parse_lvnet(text)), "Placed.vi"
    )
    assert render_lvnet(
        reconstruct_module(parse_lvnet(text)), display_name="Placed.vi", verbose=True
    ) == text


def test_json_gives_each_placed_terminal_its_scope(tmp_path: Path) -> None:
    module, _ = _lvnet_body(
        tmp_path,
        [Placed("Gain"), Placed("Limit", "True")],
        [Placed("Rate", "True", "5"), Placed("Total", None, "9")],
    )
    data = netlist_to_dict(module)
    where = {"uid": "600", "frame": "True"}
    assert [i.get("scope") for i in data["inputs"]] == [None, where]
    assert [o.get("scope") for o in data["outputs"]] == [where, None]
    pane = {t["name"]: t.get("scope") for t in data["connector_pane"]["terminals"]}
    assert pane["Limit"] == where and pane["Gain"] is None


def test_a_vi_with_no_placed_terminal_prints_what_it_always_did(
    tmp_path: Path,
) -> None:
    _, text = _lvnet_body(tmp_path, [Placed("Gain")], [Placed("Total", None, "9")])
    assert _diagram(text) == (
        "    case ? (id 600) :\n"
        '      frame "False" :\n'
        '      frame "True" :\n'
        "    Total = 9"
    )
    assert "read" not in _diagram(text)


def test_names_that_read_as_other_forms_keep_their_own_line(tmp_path: Path) -> None:
    """A control named ``read x`` is read as ``read "read x"``; an indicator
    named like a structure net keeps its quotes and is not that net's drive."""
    module, text = _lvnet_body(
        tmp_path,
        [Placed("read x", "True")],
        [Placed("case_600::out0", "True", "1")],
    )
    assert 'read "read x"' in text
    assert '"case_600::out0" = 1' in text
    rebuilt = reconstruct_module(parse_lvnet(text))
    _assert_graph_identity(module, rebuilt, "Placed.vi")


def test_read_on_the_vi_diagram_is_an_error(tmp_path: Path) -> None:
    _, text = _lvnet_body(tmp_path, [Placed("Gain")], [Placed("Total", None, "9")])
    with pytest.raises(LvnetParseError, match="read <Control>"):
        parse_lvnet(text + "\n    read Gain")


def test_a_structure_terminal_driven_on_the_diagram_is_an_error(
    tmp_path: Path,
) -> None:
    _, text = _lvnet_body(tmp_path, [], [Placed("Total", None, "9")])
    with pytest.raises(LvnetParseError, match="belongs inside the structure"):
        parse_lvnet(text + "\n    case_600::out0 = 1")
