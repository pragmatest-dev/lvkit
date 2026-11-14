"""An Event Structure frame keeps the events it handles: each one's type code and
the terminal of the control it comes from, through every view."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import EventStructureNode
from lvkit.load_mode import LoadMode
from lvkit.models import FPTerminal
from tests.error_diagrams import Project, project  # noqa: F401
from tests.event_diagrams import (
    PANEL_CLOSE,
    TIMEOUT,
    VALUE_CHANGE,
    event_loop_vi,
)

_SAMPLES = Path(".lvkit/cache/samples")


def _frames(project: Project):  # noqa: F811
    project.add(event_loop_vi())
    views = project.views("Events.vi")
    node = next(
        n for n in views.graph.iter_nodes(views.vi) if isinstance(n, EventStructureNode)
    )
    return views, node


def test_every_frame_lists_its_events_with_type_code_and_source_control(
    project: Project,  # noqa: F811
) -> None:
    views, node = _frames(project)
    timeout, change, close = node.frames
    assert [e.type_code for e in timeout.events] == [TIMEOUT]
    assert [e.source_terminal_id for e in timeout.events] == [None]
    assert [e.type_code for e in change.events] == [VALUE_CHANGE, VALUE_CHANGE]
    sources = [views.graph.get_terminal(e.source_terminal_id) for e in change.events]
    assert all(isinstance(t, FPTerminal) for t in sources)
    assert [t.name for t in sources] == ["Gain", "Offset"]
    assert [e.type_code for e in close.events] == [PANEL_CLOSE]
    assert close.events[0].source_terminal_id is None


def test_the_views_show_the_same_frame_labels(project: Project) -> None:  # noqa: F811
    views, node = _frames(project)
    assert [f.event_label for f in node.frames] == [
        "[0] Timeout",
        '[1] "Gain": Value Change, "Offset": Value Change',
        "[2] Panel Close?",
    ]
    for label in (
        "[0] Timeout",
        '[1] "Gain": Value Change, "Offset": Value Change',
        "[2] Panel Close?",
    ):
        assert f"Frame {label}:" in views.describe
        assert f"frame {json.dumps(label)} :" in views.lvnet
        assert f"frame {json.dumps(label)} :" in views.terse
        assert json.dumps(label)[1:-1] in json.dumps(views.json)


@pytest.mark.needs_samples
def test_a_value_change_frame_names_the_control_terminal_of_a_real_vi() -> None:
    vi = _SAMPLES / (
        "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/"
        "Graphical Test Runner Support/VI Tester About.vi"
    )
    if not vi.exists():
        pytest.skip(f"{vi} not present")
    graph = InMemoryVIGraph()
    key = graph.load_vi(str(vi), LoadMode.MINIMAL, layout=False)
    node = next(n for n in graph.iter_nodes(key) if isinstance(n, EventStructureNode))
    events = [(f.event_label, f.events) for f in node.frames]
    named = {
        label: [graph.get_terminal(e.source_terminal_id).name for e in found]
        for label, found in events
        if all(e.source_terminal_id for e in found)
    }
    assert named['[3] "copyrights": Value Change'] == ["copyrights"]
    assert named['[0] "Cancel": Value Change'] == ["Cancel"]
    assert [e.type_code for e in node.frames[2].events] == [PANEL_CLOSE]
