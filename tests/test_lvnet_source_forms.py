"""Two ways a wire's source is spelled when the diagram does not name it: an
unnamed front-panel control, and a producer that is not there. Both read back to
the same graph.

Each case is a tiny diagram written as heap XML (``structure_diagrams.py``): an
Increment node whose input is wired from the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import LvnetReconstructError, reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests.structure_diagrams import (
    Views,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    root_diagram,
    srn,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def test_an_unnamed_control_is_read_under_the_name_of_its_row(tmp_path: Path) -> None:
    """The control's fPTerm names a front-panel object the heap does not have."""
    xml = root_diagram(
        [increment(10)],
        [srn(800, [fp_terminal(5010, 99)])],
        [wire(901, 5010, 13)],
    )
    views = load(tmp_path, xml)
    assert "    in   input : I32\n" in views.lvnet
    assert "      in   x   : I32 = input\n" in views.lvnet
    _round_trips(views)


def test_a_source_the_diagram_does_not_have_is_spelled_by_uid(tmp_path: Path) -> None:
    xml = root_diagram([increment(10)], [], [wire(901, 999, 13)])
    views = load(tmp_path, xml)
    assert views.diagram.splitlines()[1] == "      in   x   : I32 = 999::999"
    _round_trips(views)


def _two_controls_with_one_label(tmp_path: Path) -> Views:
    """Controls 20 and 30 are both labelled ``x``; Increment_10 reads the first
    (terminal 5020), Increment_20 the second (terminal 5030)."""
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({20: "x", 30: "x"}, {}, pane={0: 20, 1: 30})
    )
    xml = root_diagram(
        [increment(10), increment(20)],
        [srn(800, [fp_terminal(5020, 20), fp_terminal(5030, 30)])],
        [wire(901, 5020, 13), wire(902, 5030, 23)],
    )
    return load(tmp_path, xml)


def test_a_wire_from_a_control_with_a_shared_label_names_its_terminal(
    tmp_path: Path,
) -> None:
    views = _two_controls_with_one_label(tmp_path)
    assert views.diagram == (
        "    function Increment_10 : Increment\n"
        "      in   x   : I32 = x (id 5020)\n"
        "      out  x+1 : I32\n"
        "    function Increment_20 : Increment\n"
        "      in   x   : I32 = x (id 5030)\n"
        "      out  x+1 : I32"
    )
    _round_trips(views)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    reads = [i.inputs[0].net.boundary_uid for i in rebuilt.body]  # type: ignore[union-attr]
    assert reads == ["5020", "5030"]


def test_terse_text_cannot_say_which_control_a_wire_reads(tmp_path: Path) -> None:
    views = _two_controls_with_one_label(tmp_path)
    assert "(id" not in views.terse
    with pytest.raises(LvnetReconstructError, match="gives each its terminal id"):
        reconstruct_module(parse_lvnet(views.terse))
