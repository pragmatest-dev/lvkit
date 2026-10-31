"""A control or indicator whose terminal sits inside a structure is a line of that
structure's body in lvnet, and a ``scope`` of its entry in JSON (#180).

Each case is a tiny block diagram written as heap XML (``placement_cases.py``): the
parser attributes the front-panel terminal to its structure and frame, the graph
and the netlist are built, and the exact text is asserted -- for every structure
kind, for nesting, for two terminals sharing a label, and for the VI's own diagram,
which gets no new line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.lvnet_parse import LvnetParseError, parse_lvnet
from lvkit.graph.lvnet_reconstruct import LvnetReconstructError, reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests import placement_cases as cases
from tests.structure_diagrams import Views, load
from tests.test_lvnet_identity import _assert_graph_identity


def _views(tmp_path: Path, xml: str, front_panel: str = cases.FRONT_PANEL) -> Views:
    (tmp_path / "T_FPHb.xml").write_text(front_panel)
    return load(tmp_path, xml)


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


_TWO_FRAMES = (
    "{header}"
    "      frame {first} :\n"
    "        read Gain\n"
    "      frame {second} :\n"
    "        Rate = 3\n"
    "    Total = 9"
)


@pytest.mark.parametrize(
    ("kind", "header", "first", "second"),
    [
        ("case", "    case ? (id 600) :\n", '"False"', '"True"'),
        (
            "event",
            "    event-structure (id 4000) :\n",
            '"[0] Timeout"',
            '"[1] Value Change"',
        ),
        ("flat", "    flat-sequence (id 1000) :\n", "[0]", "[1]"),
        ("stacked", "    stacked-sequence (id 2000) :\n", "[0]", "[1]"),
        (
            "disable",
            "    diagram-disable (id 3000) :\n",
            "Enabled, default",
            "Disabled",
        ),
    ],
)
def test_a_terminal_in_a_frame_is_a_line_of_that_frame(
    tmp_path: Path, kind: str, header: str, first: str, second: str
) -> None:
    views = _views(tmp_path, cases.placement_xml(kind))
    expected = _TWO_FRAMES.format(header=header, first=first, second=second)
    assert views.diagram == expected
    _round_trips(views)


@pytest.mark.parametrize(
    ("kind", "header", "tail"),
    [
        (
            "whileLoop",
            "while-loop (stop if true) (id 400) :",
            "      loop_400::condition = ?\n",
        ),
        ("forLoop", "for-loop (id 400) :", ""),
        ("inplace", "in-place-element (id 5000) :", ""),
    ],
)
def test_a_terminal_in_a_single_body_is_a_line_of_that_body(
    tmp_path: Path, kind: str, header: str, tail: str
) -> None:
    views = _views(tmp_path, cases.placement_xml(kind))
    assert views.diagram == (
        f"    {header}\n      read Gain\n      Rate = 3\n{tail}    Total = 9"
    )
    _round_trips(views)


def test_a_terminal_in_a_loop_in_a_frame_is_a_line_of_the_loop(
    tmp_path: Path,
) -> None:
    views = _views(tmp_path, cases.nested_xml())
    assert views.diagram == (
        "    case ? (id 600) :\n"
        '      frame "False" :\n'
        "        read Gain\n"
        '      frame "True" :\n'
        "        while-loop (stop if true) (id 400) :\n"
        "          Rate = 3\n"
        "          loop_400::condition = ?\n"
        "    Total = 9"
    )
    _round_trips(views)


def test_json_gives_each_placed_terminal_its_scope(tmp_path: Path) -> None:
    (tmp_path / "case").mkdir()
    views = _views(tmp_path / "case", cases.placement_xml("case"))
    in_false = {"uid": "600", "frame": "False"}
    in_true = {"uid": "600", "frame": "True"}
    assert [(o["name"], o.get("scope")) for o in views.json["outputs"]] == [
        ("Rate", in_true),
        ("Total", None),
    ]
    assert [
        (t["name"], t.get("scope")) for t in views.json["connector_pane"]["terminals"]
    ] == [("Rate", in_true), ("Total", None), ("Gain", in_false)]
    assert [f["body"] for f in views.json["body"][0]["frames"]] == [[], []]


def test_a_loop_body_has_no_frame_in_json(tmp_path: Path) -> None:
    views = _views(tmp_path, cases.placement_xml("whileLoop"))
    scopes = {
        t["name"]: t.get("scope") for t in views.json["connector_pane"]["terminals"]
    }
    assert scopes == {
        "Rate": {"uid": "400", "frame": None},
        "Total": None,
        "Gain": {"uid": "400", "frame": None},
    }


def test_a_vi_with_no_placed_terminal_prints_what_it_always_did(
    tmp_path: Path,
) -> None:
    views = _views(
        tmp_path,
        cases.case_in_without_terminals(),
    )
    assert views.diagram == (
        "    case ? (id 600) :\n"
        '      frame "False" :\n'
        '      frame "True" :\n'
        "    Total = 9"
    )
    assert "read" not in views.diagram


# ---------------------------------------------------------------------------
# Two terminals with one label
# ---------------------------------------------------------------------------


def _shared(tmp_path: Path, *, indicators: bool) -> Views:
    return _views(
        tmp_path,
        cases.shared_label_xml(indicators=indicators),
        cases.shared_label_front_panel(indicators=indicators),
    )


def test_two_indicators_with_one_label_keep_their_own_sources(tmp_path: Path) -> None:
    views = _shared(tmp_path, indicators=True)
    assert views.lvnet.split("  front-panel :\n")[1].splitlines()[:3] == [
        "    pattern : 4800",
        "    out  x (id 5020) : I32 @0",
        "    out  x (id 5030) : I32 @1",
    ]
    assert views.diagram == (
        "    case ? (id 600) :\n"
        '      frame "False" :\n'
        '      frame "True" :\n'
        "        x (id 5020) = 2\n"
        "    x (id 5030) = 1"
    )
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    assert [
        (o.terminal_uid, o.placed, o.source.lvnet_value)  # type: ignore[union-attr]
        for o in rebuilt.outputs
    ] == [("5020", True, "2"), ("5030", False, "1")]
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert [(o["name"], o.get("scope")) for o in views.json["outputs"]] == [
        ("x", {"uid": "600", "frame": "True"}),
        ("x", None),
    ]


def test_two_controls_with_one_label_say_which_is_read(tmp_path: Path) -> None:
    views = _shared(tmp_path, indicators=False)
    assert views.diagram == (
        "    case ? (id 600) :\n"
        '      frame "False" :\n'
        '      frame "True" :\n'
        "        read x (id 5020)"
    )
    _round_trips(views)
    assert [(i["name"], i.get("scope")) for i in views.json["inputs"]] == [
        ("x", {"uid": "600", "frame": "True"}),
        ("x", None),
    ]


@pytest.mark.parametrize("indicators", [True, False])
def test_terse_text_cannot_say_which_of_two_same_label_terminals(
    tmp_path: Path, indicators: bool
) -> None:
    views = _shared(tmp_path, indicators=indicators)
    with pytest.raises(LvnetReconstructError, match="use verbose text"):
        reconstruct_module(parse_lvnet(views.terse))


def test_an_unshared_label_has_no_identity_in_the_text(tmp_path: Path) -> None:
    views = _views(tmp_path, cases.placement_xml("case"))
    assert "(id 50" not in views.lvnet


def test_a_name_that_ends_like_an_identity_is_quoted(tmp_path: Path) -> None:
    views = _views(
        tmp_path,
        cases.placement_xml("case"),
        cases.FRONT_PANEL.replace('"Rate"', '"Rate (id 7)"'),
    )
    assert '"Rate (id 7)" = 3' in views.lvnet
    _round_trips(views)


def test_a_read_of_no_control_is_an_error() -> None:
    text = (
        "vi V.vi :\n  block-diagram :\n"
        "    for-loop :\n      read Missing\n"
    )
    with pytest.raises(LvnetReconstructError, match="names no control"):
        reconstruct_module(parse_lvnet(text))


def test_read_on_the_vi_diagram_is_an_error() -> None:
    with pytest.raises(LvnetParseError, match="read <Control>"):
        parse_lvnet("vi V.vi :\n  block-diagram :\n    read Gain\n")


_PANE = "vi V.vi :\n  front-panel :\n    in   Gain : I32\n    out  Rate : I32\n"


def test_a_read_after_an_indicator_drive_is_an_error() -> None:
    text = (
        _PANE
        + "  block-diagram :\n    for-loop :\n      Rate = 3\n      read Gain\n"
        + "    Rate = 1\n"
    )
    with pytest.raises(LvnetReconstructError, match="first and its indicator"):
        reconstruct_module(parse_lvnet(text))


def test_a_control_is_read_once_in_a_body() -> None:
    text = (
        _PANE
        + "  block-diagram :\n    for-loop :\n      read Gain\n      read Gain\n"
        + "    Rate = 1\n"
    )
    with pytest.raises(LvnetReconstructError, match="appears twice"):
        reconstruct_module(parse_lvnet(text))


def test_a_terminal_in_a_structure_lvnet_does_not_write_is_an_error() -> None:
    from lvkit.graph.netlist_fp_placement import FrontPanelPlacements, PlacementError
    from lvkit.models import FPTerminal

    placements = FrontPanelPlacements()
    placements.add_control(
        FPTerminal(
            id="/some/dir/T_BDHb.xml::5010",
            index=None,
            direction="input",
            name="Gain",
            parent="/some/dir/T_BDHb.xml::600",
            frame="True",
            is_public=False,
        )
    )
    with pytest.raises(PlacementError) as error:
        placements.check_all_claimed()
    assert str(error.value) == (
        "front-panel terminals Gain sit inside structure 600, frame True, "
        "which lvnet does not write"
    )
