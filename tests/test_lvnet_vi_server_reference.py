"""A Control Reference constant is a ``vi-server-reference`` in lvnet, JSON and
describe, not a local-variable read (#160).

Each case is a tiny diagram of just the constant, written as heap XML
(``structure_diagrams.py``): the type's ``CtlFlags`` and the constant's ``ddo`` are
decoded by the parser, then lvnet, JSON and describe are asserted, and the text is
read back to the same graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.lvnet_parse import LvnetParseError, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import LocalVariableNode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.reference_target import reference_phrase, reference_target
from tests.structure_diagrams import (
    APPLICATION_TYPE,
    CONTROL_TYPE,
    REFERENCE_TYPES,
    THIS_VI_TYPE,
    TYPES,
    Views,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    reference_constant,
    root_diagram,
    srn,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity

_TYPES = TYPES + REFERENCE_TYPES
_CONTROL_DDO = 101  # the ddo of front-panel control 10


def _views(
    tmp_path: Path,
    label: str,
    type_id: int,
    *,
    control: str | None = None,
    read: bool = False,
) -> Views:
    """A diagram of one reference constant (and, with ``control``, the
    front-panel control it references; with ``read``, an Increment reading it)."""
    nodes = [srn(800, [fp_terminal(5010, 10)])] if control else []
    if control:
        (tmp_path / "T_FPHb.xml").write_text(front_panel_xml({10: control}, {}))
    ddo = _CONTROL_DDO if control else None
    structures = [reference_constant(80, label, type_id, ddo)]
    wires = []
    if read:
        structures.append(increment(810))
        wires.append(wire(901, 81, 813))
    return load(tmp_path, root_diagram(structures, nodes, wires), _TYPES)


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def test_this_vi_names_the_vi_by_its_decoded_class(tmp_path: Path) -> None:
    views = _views(tmp_path, "This VI", THIS_VI_TYPE, read=True)
    assert views.diagram == (
        "    vi-server-reference This_VI_80 : This VI\n"
        "    function Increment_810 : Increment\n"
        "      in   x   : I32 = This_VI_80::This VI\n"
        "      out  x+1 : I32"
    )
    _round_trips(views)


def test_this_application_is_told_from_this_vi_by_its_type(tmp_path: Path) -> None:
    views = _views(tmp_path, "AppRef", APPLICATION_TYPE)
    assert views.diagram == "    vi-server-reference AppRef_80 : This Application"
    _round_trips(views)


def test_a_label_is_not_the_target(tmp_path: Path) -> None:
    """A constant labelled "Current VI Reference" whose type names the VI class is
    This VI; one labelled "This VI" whose type names no class is not."""
    (tmp_path / "a").mkdir()
    named = _views(tmp_path / "a", "Current VI Reference", THIS_VI_TYPE)
    assert named.diagram == "    vi-server-reference Current_VI_Reference_80 : This VI"
    (tmp_path / "b").mkdir()
    plain = _views(tmp_path / "b", "This VI", CONTROL_TYPE)
    assert plain.diagram == "    vi-server-reference This_VI_80 : ?"
    _round_trips(plain)


def test_a_control_reference_names_the_control_not_its_label(tmp_path: Path) -> None:
    views = _views(tmp_path, "channel list", CONTROL_TYPE, control="Table")
    assert views.diagram == "    vi-server-reference channel_list_80 : Table"
    _round_trips(views)


def test_a_control_named_like_a_built_in_target_is_quoted(tmp_path: Path) -> None:
    views = _views(tmp_path, "Ref", CONTROL_TYPE, control="This VI")
    assert views.diagram == '    vi-server-reference Ref_80 : "This VI"'
    _round_trips(views)
    instance = reconstruct_module(parse_lvnet(views.lvnet)).body[0]
    assert instance.reference.control == "This VI"  # type: ignore[union-attr]


def test_json_tags_the_instance_with_its_target(tmp_path: Path) -> None:
    (tmp_path / "v").mkdir()
    this_vi = _views(tmp_path / "v", "This VI", THIS_VI_TYPE)
    (tmp_path / "c").mkdir()
    control = _views(tmp_path / "c", "Tree", CONTROL_TYPE, control="Tree")
    (tmp_path / "u").mkdir()
    unknown = _views(tmp_path / "u", "Ref", CONTROL_TYPE)
    assert [
        [(i["kind"], i["target"], i["control"]) for i in v.json["body"]]
        for v in (this_vi, control, unknown)
    ] == [
        [("vi_server_reference", "This VI", None)],
        [("vi_server_reference", "control", "Tree")],
        [("vi_server_reference", None, None)],
    ]


def test_describe_names_a_reference_by_what_it_references(tmp_path: Path) -> None:
    views = _views(tmp_path, "channel list", CONTROL_TYPE, control="Table")
    node = views.graph.get_graph_node(f"{views.vi}::80")
    assert isinstance(node, LocalVariableNode)
    assert reference_phrase(reference_target(views.graph, node)) == (
        "Table control reference"
    )


def test_the_line_needs_a_handle_and_a_target() -> None:
    base = "vi Refs.vi :\n  block-diagram :\n    "
    with pytest.raises(LvnetParseError, match="<handle> : <target>"):
        parse_lvnet(base + "vi-server-reference This_VI_1")


def test_two_controls_with_one_label_say_which_is_referenced(tmp_path: Path) -> None:
    """Controls 10 and 20 are both labelled ``Tree``; the constant references the
    second (ddo 201), which the verbose text names by its terminal."""
    (tmp_path / "T_FPHb.xml").write_text(front_panel_xml({10: "Tree", 20: "Tree"}, {}))
    xml = root_diagram(
        [reference_constant(80, "Ref", CONTROL_TYPE, 201)],
        [srn(800, [fp_terminal(5010, 10), fp_terminal(5020, 20)])],
        [],
    )
    views = load(tmp_path, xml, _TYPES)
    assert views.diagram == "    vi-server-reference Ref_80 : Tree (id 5020)"
    assert views.json["body"][0]["control_uid"] == "5020"
    _round_trips(views)
    instance = reconstruct_module(parse_lvnet(views.lvnet)).body[0]
    assert instance.reference.control_uid == "5020"  # type: ignore[union-attr]
