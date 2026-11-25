"""``wire_origins`` and ``wire_destinations`` follow a wire through the tunnels of
every structure, a Diagram Disable Structure too (its node's ``kind`` names the
disable kind, not "structure")."""

from __future__ import annotations

from lvkit.graph.models import (
    CaseStructureNode,
    DisableStructureNode,
    PrimitiveNode,
    WireEnd,
)
from lvkit.graph.wire_origin import WireOriginMixin


def _end(terminal: str, node: str) -> WireEnd:
    return WireEnd(terminal_id=terminal, node_id=node)


class _Graph(WireOriginMixin):
    """A constant ``source`` -> tunnel ``in`` of a structure -> tunnel ``out`` of
    the structure -> ``reader``."""

    def __init__(self, structure: object) -> None:
        self.nodes = {
            "source": PrimitiveNode(id="source", vi_path="t.vi", name="source"),
            "reader": PrimitiveNode(id="reader", vi_path="t.vi", name="reader"),
            "structure": structure,
        }
        self.forward = {
            "source:out": [_end("structure:in", "structure")],
            "structure:in": [_end("structure:out", "structure")],
            "structure:out": [_end("reader:in", "reader")],
        }
        self.backward: dict[str, list[WireEnd]] = {
            "reader:in": [_end("structure:out", "structure")],
            "structure:out": [_end("structure:in", "structure")],
            "structure:in": [_end("source:out", "source")],
        }

    def incoming_edges(self, terminal_id: str) -> list[WireEnd]:
        return self.backward.get(terminal_id, [])

    def outgoing_edges(self, terminal_id: str) -> list[WireEnd]:
        return self.forward.get(terminal_id, [])

    def terminal_vi(self, terminal_id: str) -> str | None:
        return "t.vi"

    def _get_typed_node(self, uid: str):  # noqa: ANN202
        return self.nodes.get(uid)


def _structures():  # noqa: ANN202
    return [
        DisableStructureNode(id="structure", vi_path="t.vi", name="disable"),
        CaseStructureNode(id="structure", vi_path="t.vi", name="case"),
    ]


def test_a_wire_is_followed_back_through_any_structure() -> None:
    for structure in _structures():
        origins = _Graph(structure).wire_origins("reader:in")
        assert [(node.id, end.terminal_id) for node, end in origins] == [
            ("source", "source:out")
        ], structure.name


def test_a_wire_is_followed_forward_through_any_structure() -> None:
    for structure in _structures():
        destinations = _Graph(structure).wire_destinations("source:out")
        assert [(node.id, end.terminal_id) for node, end in destinations] == [
            ("reader", "reader:in")
        ], structure.name
