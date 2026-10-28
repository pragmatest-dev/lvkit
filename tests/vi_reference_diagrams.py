"""Tiny block diagrams for the VI-reference tests: XML for the nodes, and the
typed constants a diagram of just that construct needs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import ConstantNode, VINode, WireEnd
from lvkit.models import FPTerminal, LVType, LVTypeKind, PaneSlot, Terminal

CALLEE_FILE = "Callee_BDHb.xml"
CALLEE_NAME = "Callee.vi"
CALLER_FILE = "Refs_BDHb.xml"

# Terminals of the called VI's connector pane.
CALLEE_INPUT = Terminal(id="x", index=0, direction="input", name="x")
CALLEE_OUTPUT = Terminal(id="y", index=1, direction="output", name="y")


def label(uid: int, text: str) -> str:
    return (
        f'<label class="label" uid="{uid}"><objFlags>1</objFlags>'
        f'<partID>16</partID><textRec class="textHair"><text>"{text}"</text>'
        "</textRec></label>"
    )


def static_vi_reference(uid: int, vi_name: str) -> str:
    """Terminal ``uid + 1``."""
    return (
        f'<SL__arrayElement class="statVIRef" uid="{uid}">'
        '<termList elements="1">'
        f'<SL__arrayElement class="term" uid="{uid + 1}"><objFlags>4096</objFlags>'
        f'<dco class="bDDCO" uid="{uid + 2}"><objFlags>9</objFlags></dco>'
        "</SL__arrayElement></termList>"
        f"{label(uid + 3, vi_name)}</SL__arrayElement>"
    )


def this_vi(uid: int) -> str:
    """The "This VI" constant. Terminal ``uid + 1``; type it with
    ``this_vi_type``."""
    return (
        f'<SL__arrayElement class="ctlRefConst" uid="{uid}">'
        '<termList elements="1">'
        f'<SL__arrayElement class="term" uid="{uid + 1}"><objFlags>4096</objFlags>'
        f'<dco class="ctlRefDCO" uid="{uid + 2}"><objFlags>1</objFlags></dco>'
        "</SL__arrayElement></termList>"
        f"{label(uid + 3, 'This VI')}</SL__arrayElement>"
    )


def this_vi_type() -> LVType:
    """The type of a generic VI Server reference of class VI."""
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="LVObjCtl",
        server_class="VI",
    )


def open_vi_reference(uid: int) -> str:
    """Terminals: ``uid + 1`` the VI reference out, ``uid + 3`` the VI path in,
    ``uid + 5`` the options in."""
    return (
        f'<SL__arrayElement class="prim" uid="{uid}"><primResID>8010</primResID>'
        '<termList elements="3">'
        f'<SL__arrayElement class="term" uid="{uid + 1}">'
        f'<dco class="parm" uid="{uid + 2}"><objFlags>1</objFlags>'
        "<parmIndex>3</parmIndex></dco></SL__arrayElement>"
        f'<SL__arrayElement class="term" uid="{uid + 3}">'
        f'<dco class="parm" uid="{uid + 4}"><parmIndex>10</parmIndex></dco>'
        "</SL__arrayElement>"
        f'<SL__arrayElement class="term" uid="{uid + 5}">'
        f'<dco class="parm" uid="{uid + 6}"><parmIndex>9</parmIndex></dco>'
        "</SL__arrayElement></termList></SL__arrayElement>"
    )


def build_path(uid: int) -> str:
    """Terminals: ``uid + 1`` the appended path out, ``uid + 3`` the name or
    relative path in, ``uid + 5`` the base path in."""
    return (
        f'<SL__arrayElement class="prim" uid="{uid}"><primResID>1419</primResID>'
        '<termList elements="3">'
        f'<SL__arrayElement class="term" uid="{uid + 1}">'
        f'<dco class="parm" uid="{uid + 2}"><objFlags>1</objFlags></dco>'
        "</SL__arrayElement>"
        f'<SL__arrayElement class="term" uid="{uid + 3}">'
        f'<dco class="parm" uid="{uid + 4}"><parmIndex>1</parmIndex></dco>'
        "</SL__arrayElement>"
        f'<SL__arrayElement class="term" uid="{uid + 5}">'
        f'<dco class="parm" uid="{uid + 6}"><parmIndex>2</parmIndex></dco>'
        "</SL__arrayElement></termList></SL__arrayElement>"
    )


def close_reference(uid: int) -> str:
    """Terminal ``uid + 1`` the reference in."""
    return (
        f'<SL__arrayElement class="prim" uid="{uid}"><primResID>8011</primResID>'
        '<termList elements="1">'
        f'<SL__arrayElement class="term" uid="{uid + 1}">'
        f'<dco class="parm" uid="{uid + 2}"><parmIndex>11</parmIndex></dco>'
        "</SL__arrayElement></termList></SL__arrayElement>"
    )


def vi_name_property(uid: int, code: int, name: str) -> str:
    """A Property Node of the VI class reading one property. Terminals:
    ``uid + 1`` the VI reference in, ``uid + 9`` the property's value."""
    frame = [(uid + 2, 65536), (uid + 4, 262145), (uid + 6, 131072), (uid + 8, 524289)]
    terminals = "".join(
        f'<SL__arrayElement class="term" uid="{dco - 1}">'
        f'<dco class="hGrowCItem" uid="{dco}"><objFlags>{flags}</objFlags></dco>'
        "</SL__arrayElement>"
        for dco, flags in frame
    )
    perm = "".join(f'<SL__arrayElement uid="{dco}" />' for dco, _ in frame)
    return (
        f'<SL__arrayElement class="propNode" uid="{uid}">'
        f'<termList elements="5">{terminals}'
        f'<SL__arrayElement class="term" uid="{uid + 9}">'
        f'<dco class="propItem" uid="{uid + 10}"><objFlags>1</objFlags>'
        '<propList elements="1">'
        f'<SL__arrayElement class="propItemInfo" uid="{uid + 11}">'
        f'<PropItemName>"{name}"</PropItemName><PropItemCode>{code}</PropItemCode>'
        "</SL__arrayElement></propList></dco></SL__arrayElement></termList>"
        f'<dcoList elements="1"><SL__arrayElement uid="{uid + 10}" /></dcoList>'
        f'<permDCOList elements="4">{perm}</permDCOList>'
        '<nodeName>"VI"</nodeName></SL__arrayElement>'
    )


