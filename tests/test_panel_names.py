"""The one name table of a VI's front panel: unique among every control and
indicator, the same for the hooks, the panel description and the view."""

from __future__ import annotations

from lvkit.codegen.panel_names import panel_names
from lvkit.graph.models import VIContext
from tests.class_graphs import INT, pane


def test_a_control_and_an_indicator_with_one_label_get_distinct_names() -> None:
    first = pane("v", "input", "a", INT, "same", 0)
    second = pane("v", "input", "b", INT, "same", 1)
    shown = pane("v", "output", "c", INT, "same", 2)
    hidden = pane("v", "output", "d", INT, "Off Pane", 3)
    hidden.is_public = False
    context = VIContext(
        name="v", inputs=[first, second], outputs=[shown], off_pane_outputs=[hidden]
    )
    names = panel_names(context)
    assert list(names) == [first.id, second.id, shown.id, hidden.id]
    assert len(set(names.values())) == 4
    assert names[hidden.id] == "off_pane"
