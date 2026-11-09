"""An Event Structure with an event the generated code does not handle is a gap
that names the event: the code raises where the structure would run."""

from __future__ import annotations

import ast

import pytest

from lvkit.codegen.nodes import generate
from lvkit.codegen.panel_hooks import PanelHooks
from lvkit.graph.models import EventStructureNode
from lvkit.models import EventFrame, FrameEvent
from tests.helpers import make_ctx

TIMEOUT = 1073741825
MOUSE_DOWN = 1073741842
UNCONFIRMED = 0x400000FF


def _event_node(frames: list[list[int]]) -> EventStructureNode:
    return EventStructureNode(
        id="es1",
        vi_path="test.vi",
        name="Event Structure",
        frames=[
            EventFrame(index=i, events=[FrameEvent(type_code=c) for c in codes])
            for i, codes in enumerate(frames)
        ],
    )


def _source(node: EventStructureNode) -> str:
    ctx = make_ctx()
    ctx.panel_hooks = PanelHooks("Value", {}, {}, (), (), event_enum_name="Event")
    frag = generate(node, ctx)
    return "\n".join(ast.unparse(s) for s in frag.statements)


def test_an_event_that_is_not_handled_is_named_by_its_kind() -> None:
    source = _source(_event_node([[TIMEOUT], [MOUSE_DOWN]]))
    assert "raise NotImplementedError" in source
    assert "also handles Mouse Down" in source


def test_an_event_with_no_name_is_named_by_its_code() -> None:
    assert "event code 0x400000FF" in _source(_event_node([[UNCONFIRMED]]))


def test_a_frame_that_lists_no_event_is_a_gap() -> None:
    assert "The frame lists no event" in _source(_event_node([[]]))


def test_a_frame_that_handles_timeout_and_another_event_is_a_gap() -> None:
    source = _source(_event_node([[TIMEOUT, 1073741826]]))
    assert "handles Timeout and other events" in source


def test_the_gap_code_raises_when_it_runs() -> None:
    node = _event_node([[MOUSE_DOWN]])
    ctx = make_ctx()
    ctx.panel_hooks = PanelHooks("Value", {}, {}, (), (), event_enum_name="Event")
    module = ast.fix_missing_locations(
        ast.Module(body=generate(node, ctx).statements, type_ignores=[])
    )
    with pytest.raises(NotImplementedError, match="Mouse Down"):
        exec(compile(module, "<event>", "exec"), {})
