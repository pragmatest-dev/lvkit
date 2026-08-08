"""Event Structure codegen emits an explicit, self-explaining raise -- never a
silent no-op. An event structure is an asynchronous UI event loop with no
headless runtime analog, so codegen must fail loudly (and name the events)
rather than dropping the VI's event behaviour behind a warning comment."""
from __future__ import annotations

import ast

import pytest

from lvkit.codegen.nodes import generate
from lvkit.models import EventFrame, EventOperation
from tests.helpers import make_ctx


def _event_node(labels: list[str]) -> EventOperation:
    return EventOperation(
        id="es1",
        name="Event Structure",
        kind="eventStruct",
        frames=[EventFrame(event_label=label) for label in labels],
    )


def _source(node: EventOperation) -> str:
    frag = generate(node, make_ctx())
    return "\n".join(ast.unparse(s) for s in frag.statements)


def test_event_structure_emits_explicit_raise_not_silent_comment():
    src = _source(
        _event_node(['[0] Timeout', '[3] "copyrights": Value Change'])
    )
    assert "raise NotImplementedError" in src
    assert "Event Structure not supported" in src
    # the old silent fallback is gone
    assert "WARNING: Unknown node" not in src


def test_event_stub_names_the_registered_events():
    src = _source(
        _event_node(['[0] Timeout', '[3] "copyrights": Value Change'])
    )
    assert "Timeout" in src
    assert "copyrights" in src


def test_event_stub_raises_when_the_generated_code_runs():
    node = _event_node(['[0] Timeout'])
    frag = generate(node, make_ctx())
    module = ast.fix_missing_locations(
        ast.Module(body=frag.statements, type_ignores=[])
    )
    code = compile(module, "<event>", "exec")
    with pytest.raises(NotImplementedError, match="Event Structure not supported"):
        exec(code, {})