def call_through_reference(
    uid: int,
    title: str | None = None,
    *,
    save_style: str = "00",
    callee_terminals: bool = True,
) -> str:
    """A node that calls through a VI reference. Terminals: ``uid + 1`` the VI
    reference in, ``uid + 3`` the VI reference out, then error in and out;
    ``uid + 11`` the called VI's input at index 0, ``uid + 13`` its output at
    index 1."""
    frame = [(uid + 2, 65536), (uid + 4, 262145), (uid + 6, 131072), (uid + 8, 524289)]
    terminals = "".join(
        f'<SL__arrayElement class="term" uid="{dco - 1}">'
        f'<dco class="hGrowCItem" uid="{dco}"><objFlags>{flags}</objFlags></dco>'
        "</SL__arrayElement>"
        for dco, flags in frame
    )
    if callee_terminals:
        terminals += (
            f'<SL__arrayElement class="term" uid="{uid + 11}">'
            f'<dco class="iUseDCO" uid="{uid + 12}"><objFlags>64</objFlags></dco>'
            "</SL__arrayElement>"
            f'<SL__arrayElement class="term" uid="{uid + 13}">'
            f'<dco class="iUseDCO" uid="{uid + 14}"><objFlags>65</objFlags>'
            "<paramIdx>1</paramIdx></dco></SL__arrayElement>"
        )
    perm = "".join(f'<SL__arrayElement uid="{dco}" />' for dco, _ in frame)
    title_label = label(uid + 9, title) if title else ""
    return (
        f'<SL__arrayElement class="callByRefNode" uid="{uid}">'
        f"<objFlags>640</objFlags><termList>{terminals}</termList>"
        f'{title_label}<permDCOList elements="4">{perm}</permDCOList>'
        f"<CBRSaveStyle>{save_style}</CBRSaveStyle></SL__arrayElement>"
    )


def wire(uid: int, source: int, dest: int) -> str:
    return (
        f'<SL__arrayElement class="signal" uid="{uid}"><termList elements="2">'
        f'<SL__arrayElement uid="{source}" /><SL__arrayElement uid="{dest}" />'
        "</termList></SL__arrayElement>"
    )


def diagram(nodes: list[str], wires: list[str]) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        '<zPlaneList elements="0" />'
        f'<nodeList elements="{len(nodes)}">{"".join(nodes)}</nodeList>'
        f'<signalList elements="{len(wires)}">{"".join(wires)}</signalList>'
        "</root></SL__rootObject>"
    )


@dataclass(frozen=True)
class Constant:
    """A typed constant wired to the terminal ``to`` of the caller's diagram."""

    uid: int
    value: str
    underlying_type: str
    to: int


@dataclass
class Loaded:
    graph: InMemoryVIGraph
    caller: str
    callee: str


