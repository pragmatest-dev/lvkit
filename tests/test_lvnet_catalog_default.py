"""What an unwired input shows as its default (#169): the catalog's documented
value, its documented behaviour, or the bare `default`; a value that is not a value
of the input's type at the call is reported and shown bare.

Each case is a tiny diagram of one primitive with its input left unwired, written as
heap XML (``structure_diagrams.py``): the lvnet text, that it reads back to the same
graph, and the Python the default generates.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests.structure_diagrams import (
    BOOL,
    I32,
    STR,
    Views,
    constant,
    fp_terminal,
    front_panel_xml,
    load,
    one_input_primitive,
    primitive,
    root_diagram,
    srn,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity

ONE_BUTTON_DIALOG = 1340  # input 1 `button name`, default "OK"
ARRAY_OF_STRINGS_TO_PATH = 1422  # input 1 `is_relative`, default FALSE
NUMBER_TO_DECIMAL_STRING = 1180  # inputs 1 `width` (no padding), 2 `number`
MULTIPLY = 1052  # input 2 `x`: NI states no default


def _views(tmp_path: Path, node: str) -> Views:
    return load(tmp_path, root_diagram([], [node], []))


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def test_a_documented_value_is_shown(tmp_path: Path) -> None:
    views = _views(
        tmp_path, one_input_primitive(10, ONE_BUTTON_DIALOG, STR, out_type=BOOL)
    )
    assert '      in   button_name : String default "OK"' in views.lvnet
    _round_trips(views)


def test_a_value_that_is_not_of_the_inputs_type_is_reported_and_shown_bare(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    node = one_input_primitive(10, ARRAY_OF_STRINGS_TO_PATH, I32, out_type=BOOL)
    with caplog.at_level(logging.WARNING):
        views = _views(tmp_path, node)
    assert "      in   1 : I32 default\n" in views.lvnet + "\n"
    assert "not a value of its type I32" in caplog.text
    _round_trips(views)


def test_a_documented_behaviour_is_shown_in_parentheses(tmp_path: Path) -> None:
    node = primitive(
        10, NUMBER_TO_DECIMAL_STRING, [(1, I32), (2, I32)], out_type=STR
    )
    views = _views(tmp_path, node)
    assert " default (no padding)\n" in views.lvnet + "\n"
    _round_trips(views)


def test_an_input_nothing_documents_is_the_bare_default(tmp_path: Path) -> None:
    views = _views(tmp_path, primitive(10, MULTIPLY, [(2, I32)], out_type=I32))
    assert "\n      in   x" in views.lvnet
    assert views.lvnet.rstrip().splitlines()[-2].endswith(" default")
    _round_trips(views)


def test_a_behaviour_the_function_writes_is_executed(tmp_path: Path) -> None:
    """Number To Decimal String with `width` unwired pads nothing."""
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({}, {30: "result"}, pane={0: 30})
    )
    node = primitive(
        10, NUMBER_TO_DECIMAL_STRING, [(1, I32), (2, I32)], out_type=STR
    )
    xml = root_diagram(
        [],
        [srn(800, [constant(701, I32, 7), fp_terminal(5030, 30)]), node],
        [wire(901, 701, 15), wire(902, 11, 5030)],
    )
    views = load(tmp_path, xml)
    context = views.graph.get_vi_context(views.vi)
    source = build_module(context, "T.vi", graph=views.graph)
    namespace: dict[str, object] = {}
    exec(compile(source, "T.py", "exec"), namespace)  # noqa: S102
    assert namespace["t"]().result == "7"  # type: ignore[operator]


def test_an_unwired_input_nothing_documents_is_a_gap_in_the_generated_code(
    tmp_path: Path,
) -> None:
    """Multiply's `x` stays unknown, a gap and not a guess."""
    views = _views(tmp_path, primitive(10, MULTIPLY, [(2, I32)], out_type=I32))
    gaps: list = []
    build_module(
        views.graph.get_vi_context(views.vi), "T.vi", graph=views.graph, gaps=gaps
    )
    assert [g.kind for g in gaps] == ["unknown_default"]
    assert "its default is not known" in gaps[0].reason


def test_get_variant_attribute_with_default_value_unwired_returns_none() -> None:
    """Unwired, `default value` is an empty variant: None in Python."""
    from tests.native_vi import generate, node, scalar

    variant = scalar("Variant")
    name = scalar("String")
    get_attribute = node(
        "ga",
        8205,
        [
            (1, "output", variant),
            (4, "input", None),
            (5, "input", variant),
            (6, "input", name),
            (7, "input", variant),
        ],
    )
    generated = generate(
        {"v": variant, "n": name},
        {"value": variant},
        [get_attribute],
        [("fp:v", "ga:7"), ("fp:n", "ga:6"), ("ga:1", "fp:value")],
    )
    holder = type("Holder", (), {"present": 3})()
    assert generated.function(holder, "present").value == 3  # type: ignore[operator]
    assert generated.function(holder, "absent").value is None  # type: ignore[operator]


def test_a_project_vi_call_shows_the_default_its_uses_entry_shows(
    tmp_path: Path,
) -> None:
    """The called VI's connector pane is the one source: an unwired input of the
    call shows the value of the callee's control, the same value its `uses :`
    entry shows; a callee control with no saved default shows the bare default."""
    from lvkit.graph.models import VINode
    from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
    from tests import vi_reference_diagrams as d

    number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    loaded = d.load(tmp_path, [], [])
    graph, caller, callee = loaded.graph, loaded.caller, loaded.callee
    callee_node = graph._graph.nodes[callee]["node"]  # noqa: SLF001
    callee_node.terminals = [
        FPTerminal(
            id="x", index=0, direction="input", name="x", lv_type=number,
            is_public=True, default_value="7",
        ),
        FPTerminal(
            id="z", index=1, direction="input", name="z", lv_type=number,
            is_public=True,
        ),
    ]
    call = VINode(
        id=f"{caller}::50",
        vi_path=caller,
        name="Callee.vi",
        node_type="iUse",
        callee_key=callee,
        terminals=[
            Terminal(id=f"{caller}::51", index=0, direction="input", lv_type=number),
            Terminal(id=f"{caller}::52", index=1, direction="input", lv_type=number),
        ],
    )
    graph._graph.add_node(call.id, node=call)  # noqa: SLF001
    graph._vi_nodes[caller].add(call.id)  # noqa: SLF001
    for terminal in call.terminals:
        graph._term_to_node[terminal.id] = call.id  # noqa: SLF001
    text = render_lvnet(build_netlist_from_graph(graph, caller), verbose=True)
    assert "      in   x : I32 default 7\n" in text + "\n"
    assert "      in   z : I32 default\n" in text + "\n"
    assert "    in   x : I32 default 7" in text


def test_simple_error_handler_inputs_sit_at_the_slots_its_calls_wire() -> None:
    """Calls wire an I32 at 3, a String at 4, a U16 at 8 and an error cluster at 9:
    error code, error source, type of dialog, error in."""
    from lvkit.vilib_resolver import get_resolver

    entry = get_resolver().resolve_by_name("Simple Error Handler.vi")
    assert entry is not None
    inputs = {t.index: t.name for t in entry.terminals if t.direction == "in"}
    assert inputs == {
        3: "error code (no error:0)",
        4: 'error source (" ")',
        8: "type of dialog (OK msg:1)",
        9: "error in (no error)",
    }
    dialog = next(t for t in entry.terminals if t.index == 8)
    assert (dialog.type, dialog.default) == ("NumUInt16", 1)
