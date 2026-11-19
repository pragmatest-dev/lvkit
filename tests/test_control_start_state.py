"""A Property Node that reads ``Visible`` or ``Disabled`` of a control of its own VI
gives the control's last written state, else the state the VI file stores for it
(``parser/flags.py``, ``codegen/control_property_state.py``). Each case is a tiny
diagram whose front panel stores the control's ``ddo`` ``objFlags``, shown in
describe, generated and called."""

from __future__ import annotations

import pytest

from lvkit.graph.lvnet_parse import ParsedLvnet, netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import render_lvnet
from lvkit.models import FPTerminal
from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.live_io_recorder import Recorder
from tests.panel_diagrams import value_property

NAME = "Prop.vi"

HIDDEN = 0x8
DISABLED = 0x200
GREYED_OUT = 0x80


def _read_vi(row: str | tuple[str, int], flags: int, *, as_bool: bool = True) -> Vi:
    """The row ``row`` of the control ``n`` (with ddo flags ``flags``) is read
    into the indicator ``seen``."""
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED, ddo_flags=flags)
    seen = (
        vi.indicator("seen", BOOL, ddo_class="stdBool")
        if as_bool
        else vi.indicator("seen", I32)
    )
    (state,) = value_property(vi, n, [row])
    vi.wire(state, seen)
    return vi


@pytest.mark.parametrize(
    ("flags", "expected"), [(0, True), (HIDDEN, False), (HIDDEN | DISABLED, False)]
)
def test_a_visible_read_gives_the_stored_state(
    flags: int,
    expected: bool,
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_vi("Visible", flags))
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(n=1).seen is expected


@pytest.mark.parametrize(
    "row", ["DisabledEnum", ("Disabled", 104013825)], ids=["new", "old"]
)
@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (0, 0),
        (HIDDEN, 0),
        (DISABLED, 1),
        (DISABLED | HIDDEN, 1),
        (DISABLED | GREYED_OUT, 2),
        (DISABLED | GREYED_OUT | HIDDEN, 2),
    ],
)
def test_a_disabled_read_gives_the_stored_state(
    row: str | tuple[str, int],
    flags: int,
    expected: int,
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_vi(row, flags, as_bool=False))
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(n=1).seen == expected


def test_a_greyed_bit_without_the_disabled_bit_stays_a_gap(
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_vi("DisabledEnum", GREYED_OUT, as_bool=False))
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "reads 'DisabledEnum', and the file does not record" in str(package.gaps[0])


def test_a_property_with_no_stored_state_stays_a_gap(
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_vi("Key Focus", 0))
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "reads 'Key Focus', and the file does not record" in str(package.gaps[0])


def _read_then_write(*, write_first: bool, flags: int) -> Vi:
    """One node on ``n``: a Visible row written True, and a Visible row read into
    ``seen``, in the order of ``write_first``."""
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED, ddo_flags=flags)
    seen = vi.indicator("seen", BOOL, ddo_class="stdBool")
    rows = ["Visible", "Visible"]
    first, second = value_property(vi, n, rows)
    write, read = (first, second) if write_first else (second, first)
    vi.wire(vi.bool_constant(True), write)
    vi.wire(read, seen)
    return vi


def test_a_read_after_a_write_gives_the_written_state(
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_then_write(write_first=True, flags=HIDDEN))
    package = project.generate(NAME)
    assert package.gaps == []
    recorder = Recorder()
    assert package.function(n=1, live_io=recorder).seen is True
    assert recorder.properties == [("n", package.module.PropProperty.VISIBLE, True)]


def test_a_read_before_a_write_gives_the_stored_state_then_the_next_call_sees_the_write(
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_then_write(write_first=False, flags=HIDDEN))
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(n=1).seen is False
    # The control keeps the state the write gave it from one call to the next.
    assert package.function(n=1).seen is True


