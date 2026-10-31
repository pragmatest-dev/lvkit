"""A ring or enum value names its item (#176): `2 (verify)` in lvnet and describe,
and a ring numbered by its own values lists them: `ring{verify = 2, commit = 3}`.

Each case is a tiny diagram of ring constants wired to Increment nodes, written as
heap XML (``structure_diagrams.py``): what lvnet, describe and JSON print, the
neighbouring cases (a ring numbered 0, 1, 2, a value that is no item, an item
needing quotes), and that the text reads back to the same graph.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.lvnet_reconstruct_common import parse_source_token
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.render_lvnet import lvnet_control_name_token
from tests.structure_diagrams import (
    Views,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    ring_constant,
    root_diagram,
    srn,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity

_ITEMS = ["verify", "commit", "reserve"]


def _views(tmp_path: Path, *rings: str) -> Views:
    """Each ring constant (terminal 701, 711, ...) feeds its own Increment."""
    nodes = [increment(10 * (i + 1)) for i in range(len(rings))]
    wires = [wire(900 + i, 701 + 10 * i, 13 + 10 * i) for i in range(len(rings))]
    return load(tmp_path, root_diagram(nodes, [srn(800, list(rings))], wires))


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def test_a_ring_with_its_own_values_names_the_selected_item(tmp_path: Path) -> None:
    views = _views(tmp_path, ring_constant(701, 2, _ITEMS, [2, 3, 4]))
    assert views.diagram.splitlines()[1] == "      in   x   : I32 = 2 (verify)"
    assert (
        "ring{verify = 2, commit = 3, reserve = 4} = 2 (verify)" in views.describe
    )
    _round_trips(views)


def test_a_ring_numbered_from_zero_lists_names_and_still_names_the_item(
    tmp_path: Path,
) -> None:
    views = _views(tmp_path, ring_constant(701, 1, ["a", "b", "c"]))
    assert views.diagram.splitlines()[1] == "      in   x   : I32 = 1 (b)"
    assert "ring{a, b, c} = 1 (b)" in views.describe
    _round_trips(views)


def test_a_value_that_is_no_item_has_no_name(tmp_path: Path) -> None:
    views = _views(tmp_path, ring_constant(701, 9, _ITEMS, [2, 3, 4]))
    assert views.diagram.splitlines()[1] == "      in   x   : I32 = 9"
    assert "= 9\n" in views.describe
    _round_trips(views)


def test_an_item_that_needs_quotes_is_quoted(tmp_path: Path) -> None:
    views = _views(tmp_path, ring_constant(701, 7, ["a, b", "c = d"], [7, 8]))
    assert views.diagram.splitlines()[1] == '      in   x   : I32 = 7 ("a, b")'
    _round_trips(views)


def test_a_source_of_number_and_item_reads_as_a_literal() -> None:
    ref = parse_source_token("2 (verify)", {})
    assert not isinstance(ref, type(None))
    assert getattr(ref, "lvnet_value", None) == "2 (verify)"
    assert getattr(ref, "boundary", True) is False


def test_a_control_named_like_a_number_and_item_is_quoted() -> None:
    assert lvnet_control_name_token("2 (verify)") == '"2 (verify)"'
    assert lvnet_control_name_token("verify") == "verify"


def _ring_control(tmp_path: Path, default: int) -> Views:
    """A Ring control ``mode`` on the connector pane, read by an Increment."""
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml(
            {20: "mode"}, {}, pane={0: 20}, rings={20: (default, _ITEMS, [2, 3, 4])}
        )
    )
    xml = root_diagram(
        [increment(10)], [srn(800, [fp_terminal(5020, 20)])], [wire(901, 5020, 13)]
    )
    return load(tmp_path, xml)


def test_a_ring_control_default_names_its_item(tmp_path: Path) -> None:
    views = _ring_control(tmp_path, 3)
    assert (
        "    in   mode : ring{verify = 2, commit = 3, reserve = 4}"
        " default 3 (commit) @0\n"
    ) in views.lvnet
    assert "= 3 (commit)" in views.describe
    _round_trips(views)


def test_a_ring_control_default_that_is_no_item_has_no_name(tmp_path: Path) -> None:
    views = _ring_control(tmp_path, 9)
    assert " default 9 @0\n" in views.lvnet
    assert "= 9 (" not in views.describe and "= \"9\"" not in views.describe
    _round_trips(views)
