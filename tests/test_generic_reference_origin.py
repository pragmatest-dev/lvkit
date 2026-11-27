"""A Property Node on a Generic or GObject reference is a gap when the diagram
made the reference from a block diagram, and is read when the code runs when it
came from the front panel or from somewhere the diagram does not show
(``codegen/nodes/reference_origin.py``). Each case is a tiny diagram generated
with its gaps recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from tests.class_graphs import INT, Project, pane, wire
from tests.test_control_references import PANEL, REFNUM, Row, property_node
from tests.test_front_panel_reference import VI_REF

DIAGRAM = (572,)
LEFT = (103983105,)
OWNER = (103970822,)

_Step = tuple[str, str, Row]


def chain(first_is_vi: bool, *steps: _Step) -> Callable[[str], tuple[VIContext, list]]:
    """A VI whose control ``ctl`` is the reference of the first Property Node, each
    node's reference input wired from the row output of the one before, and whose
    last row is the indicator ``out``. A step is ``(tag, class, row)``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        nodes = [
            property_node(vi, tag, class_name, [row]) for tag, class_name, row in steps
        ]
        flow = [wire(vi, "ctl", f"{steps[0][0]}.ref")]
        for (tag, _, _), (next_tag, _, _), (_, _, row) in zip(
            steps, steps[1:], steps
        ):
            flow.append(wire(vi, f"{tag}.{row[0]}", f"{next_tag}.ref"))
        last_tag, _, last_row = steps[-1]
        flow.append(wire(vi, f"{last_tag}.{last_row[0]}", "out"))
        control = pane(vi, "input", "ctl", VI_REF if first_is_vi else REFNUM, "ctl", 0)
        return (
            VIContext(
                name=vi,
                inputs=[control],
                outputs=[pane(vi, "output", "out", last_row[3], "out", 1)],
                data_flow=flow,
            ),
            list(nodes),
        )

    return build


def _row(name: str, codes: tuple[int, ...], lv_type=REFNUM) -> Row:
    return (name, codes, "output", lv_type)


def _generate(tmp_path: Path, vis: dict[str, Callable]) -> tuple[list[Gap], object]:
    project = Project(tmp_path)
    for name, build in vis.items():
        project.add_vi(name, build)
    gaps: list[Gap] = []
    return gaps, project.generate(gaps)


_FROM_PANEL = chain(
    True, ("vi", "VI", _row("panel", PANEL)), ("o", "GObj", _row("left", LEFT, INT))
)
_FROM_DIAGRAM = chain(
    True, ("vi", "VI", _row("diagram", DIAGRAM)), ("o", "GObj", _row("left", LEFT, INT))
)
_FROM_DIAGRAM_OWNER = chain(
    True,
    ("vi", "VI", _row("diagram", DIAGRAM)),
    ("w", "GObj", _row("owner", OWNER)),
    ("o", "Gen", _row("left", LEFT, INT)),
)
_FROM_PANEL_OWNER = chain(
    True,
    ("vi", "VI", _row("panel", PANEL)),
    ("w", "GObj", _row("owner", OWNER)),
    ("o", "Gen", _row("left", LEFT, INT)),
)
_FROM_NODE = chain(
    False, ("n", "SubVI", _row("owner", OWNER)), ("o", "GObj", _row("left", LEFT, INT))
)
_FROM_INPUT = chain(False, ("o", "GObj", _row("left", LEFT, INT)))
_FROM_APPLICATION_ROW = chain(
    False, ("a", "App", _row("other", (999,))), ("o", "GObj", _row("left", LEFT, INT))
)


def _block_diagram_gaps(gaps: list[Gap]) -> list[str]:
    return [str(g) for g in gaps if "block-diagram objects do not exist" in str(g)]


@pytest.mark.parametrize(
    ("name", "build", "class_name"),
    [
        ("FromDiagram", _FROM_DIAGRAM, "GObj"),
        ("FromDiagramOwner", _FROM_DIAGRAM_OWNER, "Gen"),
        ("FromNode", _FROM_NODE, "GObj"),
    ],
)
def test_a_generic_reference_made_from_a_block_diagram_is_a_gap(
    tmp_path: Path, name: str, build: Callable, class_name: str
) -> None:
    gaps, _ = _generate(tmp_path, {name: build})
    messages = _block_diagram_gaps(gaps)
    assert any(f"on a {class_name} reference" in m for m in messages), messages


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("FromPanel", _FROM_PANEL),
        ("FromPanelOwner", _FROM_PANEL_OWNER),
        ("FromInput", _FROM_INPUT),
        ("FromApplicationRow", _FROM_APPLICATION_ROW),
    ],
)
def test_a_generic_reference_from_the_front_panel_or_from_somewhere_unseen_is_not(
    tmp_path: Path, name: str, build: Callable
) -> None:
    gaps, _ = _generate(tmp_path, {name: build})
    assert _block_diagram_gaps(gaps) == []
