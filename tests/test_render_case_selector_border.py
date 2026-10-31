"""The case selector's border glyph comes from the structure's selector terminal,
not from a terminal's name."""

from __future__ import annotations

from lvkit.graph.models import CaseStructureNode
from lvkit.models import TunnelTerminal
from lvkit.parser.layout import Layout
from lvkit.render.scene import _structure_borders


def _case(selector_terminal: str | None) -> CaseStructureNode:
    return CaseStructureNode(
        id="vi::1",
        vi_path="vi",
        node_type="caseStruct",
        terminals=[
            TunnelTerminal(
                id="vi::10",
                index=0,
                direction="input",
                name="selector",
                boundary="outer",
            ),
            TunnelTerminal(
                id="vi::11",
                index=1,
                direction="input",
                name="renamed",
                boundary="outer",
            ),
        ],
        selector_terminal=selector_terminal,
    )


def _kinds(node: CaseStructureNode) -> dict[str, str | None]:
    layout = Layout(node_bounds={"10": (0, 0, 8, 8), "11": (0, 10, 8, 18)})
    borders = _structure_borders(node, layout, "vi")
    return {b.terminal.id: b.glyph_kind for b in borders}


def test_selector_found_by_terminal_id() -> None:
    assert _kinds(_case("vi::11")) == {"vi::10": "tunnel", "vi::11": "selector"}


def test_terminal_name_alone_is_not_the_selector() -> None:
    assert _kinds(_case(None)) == {"vi::10": "tunnel", "vi::11": "tunnel"}
