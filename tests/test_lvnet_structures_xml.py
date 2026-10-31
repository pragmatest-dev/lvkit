"""What lvnet, describe and JSON print for each structure's border (#179), from
tiny block-diagram XML: the parser reads the heap, the graph is built, and the
exact text is asserted -- including the neighbouring cases the change did not
mean to touch.

The diagrams are in ``structure_xml_cases.py``, built from ``structure_diagrams.py``.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests import structure_xml_cases as cases
from tests.structure_diagrams import Views, front_panel_xml, load
from tests.test_lvnet_identity import _assert_graph_identity


def _round_trips(views: Views) -> None:
    """The verbose text reads back to the same graph and the same text."""
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


_INCREMENT = (
    "    function Increment_810 : Increment\n"
    "      in   x   : I32 = {net}\n"
    "      out  x+1 : I32"
)


# ---------------------------------------------------------------------------
# Case structure
# ---------------------------------------------------------------------------


def test_a_case_selector_is_not_a_structure_input(tmp_path: Path) -> None:
    views = load(tmp_path, cases.case_xml())
    assert views.diagram == (
        "    case True (id 600) :\n"
        "      case_600::in0 = 7\n"
        '      frame "False" :\n'
        "        case_600::out0 = (default I32)\n"
        '      frame "True" :\n'
        "        case_600::out0 = 1\n" + _INCREMENT.format(net="case_600::out0")
    )
    _round_trips(views)


def test_an_unwired_selector_changes_nothing_else(tmp_path: Path) -> None:
    views = load(tmp_path, cases.case_xml(wire_selector=False))
    assert views.diagram.splitlines()[:2] == [
        "    case ? (id 600) :",
        "      case_600::in0 = 7",
    ]
    _round_trips(views)


def test_an_input_tunnel_a_frame_reads_has_no_line_of_its_own(tmp_path: Path) -> None:
    views = load(tmp_path, cases.case_xml(read_input=True))
    assert "case_600::in0" not in views.diagram
    assert "      in   x   : I32 = 7\n" in views.diagram
    _round_trips(views)


def test_an_unwired_case_output_is_a_default_not_a_constant(tmp_path: Path) -> None:
    views = load(tmp_path, cases.case_xml())
    (merge,) = views.json["body"][0]["outputs"]
    assert [
        (c["frame"], c["source"]["kind"] if "kind" in c["source"] else "net")
        for c in merge["cases"]
    ] == [("False", "default"), ("True", "net")]
    assert merge["cases"][0]["source"]["literal"] == "0"
    assert views.json["body"][0]["inputs"] == [
        {
            "net": "case_600.in0",
            "value": {
                "node": None,
                "terminal": "7",
                "occurrence": None,
                "bare": "7",
            },
        }
    ]


def test_describe_keeps_to_the_frames(tmp_path: Path) -> None:
    text = load(tmp_path, cases.case_xml()).describe
    assert "Case Structure (2 frames)" in text
    assert "in0" not in text and "out0" not in text


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------


def test_a_for_loop_opens_with_its_unread_input_then_its_count(
    tmp_path: Path,
) -> None:
    views = load(tmp_path, cases.loop_xml("forLoop"))
    assert views.diagram == (
        "    for-loop (id 400) :\n"
        "      loop_400::in0 = 7\n"
        "      loop_400::N = 5"
    )
    assert views.json["body"][0]["count"]["terminal"] == "5"
    assert views.json["body"][0]["inputs"][0]["net"] == "loop_400.in0"
    _round_trips(views)


def test_a_while_loop_says_how_its_condition_reads(tmp_path: Path) -> None:
    (tmp_path / "stop").mkdir()
    stop = load(tmp_path / "stop", cases.loop_xml("whileLoop"))
    assert stop.diagram == (
        "    while-loop (stop if true) (id 400) :\n"
        "      loop_400::in0 = 7\n"
        "      loop_400::condition = False"
    )
    _round_trips(stop)
    (tmp_path / "go").mkdir()
    go = load(tmp_path / "go", cases.loop_xml("whileLoop", stop_if_true=False))
    assert go.diagram.splitlines()[0] == "    while-loop (continue if true) (id 400) :"
    assert go.json["body"][0]["condition"] == "continue_if_true"
    _round_trips(go)


# ---------------------------------------------------------------------------
# Sequences, Disable and Event structures
# ---------------------------------------------------------------------------


def test_a_flat_sequence_drives_a_tunnel_only_in_the_frame_that_wires_it(
    tmp_path: Path,
) -> None:
    views = load(tmp_path, cases.flat_sequence_xml())
    assert views.diagram == (
        "    flat-sequence (id 1000) :\n"
        "      sequence_1000::in0 = 7\n"
        "      frame [0] :\n"
        "      frame [1] :\n"
        "        sequence_1000::out0 = 3\n"
        + _INCREMENT.format(net="sequence_1000::out0")
    )
    assert views.json["body"][0]["outputs"] == [
        {
            "net": "sequence_1000.out0",
            "kind": "frame_output",
            "frame": "1",
            "value": {"node": None, "terminal": "3", "occurrence": None, "bare": "3"},
        }
    ]
    _round_trips(views)


def test_a_stacked_sequence_counts_every_input_tunnel_read_or_not(
    tmp_path: Path,
) -> None:
    views = load(tmp_path, cases.stacked_sequence_xml())
    assert views.diagram == (
        "    stacked-sequence (id 2000) :\n"
        "      sequence_2000::in1 = 9\n"
        "      frame [0] :\n"
        "        function Increment_820 : Increment\n"
        "          in   x   : I32 = 7\n"
        "          out  x+1 : I32\n"
        "      frame [1] :\n"
        "        sequence_2000::out0 = 3\n"
        + _INCREMENT.format(net="sequence_2000::out0")
    )
    _round_trips(views)


def test_a_disable_structure_drives_its_output_in_the_frame_that_wires_it(
    tmp_path: Path,
) -> None:
    views = load(tmp_path, cases.disable_xml())
    assert views.diagram == (
        "    diagram-disable (id 3000) :\n"
        "      disabled_3000::in0 = 7\n"
        "      frame Enabled, default :\n"
        "        disabled_3000::out0 = 3\n"
        "      frame Disabled :\n"
        "        disabled_3000::out0 = (default I32)\n"
        + _INCREMENT.format(net="disabled_3000::out0")
    )
    _round_trips(views)


def test_an_event_structure_has_a_timeout_and_one_dynamic_net(tmp_path: Path) -> None:
    views = load(tmp_path, cases.event_xml())
    assert views.diagram == (
        "    event-structure (id 4000) :\n"
        "      event_4000::timeout = -1\n"
        "      event_4000::dynamic = 5\n"
        '      frame "[0] Timeout" :\n'
        "        event_4000::out0 = (default I32)\n"
        '      frame "[1] Value Change" :\n'
        "        event_4000::out0 = 3\n"
        "        event_4000::dynamic = 9\n"
        + _INCREMENT.format(net="event_4000::out0")
        + "\n    function Increment_820 : Increment\n"
        "      in   x   : I32 = event_4000::dynamic\n"
        "      out  x+1 : I32"
    )
    inputs = views.json["body"][0]["inputs"]
    assert [i["net"] for i in inputs] == ["event_4000.timeout", "event_4000.dynamic"]
    assert [
        o["net"] for o in views.json["body"][0]["outputs"]
    ] == ["event_4000.out0", "event_4000.dynamic"]
    _round_trips(views)


def test_a_conditional_disable_label_is_one_string_literal(tmp_path: Path) -> None:
    views = load(tmp_path, cases.conditional_disable_xml())
    assert views.diagram == (
        "    conditional-disable (id 3000) :\n"
        '      frame "TARGET==a\\"b", default :\n'
        "        disabled_3000::out0 = 3\n"
        '      frame "Default" :\n'
        "        disabled_3000::out0 = (default I32)\n"
        + _INCREMENT.format(net="disabled_3000::out0")
    )
    labels = [f["label"] for f in views.json["body"][0]["frames"]]
    assert labels == ['TARGET==a"b', "Default"]
    _round_trips(views)


def test_a_type_specialization_drives_its_output_like_the_other_disables(
    tmp_path: Path,
) -> None:
    views = load(tmp_path, cases.conditional_disable_xml(type_specialization=True))
    assert views.diagram == (
        "    type-specialization (id 3000) :\n"
        "      frame [0], default :\n"
        "        disabled_3000::out0 = 3\n"
        "      frame [1] :\n"
        "        disabled_3000::out0 = (default I32)\n"
        + _INCREMENT.format(net="disabled_3000::out0")
    )
    _round_trips(views)


def test_an_event_frame_label_with_quotes_is_escaped(tmp_path: Path) -> None:
    (tmp_path / "T_FPHb.xml").write_text(front_panel_xml({10: "stop"}, {}))
    views = load(tmp_path, cases.event_on_control_xml())
    assert views.diagram == (
        "    event-structure (id 4000) :\n"
        '      frame "[0] Timeout" :\n'
        '      frame "[1] \\"stop\\": Value Change" :'
    )
    assert views.json["body"][0]["frames"][1]["label"] == '[1] "stop": Value Change'
    _round_trips(views)


def test_a_value_handed_from_one_flat_sequence_frame_to_the_next_is_one_net(
    tmp_path: Path,
) -> None:
    (tmp_path / "read").mkdir()
    read = load(tmp_path / "read", cases.flat_sequence_hand_over_xml())
    assert read.diagram == (
        "    flat-sequence (id 1000) :\n"
        "      frame [0] :\n"
        "        sequence_1000::out0 = 3\n"
        "      frame [1] :\n"
        "        function Increment_820 : Increment\n"
        "          in   x   : I32 = sequence_1000::out0\n"
        "          out  x+1 : I32"
    )
    _round_trips(read)
    (tmp_path / "unread").mkdir()
    unread = load(
        tmp_path / "unread", cases.flat_sequence_hand_over_xml(read_in_frame=False)
    )
    assert unread.diagram == (
        "    flat-sequence (id 1000) :\n"
        "      frame [0] :\n"
        "        sequence_1000::out0 = 3\n"
        "      frame [1] :"
    )
    assert "inputs" not in unread.json["body"][0]
    _round_trips(unread)
