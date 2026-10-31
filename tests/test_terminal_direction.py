"""One terminal-direction value set for graph terminals and catalog terminals,
and describe's ``## Dependencies`` ports of a catalog-only dependency read from
it in the catalog's connector-pane order."""

from __future__ import annotations

from lvkit.graph.op_walk import _subvi_ports
from lvkit.models import TerminalDirection
from lvkit.primitive_resolver.models import PrimitiveTerminal
from lvkit.vilib_resolver import VITerminal, get_resolver


def test_the_direction_is_its_raw_value() -> None:
    assert TerminalDirection.INPUT == "input"
    assert f"{TerminalDirection.OUTPUT}" == "output"
    assert TerminalDirection.of(True) is TerminalDirection.OUTPUT
    assert TerminalDirection.of(False) is TerminalDirection.INPUT


def test_catalog_terminals_read_the_same_values() -> None:
    assert VITerminal(name="x", direction="input").direction is TerminalDirection.INPUT
    assert (
        PrimitiveTerminal(index=0, direction="output").direction
        is TerminalDirection.OUTPUT
    )


def test_a_catalog_only_dependency_shows_its_ports_in_pane_order() -> None:
    """``DAQmx Start Task.vi`` is not loaded; describe reads its catalog entry."""
    recorded = "<vilib>/DAQmx/configure/task.llb/DAQmx Start Task.vi"
    entry = get_resolver().resolve_recorded(recorded)
    assert entry is not None
    ports = _subvi_ports(None, None, recorded)  # type: ignore[arg-type]
    assert ports is not None
    ins, outs = ports
    assert [p.name for p in ins] == [
        t.name
        for t in sorted(entry.terminals, key=lambda t: t.index or 0)
        if t.direction is TerminalDirection.INPUT
    ]
    assert [(p.name, p.type) for p in ins] == [("task in", "Refnum")]
    assert [(p.name, p.type) for p in outs] == [("task out", "Refnum")]
