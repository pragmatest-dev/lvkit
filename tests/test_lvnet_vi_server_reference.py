"""A Control Reference constant is a ``vi-server-reference`` in lvnet, JSON and
describe, not a local-variable read (#160).

Each case is a tiny diagram of just the constant (``vi_reference_diagrams.py``):
what lvnet and JSON print for it, and that the text reads back to the same graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.lvnet_parse import LvnetParseError, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import LocalVariableNode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.reference_target import reference_phrase, reference_target
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests import vi_reference_diagrams as d
from tests.test_lvnet_identity import _assert_graph_identity

_OBJECT_CONTROL = LVType(
    kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="LVObjCtl"
)


def _constant(loaded: d.Loaded, uid: int) -> LocalVariableNode:
    node = loaded.graph.get_graph_node(f"{loaded.caller}::{uid}")
    assert isinstance(node, LocalVariableNode)
    return node


def _reference_to_control(tmp_path: Path, label: str, control: str) -> d.Loaded:
    """A reference constant labelled ``label`` whose ddo resolved to the
    front-panel control ``control``."""
    loaded = d.load(tmp_path, [d.reference_constant(80, label)], [])
    d.type_terminal(loaded, 81, _OBJECT_CONTROL)
    terminal = FPTerminal(
        id=f"{loaded.caller}::fp",
        index=0,
        direction="input",
        name=control,
        lv_type=_OBJECT_CONTROL,
        is_public=True,
    )
    vi_node = loaded.graph._graph.nodes[loaded.caller]["node"]  # noqa: SLF001
    vi_node.terminals = [*vi_node.terminals, terminal]
    loaded.graph._term_to_node[terminal.id] = loaded.caller  # noqa: SLF001
    _constant(loaded, 80).control_terminal_id = terminal.id
    return loaded


def _text(loaded: d.Loaded) -> str:
    module = build_netlist_from_graph(loaded.graph, loaded.caller)
    return render_lvnet(module, display_name="Refs.vi", verbose=True)


def _diagram(text: str) -> str:
    return text.split("  block-diagram :\n", 1)[1]


def test_this_vi_names_the_vi_by_its_decoded_class(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, [d.this_vi(80)], [])
    d.type_terminal(loaded, 81, d.this_vi_type())
    assert _diagram(_text(loaded)) == "    vi-server-reference This_VI_80 : This VI"


def test_this_application_is_told_from_this_vi_by_its_type(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, [d.reference_constant(80, "AppRef")], [])
    d.type_terminal(loaded, 81, d.application_type())
    assert _diagram(_text(loaded)) == (
        "    vi-server-reference AppRef_80 : This Application"
    )


def test_a_label_is_not_the_target(tmp_path: Path) -> None:
    """A constant labelled "Current VI Reference" whose type names the VI class
    is This VI; one labelled "This VI" whose type does not is not."""
    loaded = d.load(tmp_path, [d.reference_constant(80, "Current VI Reference")], [])
    d.type_terminal(loaded, 81, d.this_vi_type())
    assert _diagram(_text(loaded)) == (
        "    vi-server-reference Current_VI_Reference_80 : This VI"
    )
    loaded = d.load(tmp_path, [d.this_vi(80)], [])
    d.type_terminal(loaded, 81, _OBJECT_CONTROL)
    assert _diagram(_text(loaded)) == "    vi-server-reference This_VI_80 : ?"


def test_a_control_reference_names_the_control_not_its_label(tmp_path: Path) -> None:
    loaded = _reference_to_control(tmp_path, "channel list", "Table")
    assert _diagram(_text(loaded)) == "    vi-server-reference channel_list_80 : Table"


def test_a_control_named_like_a_built_in_target_is_quoted(tmp_path: Path) -> None:
    loaded = _reference_to_control(tmp_path, "Ref", "This VI")
    text = _text(loaded)
    assert _diagram(text) == '    vi-server-reference Ref_80 : "This VI"'
    rebuilt = reconstruct_module(parse_lvnet(text))
    instance = rebuilt.body[0]
    assert instance.reference.control == "This VI"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "case",
    ["this_vi", "application", "control", "unknown"],
)
def test_every_target_reads_back_to_the_same_graph(tmp_path: Path, case: str) -> None:
    if case == "control":
        loaded = _reference_to_control(tmp_path, "Tree", "Tree")
    else:
        loaded = d.load(tmp_path, [d.reference_constant(80, "Ref")], [])
        d.type_terminal(
            loaded,
            81,
            {
                "this_vi": d.this_vi_type(),
                "application": d.application_type(),
                "unknown": _OBJECT_CONTROL,
            }[case],
        )
    module = build_netlist_from_graph(loaded.graph, loaded.caller)
    text = render_lvnet(module, display_name="Refs.vi", verbose=True)
    rebuilt = reconstruct_module(parse_lvnet(text))
    _assert_graph_identity(module, rebuilt, "Refs.vi")
    assert render_lvnet(rebuilt, display_name="Refs.vi", verbose=True) == text


def test_json_tags_the_instance_with_its_target(tmp_path: Path) -> None:
    this_vi = d.load(tmp_path, [d.this_vi(80)], [])
    d.type_terminal(this_vi, 81, d.this_vi_type())
    body = netlist_to_dict(build_netlist_from_graph(this_vi.graph, this_vi.caller))[
        "body"
    ]
    assert [(i["kind"], i["target"], i["control"]) for i in body] == [
        ("vi_server_reference", "This VI", None)
    ]
    (tmp_path / "c").mkdir()
    control = _reference_to_control(tmp_path / "c", "Tree", "Tree")
    body = netlist_to_dict(build_netlist_from_graph(control.graph, control.caller))[
        "body"
    ]
    assert [(i["kind"], i["target"], i["control"]) for i in body] == [
        ("vi_server_reference", "control", "Tree")
    ]


def test_describe_names_a_reference_by_what_it_references(tmp_path: Path) -> None:
    loaded = _reference_to_control(tmp_path, "Tree", "Tree")
    node = _constant(loaded, 80)
    assert reference_phrase(reference_target(loaded.graph, node)) == (
        "Tree control reference"
    )
    (tmp_path / "v").mkdir()
    this_vi = d.load(tmp_path / "v", [d.this_vi(80)], [])
    d.type_terminal(this_vi, 81, d.this_vi_type())
    target = reference_target(this_vi.graph, _constant(this_vi, 80))
    assert reference_phrase(target) == "This VI reference"


def test_the_line_needs_a_handle_and_a_target() -> None:
    base = "vi Refs.vi :\n  block-diagram :\n    "
    with pytest.raises(LvnetParseError, match="<handle> : <target>"):
        parse_lvnet(base + "vi-server-reference This_VI_1")
