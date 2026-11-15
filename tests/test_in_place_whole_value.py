"""A whole-value tile of an In Place Element Structure.

A whole-value border tile (``decomposeMatchNode``) has one input and one output and
no field split: the value that enters leaves unchanged. The structure's output
port that its write half feeds therefore carries the value of the structure's
input port that its read half takes, and a node reading that output has a value.

The diagram: a control ``x`` feeds an In Place Element Structure whose read tile
feeds its write tile by a wire; the structure's output feeds the indicator ``y``.
A second structure holds a body node (Increment) between the two tiles.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.codegen.builder import build_module
from tests.helpers import exec_generated
from tests.structure_diagrams import (
    I32,
    dco,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    root_diagram,
    srn,
    term,
    wire,
)


def _tile(
    uid: int,
    poser: int,
    in_dco: int,
    out_dco: int,
    in_port: int | None = None,
    out_port: int | None = None,
) -> str:
    """A whole-value tile: terminal ``uid + 1`` in, ``uid + 2`` out. A port is the
    structure terminal that shares the data-flow id of that tile terminal."""

    def side(term_uid: int, dco_uid: int, port: int | None, output: bool) -> str:
        faces = None if port is None else [term_uid, port]
        flags = 1 if output else None
        return term(
            term_uid,
            dco("decomposeDCO", dco_uid, type_id=I32, faces=faces, flags=flags),
        )

    return (
        f'<SL__arrayElement class="decomposeMatchNode" uid="{uid}">'
        '<termList elements="2">'
        f"{side(uid + 1, in_dco, in_port, False)}"
        f"{side(uid + 2, out_dco, out_port, True)}"
        "</termList>"
        f'<poser uid="{poser}" /></SL__arrayElement>'
    )


def _structure(*, with_body: bool) -> str:
    """Structure 100: input port 101, output port 103. Read tile 120 (port in),
    write tile 130 (port out), joined by a wire, or through an Increment (150)."""
    read = _tile(120, 119, 102, 124, in_port=101)
    write = _tile(130, 119, 134, 204, out_port=103)
    nodes = [read, write]
    joins = []
    if with_body:
        nodes.append(increment(150))
        joins = [(122, 153), (151, 131)]
    else:
        joins = [(122, 131)]
    wires = "".join(
        f'<SL__arrayElement class="signal" uid="{140 + i}"><termList elements="2">'
        f'<SL__arrayElement uid="{a}" /><SL__arrayElement uid="{b}" />'
        "</termList></SL__arrayElement>"
        for i, (a, b) in enumerate(joins)
    )
    return (
        '<SL__arrayElement class="decomposeRecomposeStructure" uid="100">'
        '<termList elements="2">'
        + term(101, '<dco uid="102" />')
        + term(103, '<dco uid="204" />')
        + "</termList>"
        '<diagramList elements="1"><SL__arrayElement class="diag" uid="110">'
        '<zPlaneList elements="0" />'
        f'<nodeList elements="{len(nodes)}">{"".join(nodes)}</nodeList>'
        f'<signalList elements="{len(joins)}">{wires}</signalList>'
        "</SL__arrayElement></diagramList></SL__arrayElement>"
    )


def _run(tmp_path: Path, *, with_body: bool, value: int) -> object:
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({30: "x"}, {31: "y"}, pane={0: 30, 1: 31})
    )
    xml = root_diagram(
        [_structure(with_body=with_body)],
        [
            srn(800, [fp_terminal(5030, 30), fp_terminal(5031, 31)]),
            '<SL__arrayElement uid="100" />',
        ],
        [wire(901, 5030, 101), wire(902, 103, 5031)],
    )
    views = load(tmp_path, xml)
    source = build_module(
        views.graph.get_vi_context(views.vi), views.vi, graph=views.graph
    )
    assert "NotImplementedError" not in source
    namespace: dict[str, object] = {}
    exec_generated(source, namespace, "T.py")  # noqa: S102
    return namespace["t"](x=value).y  # type: ignore[operator]


def test_the_value_passes_through_the_structure(tmp_path: Path) -> None:
    assert _run(tmp_path, with_body=False, value=7) == 7


def test_a_node_between_the_tiles_changes_the_value(tmp_path: Path) -> None:
    assert _run(tmp_path, with_body=True, value=7) == 8