def test_describe_shows_how_a_control_starts(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    vi.control("shown", I32, REQUIRED)
    vi.control("hidden", I32, REQUIRED, ddo_flags=HIDDEN)
    vi.control("greyed", I32, REQUIRED, ddo_flags=DISABLED | GREYED_OUT)
    vi.indicator("out", I32)
    project.add(vi)
    inputs = project.views(NAME).describe.split("## Inputs\n", 1)[1].split("\n\n")[0]
    assert inputs.splitlines() == [
        "  shown: I32 (required)",
        "  hidden: I32 (required) (hidden)",
        "  greyed: I32 (required) (disabled and grayed out)",
    ]


def test_the_graph_records_the_stored_state(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    vi.control("shown", I32, REQUIRED)
    vi.control("hidden", I32, REQUIRED, ddo_flags=HIDDEN)
    vi.control("greyed", I32, REQUIRED, ddo_flags=DISABLED | GREYED_OUT)
    vi.control("open", I32, REQUIRED, ddo_flags=DISABLED)
    vi.indicator("out", I32)
    project.add(vi)
    graph = project.views(NAME).graph
    key = graph.list_vis()[0]
    states = {
        t.name: (t.starts_hidden, t.enable_state)
        for t in graph.get_vi_context(key).inputs
        if isinstance(t, FPTerminal)
    }
    assert states == {
        "shown": (False, 0),
        "hidden": (True, 0),
        "greyed": (False, 2),
        "open": (False, 1),
    }


def _states_vi() -> Vi:
    vi = Vi(NAME)
    vi.control("shown", I32, REQUIRED)
    vi.control("hidden", I32, REQUIRED, ddo_flags=HIDDEN)
    vi.control("open", I32, REQUIRED, ddo_flags=DISABLED)
    vi.control("greyed", I32, REQUIRED, ddo_flags=DISABLED | GREYED_OUT)
    vi.control("both", I32, REQUIRED, ddo_flags=HIDDEN | DISABLED | GREYED_OUT)
    vi.indicator("out", I32)
    return vi


def test_describe_shows_a_disabled_control(
    project: Project,  # noqa: F811
) -> None:
    project.add(_states_vi())
    inputs = project.views(NAME).describe.split("## Inputs\n", 1)[1].split("\n\n")[0]
    assert inputs.splitlines() == [
        "  shown: I32 (required)",
        "  hidden: I32 (required) (hidden)",
        "  open: I32 (required) (disabled)",
        "  greyed: I32 (required) (disabled and grayed out)",
        "  both: I32 (required) (hidden) (disabled and grayed out)",
    ]


def test_json_carries_the_start_state(
    project: Project,  # noqa: F811
) -> None:
    project.add(_states_vi())
    terminals = project.views(NAME).json["connector_pane"]["terminals"]
    assert [
        (t["name"], t["starts_hidden"], t["enable_state"]) for t in terminals
    ] == [
        ("shown", False, 0),
        ("hidden", True, 0),
        ("open", False, 1),
        ("greyed", False, 2),
        ("both", True, 2),
        ("out", False, 0),
    ]


def test_lvnet_writes_the_start_state_and_the_round_trip_keeps_it(
    project: Project,  # noqa: F811
) -> None:
    project.add(_states_vi())
    views = project.views(NAME)
    rows = {
        line.split(" : ")[0].split()[1]: line.split(" : ", 1)[1]
        for line in views.lvnet.split("  block-diagram :")[0].splitlines()
        if line.strip().startswith(("in ", "out ")) and " : " in line
    }
    assert rows == {
        "shown": "I32 required @0",
        "hidden": "I32 required starts-hidden @1",
        "open": "I32 required starts-disabled @2",
        "greyed": "I32 required starts-grayed-out @3",
        "both": "I32 required starts-hidden starts-grayed-out @4",
        "out": "I32 recommended @5",
    }
    assert "starts-" not in views.terse
    parsed = parse_lvnet(views.lvnet)
    assert isinstance(parsed, ParsedLvnet)
    assert {t.name: (t.starts_hidden, t.enable_state) for t in parsed.boundary} == {
        "shown": (False, 0),
        "hidden": (True, 0),
        "open": (False, 1),
        "greyed": (False, 2),
        "both": (True, 2),
        "out": (False, 0),
    }
    rebuilt = reconstruct_module(parsed)
    assert render_lvnet(rebuilt, display_name=NAME, verbose=True) == views.lvnet
    assert netlist_signature(rebuilt) == netlist_signature(parsed)