def load(
    tmp_path: Path,
    nodes: list[str],
    wires: list[str],
    constants: list[Constant] | tuple[Constant, ...] = (),
) -> Loaded:
    """A caller VI with the given diagram and the VI ``Callee.vi`` it can call,
    in one graph. A Static VI Reference on the diagram names the callee."""
    (tmp_path / CALLEE_FILE).write_text(diagram([], []))
    (tmp_path / CALLER_FILE).write_text(diagram(nodes, wires))
    graph = InMemoryVIGraph()
    callee = graph.load_vi(tmp_path / CALLEE_FILE)
    caller = graph.load_vi(tmp_path / CALLER_FILE)
    assert callee is not None and caller is not None
    # The callee is known by its qualified name and has a connector pane.
    graph._vi_display_names[callee] = CALLEE_NAME  # noqa: SLF001
    callee_node = graph._graph.nodes[callee]["node"]  # noqa: SLF001
    assert isinstance(callee_node, VINode)
    callee_node.terminals = [CALLEE_INPUT, CALLEE_OUTPUT]
    for node in graph.iter_nodes(caller):
        if isinstance(node, ConstantNode) and node.lv_type is not None:
            if node.lv_type.underlying_type == "VIRefnum":
                node.referenced_vi_key = callee
    for constant in constants:
        _add_constant(graph, caller, constant)
    return Loaded(graph, caller, callee)


def _add_constant(graph: InMemoryVIGraph, vi: str, constant: Constant) -> None:
    node_id = f"{vi}::{constant.uid}"
    terminal = Terminal(id=node_id, index=0, direction="output")
    node = ConstantNode(
        id=node_id,
        vi_path=vi,
        name=None,
        node_type="constant",
        terminals=[terminal],
        value=constant.value,
        lv_type=LVType(
            kind=LVTypeKind.PRIMITIVE, underlying_type=constant.underlying_type
        ),
    )
    graph._graph.add_node(node_id, node=node)  # noqa: SLF001
    graph._term_to_node[terminal.id] = node_id  # noqa: SLF001
    graph._vi_nodes[vi].add(node_id)  # noqa: SLF001
    dest_id = f"{vi}::{constant.to}"
    dest_node = graph._term_to_node[dest_id]  # noqa: SLF001
    graph._graph.add_edge(  # noqa: SLF001
        node_id,
        dest_node,
        vi=vi,
        source=WireEnd(terminal_id=terminal.id, node_id=node_id),
        dest=WireEnd(terminal_id=dest_id, node_id=dest_node),
    )


def typed_vi_reference(names: tuple[str, ...] = ("x", "y")) -> LVType:
    """A strictly typed VI refnum whose connector pane has an input and an output
    (slot 0 and slot 1) of the given names."""
    number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    pane = (
        PaneSlot(0, False, number, names[0]),
        PaneSlot(1, True, number, names[1]),
    )
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="LVObjCtl",
        element_type=LVType(
            kind=LVTypeKind.PRIMITIVE, underlying_type="Function", pane=pane
        ),
    )


def type_terminal(loaded: Loaded, uid: int, lv_type: LVType) -> None:
    """Give the caller's terminal ``uid`` a type."""
    for node in loaded.graph.iter_nodes(loaded.caller):
        for terminal in node.terminals:
            if terminal.id == f"{loaded.caller}::{uid}":
                terminal.lv_type = lv_type


def add_control(
    loaded: Loaded, name: str, lv_type: LVType | None, to: int, uid: str = "control"
) -> None:
    """A front-panel input named ``name`` of the caller, wired to terminal ``to``."""
    graph, vi = loaded.graph, loaded.caller
    terminal = FPTerminal(
        id=f"{vi}::{uid}",
        index=0,
        direction="input",
        name=name,
        lv_type=lv_type,
        is_public=True,
    )
    node = graph._graph.nodes[vi]["node"]  # noqa: SLF001
    node.terminals = [*node.terminals, terminal]
    graph._term_to_node[terminal.id] = vi  # noqa: SLF001
    dest_id = f"{vi}::{to}"
    dest_node = graph._term_to_node[dest_id]  # noqa: SLF001
    graph._graph.add_edge(  # noqa: SLF001
        vi,
        dest_node,
        vi=vi,
        source=WireEnd(terminal_id=terminal.id, node_id=vi),
        dest=WireEnd(terminal_id=dest_id, node_id=dest_node),
    )


def own_by_class(loaded: Loaded, class_name: str) -> None:
    """The callee is a method of the class ``class_name``, whose file sits in the
    callee's folder."""
    from lvkit.graph.node_kinds import EdgeRel, NodeType

    graph = loaded.graph
    class_key = str(Path(loaded.callee).with_name(class_name))
    graph._dep_graph.add_node(class_key, node_type=NodeType.CLASS)  # noqa: SLF001
    graph._dep_graph.add_edge(class_key, loaded.callee, rel=EdgeRel.OWNS)  # noqa: SLF001
    graph._vi_display_names[loaded.callee] = f"{class_name}:{CALLEE_NAME}"  # noqa: SLF001
    graph._vi_display_names[class_key] = class_name  # noqa: SLF001
