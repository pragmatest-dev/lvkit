"""A tiny diagram with front-panel terminals placed inside a structure.

``case_with_terminals`` writes a Case structure on a Boolean selector (frames
``False`` and ``True``) and puts controls and indicators on the VI, each at the
position it names: on the VI's own diagram, in a Case frame or in a loop body.
Every indicator is driven by a constant of the value it names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import ConstantNode, WireEnd
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from tests.case_structure_fixture import FrameSpec, _bd_xml, _main_xml

FRAMES = [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))]


@dataclass(frozen=True)
class Placed:
    """One front-panel terminal. ``frame`` is the Case frame it sits in
    (``"False"`` / ``"True"``), ``None`` for the VI's own diagram. An indicator
    is driven by ``value``, an integer constant."""

    name: str
    frame: str | None = None
    value: str | None = None
    on_pane: bool = True


def case_with_terminals(
    tmp_path: Path, controls: list[Placed], indicators: list[Placed]
) -> tuple[InMemoryVIGraph, str]:
    (tmp_path / "Placed.xml").write_text(_main_xml("boolean", ()))
    bd_xml = tmp_path / "Placed_BDHb.xml"
    bd_xml.write_text(
        _bd_xml("boolean", FRAMES, default_frame=None, stored_values=True)
    )
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    vi = graph.list_vis()[0]
    case_id = next(n.id for n in graph.iter_nodes(vi) if n.node_type == "caseStruct")
    number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    vi_node = graph._graph.nodes[vi]["node"]  # noqa: SLF001
    for i, placed in enumerate([*controls, *indicators]):
        is_indicator = i >= len(controls)
        terminal = FPTerminal(
            id=f"{vi}::fp{i}",
            index=i if placed.on_pane else None,
            direction="output" if is_indicator else "input",
            name=placed.name,
            lv_type=number,
            is_indicator=is_indicator,
            is_public=placed.on_pane,
            parent=case_id if placed.frame is not None else None,
            frame=placed.frame,
        )
        vi_node.terminals = [*vi_node.terminals, terminal]
        graph._term_to_node[terminal.id] = vi  # noqa: SLF001
        if is_indicator:
            _drive(graph, vi, terminal, placed.value or "0", 900 + i)
    return graph, vi


def _drive(
    graph: InMemoryVIGraph, vi: str, terminal: Terminal, value: str, uid: int
) -> None:
    node_id = f"{vi}::{uid}"
    source = Terminal(id=node_id, index=0, direction="output")
    graph._graph.add_node(  # noqa: SLF001
        node_id,
        node=ConstantNode(
            id=node_id,
            vi_path=vi,
            name=None,
            node_type="constant",
            terminals=[source],
            value=value,
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
        ),
    )
    graph._term_to_node[source.id] = node_id  # noqa: SLF001
    graph._vi_nodes[vi].add(node_id)  # noqa: SLF001
    graph._graph.add_edge(  # noqa: SLF001
        node_id,
        vi,
        vi=vi,
        source=WireEnd(terminal_id=source.id, node_id=node_id),
        dest=WireEnd(terminal_id=terminal.id, node_id=vi),
    )
