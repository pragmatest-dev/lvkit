"""``MechAction`` of a Boolean, a property the file saves and NI says a running VI
cannot write (``control_properties.json`` ``settable_while_running``): the parser
reads a Boolean's Mechanical Action from its ``ddo`` ``objFlags``, a read of a Boolean
reference or of the VI's own Boolean gives it, and a write is a gap. Each case is a
tiny diagram whose generated package is imported and run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.lvnet_parse import ParsedLvnet, netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import GraphNode, VIContext
from lvkit.graph.netlist import render_lvnet
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.parser.flags import MECHANICAL_ACTION_NAMES, mechanical_action
from tests.class_graphs import INT, Project, pane, wire
from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Vi,
)
from tests.error_diagrams import Project as XmlProject
from tests.error_diagrams import project as xml_project  # noqa: F401
from tests.panel_diagrams import value_property
from tests.test_control_references import REFNUM, property_node
from tests.test_front_panel_reference import ERROR

MECH_CODE = (104019976,)
BOOLEAN = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
U16 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16")

UNTIL_RELEASED = 0x10000
LATCH = 0x20000
RELEASED = 0x40000
NAME = "Mech.vi"


@pytest.mark.parametrize(
    ("flags", "action"),
    [
        (0, 0),
        (RELEASED, 1),
        (UNTIL_RELEASED, 2),
        (LATCH, 3),
        (LATCH | RELEASED, 4),
        (LATCH | UNTIL_RELEASED, 5),
        (UNTIL_RELEASED | RELEASED, None),
        (0x8 | 1 | LATCH | RELEASED, 4),
    ],
)
def test_the_flags_decode_to_nis_mechanical_action(
    flags: int, action: int | None
) -> None:
    assert mechanical_action(flags) == action


def test_the_names_are_nis_order() -> None:
    assert MECHANICAL_ACTION_NAMES[4] == "Latch When Released"
    assert MECHANICAL_ACTION_NAMES[2] == "Switch Until Released"
    assert len(MECHANICAL_ACTION_NAMES) == 6


def test_the_parser_reads_the_action_and_describe_shows_it(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    vi.control("plain", BOOL, REQUIRED, ddo_class="stdBool")
    vi.control("stop", BOOL, REQUIRED, ddo_class="stdBool", ddo_flags=LATCH | RELEASED)
    vi.control("n", I32, REQUIRED, ddo_flags=LATCH | RELEASED)
    vi.indicator("out", I32)
    xml_project.add(vi)
    views = xml_project.views(NAME)
    graph = views.graph
    actions = {
        t.name: t.mechanical_action
        for t in graph.get_vi_context(graph.list_vis()[0]).inputs
        if isinstance(t, FPTerminal)
    }
    # only a Boolean control has one
    assert actions == {"plain": 0, "stop": 4, "n": None}
    inputs = views.describe.split("## Inputs\n", 1)[1].split("\n\n")[0]
    assert inputs.splitlines() == [
        "  plain: TF (required)",
        "  stop: TF (required) (latch when released)",
        "  n: I32 (required)",
    ]


def test_json_and_lvnet_carry_the_action_and_the_round_trip_keeps_it(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    vi.control("plain", BOOL, REQUIRED, ddo_class="stdBool")
    vi.control("stop", BOOL, REQUIRED, ddo_class="stdBool", ddo_flags=LATCH | RELEASED)
    vi.control("go", BOOL, REQUIRED, ddo_class="stdBool", ddo_flags=RELEASED | 0x8)
    vi.control("n", I32, REQUIRED, ddo_flags=LATCH | RELEASED)
    vi.indicator("out", I32)
    xml_project.add(vi)
    views = xml_project.views(NAME)
    terminals = views.json["connector_pane"]["terminals"]
    assert [(t["name"], t["mechanical_action"]) for t in terminals] == [
        ("plain", 0),
        ("stop", 4),
        ("go", 1),
        ("n", 0),
        ("out", 0),
    ]
    rows = {
        line.split(" : ")[0].split()[1]: line.split(" : ", 1)[1]
        for line in views.lvnet.split("  block-diagram :")[0].splitlines()
        if line.strip().startswith(("in ", "out ")) and " : " in line
    }
    assert rows == {
        "plain": "TF  required @0",
        "stop": "TF  required mech-latch-when-released @1",
        "go": "TF  required starts-hidden mech-switch-when-released @2",
        "n": "I32 required @3",
        "out": "I32 recommended @4",
    }
    assert "mech-" not in views.terse
    parsed = parse_lvnet(views.lvnet)
    assert isinstance(parsed, ParsedLvnet)
    assert {t.name: t.mechanical_action for t in parsed.boundary} == {
        "plain": 0,
        "stop": 4,
        "go": 1,
        "n": 0,
        "out": 0,
    }
    rebuilt = reconstruct_module(parsed)
    assert render_lvnet(rebuilt, display_name=NAME, verbose=True) == views.lvnet
    assert netlist_signature(rebuilt) == netlist_signature(parsed)


def test_a_read_of_the_vis_own_boolean_gives_its_action(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    stop = vi.control(
        "stop", BOOL, REQUIRED, ddo_class="stdBool", ddo_flags=LATCH | RELEASED
    )
    seen = vi.indicator("seen", I32)
    (mech,) = value_property(
        vi, stop, [("MechAction", MECH_CODE[0])], class_name="Bool"
    )
    vi.wire(mech, seen)
    xml_project.add(vi)
    package = xml_project.generate(NAME)
    assert package.gaps == []
    assert package.function(stop=False).seen == 4


def target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI with a plain Boolean ``plain``, a latch-when-released Boolean ``stop`` and
    a number ``n``."""

    def boolean(tag: str, index: int, action: int) -> FPTerminal:
        terminal = pane(
            vi,
            "input",
            tag,
            BOOLEAN,
            tag,
            index,
            tab_index=index,
            control_type="stdBool",
        )
        return terminal.model_copy(update={"mechanical_action": action})

    return (
        VIContext(
            name=vi,
            inputs=[
                boolean("plain", 0, 0),
                boolean("stop", 1, 4),
                pane(vi, "input", "n", INT, "n", 2, tab_index=2, control_type="stdNum"),
            ],
            outputs=[pane(vi, "output", "out", BOOLEAN, "out", 3, tab_index=3)],
            data_flow=[wire(vi, "plain", "out")],
        ),
        [],
    )


def reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that reads the MechAction of the Boolean reference it is given."""
    node = property_node(vi, "ctl", "Bool", [("mech", MECH_CODE, "output", U16)])
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "mech", U16, "mech", 1),
                pane(vi, "output", "err", ERROR, "error out", 2),
            ],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "ctl.mech", "mech"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def writer(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that writes the MechAction of the Boolean reference it is given."""
    node = property_node(vi, "ctl", "Bool", [("mech", MECH_CODE, "input", U16)])
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                pane(vi, "input", "mech", U16, "mech", 1),
            ],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "mech", "ctl.mech"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def project_with(tmp_path: Path) -> Project:
    project = Project(tmp_path)
    project.add_vi("Target", target)
    project.add_vi("Reader", reader)
    project.add_vi("Writer", writer)
    return project


def by_label(module: Any) -> dict[str, Any]:
    return {c.label: c for c in module("target").target.panel.controls}


def test_only_a_non_default_action_is_in_the_panel_and_a_read_gives_either(
    tmp_path: Path,
) -> None:
    package = project_with(tmp_path).generate([])
    source = package.sources["target"]
    assert "saved={'stop': {'mech_action': 4}}" in source
    assert ".get_property('mech_action')" in package.sources["reader"]
    with package.use() as module:
        controls = by_label(module)
        stop = module("reader").reader(ctl=controls["stop"])
        assert stop.error_out is None
        assert stop.mech == 4
        # a Boolean the panel lists no action for is Switch When Pressed
        assert module("reader").reader(ctl=controls["plain"]).mech == 0


def test_a_control_that_is_not_a_boolean_has_no_action(tmp_path: Path) -> None:
    package = project_with(tmp_path).generate([])
    with package.use() as module:
        assert module("reader").reader(ctl=by_label(module)["n"]).error_out.code == 1026


def test_a_write_is_a_gap_that_quotes_nis_rule(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path).generate(gaps)
    reasons = [g.reason for g in gaps]
    assert len(reasons) == 1
    assert "Property 'p104019976' on a Bool reference is written" in reasons[0]
    assert "cannot be set while the VI runs" in reasons[0]
    with package.use() as module:
        stop = by_label(module)["stop"]
        with pytest.raises(NotImplementedError, match="cannot be set while the VI"):
            module("writer").writer(ctl=stop, mech=1)
