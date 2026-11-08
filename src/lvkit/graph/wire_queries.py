"""Queries over the wires into and out of one terminal, and over the Local
Variables of one VI."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import networkx as nx

from ..models import FPTerminal, Terminal, TunnelTerminal
from .models import (
    AnyGraphNode,
    ConstantNode,
    EventStructureNode,
    LocalVariableNode,
    LoopNode,
    PrimitiveNode,
    StructureNode,
    WireEnd,
)

# The Property Node row that reads or writes a control's value.
VALUE_PROPERTY = "Value"


class WireQueriesMixin:
    """Wire and Local Variable queries on ``InMemoryVIGraph``."""

    if TYPE_CHECKING:
        _term_to_node: dict[str, str]
        _graph: nx.MultiDiGraph

        def iter_nodes(self, vi_name: str) -> list[AnyGraphNode]: ...
        def get_graph_node(self, node_id: str) -> AnyGraphNode | None: ...
        def get_terminal(self, terminal_id: str) -> Terminal | None: ...
        def incoming_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def outgoing_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def terminal_vi(self, terminal_id: str) -> str | None: ...
        def get_off_pane_inputs(self, vi_name: str) -> list[Terminal]: ...

    def terminal_node_id(self, terminal_id: str) -> str:
        """The id of the node that owns the terminal."""
        return self._term_to_node[terminal_id]

    def terminal_is_fed(self, terminal_id: str) -> bool:
        """Whether a wire of the terminal's own VI ends at it. A subVI call's
        input is also wired to the called VI's control, which feeds nothing."""
        home = self.terminal_vi(terminal_id)
        return any(
            self.terminal_vi(src.terminal_id) == home
            for src in self.incoming_edges(terminal_id)
        )

    def wire_readers(self, terminal_id: str) -> list[WireEnd]:
        """The terminals of the terminal's own VI that the wire from the output
        ``terminal_id`` goes to."""
        home = self.terminal_vi(terminal_id)
        return [
            dest
            for dest in self.outgoing_edges(terminal_id)
            if self.terminal_vi(dest.terminal_id) == home
        ]

    def value_read_elsewhere(self, terminal_id: str) -> bool:
        """Whether the value that reaches the input ``terminal_id`` is also wired
        to another terminal. The wire is followed back through structure tunnels
        (``_wire_back``); the inside faces of one tunnel (one per case frame) are
        one reader."""
        return any(
            dest.terminal_id != current
            and not (
                isinstance(self.get_terminal(src.terminal_id), TunnelTerminal)
                and dest.node_id == src.node_id
            )
            for current, src in self._wire_back(terminal_id)
            for dest in self.wire_readers(src.terminal_id)
        )

    def _wire_back(self, terminal_id: str) -> Iterator[tuple[str, WireEnd]]:
        """Each step of the wire into ``terminal_id`` followed back to its
        producer through structure tunnels: the terminal reached and the source
        of the VI's own diagram that feeds it."""
        home = self.terminal_vi(terminal_id)
        todo, seen = [terminal_id], set()
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            for src in self.incoming_edges(current):
                if self.terminal_vi(src.terminal_id) == home:
                    yield current, src
                    todo.append(src.terminal_id)

    def read_in_frame_of(self, reader: WireEnd, node: AnyGraphNode) -> bool:
        """Whether the terminal ``reader``, wired from an output of ``node``,
        reads the value on the diagram ``node`` is on (the same structure frame,
        or the VI's own diagram). A structure reads it there through a border
        terminal on that diagram (the case selector, a loop's count terminal, an
        output tunnel's inside face) unless the value passes inside the
        structure; a front-panel terminal reads it where the VI returns, on the
        VI's own diagram."""
        reader_node = self.get_graph_node(reader.node_id)
        if reader_node is None:
            return False
        if isinstance(reader_node, StructureNode):
            return not self._passes_into_structure(reader.terminal_id)
        return (reader_node.parent, reader_node.frame) == (node.parent, node.frame)

    def _passes_into_structure(self, terminal_id: str) -> bool:
        """Whether a value wired to ``terminal_id`` is read inside a structure:
        the terminal is the outside of a structure's border terminal (a tunnel,
        shift register, case selector or count terminal) and something on a
        diagram of the structure reads one of its inside faces."""
        term = self.get_terminal(terminal_id)
        if not isinstance(term, TunnelTerminal) or term.boundary != "outer":
            return False
        structure = self.terminal_node_id(terminal_id)
        return any(
            reader.terminal_id != terminal_id
            for face in self.outgoing_edges(terminal_id)
            if face.node_id == structure
            for reader in self.outgoing_edges(face.terminal_id)
        )

    def terminal_fed_by_error_cluster(self, terminal_id: str) -> bool:
        """Whether a wire into the terminal comes from a terminal that carries an
        error cluster."""
        for src in self.incoming_edges(terminal_id):
            source = self.get_graph_node(src.node_id)
            if source is not None and any(
                t.id == src.terminal_id and t.is_error_cluster for t in source.terminals
            ):
                return True
        return False

    def terminal_sources_description(self, terminal_id: str) -> str:
        """The nodes whose terminals wire into ``terminal_id``, in LabVIEW's
        words, for an error message."""
        from .core import uid_of

        found = []
        for edge in self.incoming_edges(terminal_id):
            source = self.get_graph_node(edge.node_id)
            if source is None:
                continue
            found.append(f"{source.display_name}, terminal {uid_of(edge.terminal_id)}")
        return ", ".join(found) or "nothing"

    def indicator_local_reads(self, vi_name: str) -> dict[str, FPTerminal]:
        """The indicators of a VI that one of its Local Variables reads, by
        terminal id."""
        found: dict[str, FPTerminal] = {}
        for node in self.iter_nodes(vi_name):
            if (
                isinstance(node, LocalVariableNode)
                and not node.is_write
                and node.control_terminal_id
            ):
                control = self.get_terminal(node.control_terminal_id)
                if isinstance(control, FPTerminal) and control.is_indicator:
                    found[control.id] = control
        return found

    def front_panel_loop(self, terminal_id: str) -> str | None:
        """The id of the While Loop, For Loop or Event Structure around the
        control or indicator terminal ``terminal_id`` on the diagram (the
        nearest one, through any Case, Sequence or Disable structures between);
        None for a terminal on the VI's own diagram or any other terminal."""
        terminal = self.get_terminal(terminal_id)
        if not isinstance(terminal, FPTerminal):
            return None
        return self.enclosing_loop(terminal.parent)

    def enclosing_loop(self, around: str | None) -> str | None:
        """The id of the nearest While Loop, For Loop or Event Structure that is
        the structure ``around`` or encloses it (through any Case, Sequence or
        Disable structures between); None when ``around`` is None (the VI's own
        diagram) or no such structure encloses it."""
        while around is not None:
            node = self.get_graph_node(around)
            if node is None:
                return None
            if isinstance(node, LoopNode | EventStructureNode):
                return node.id
            around = node.parent
        return None

    def touches_panel_while_running(self, vi_name: str) -> bool:
        """Whether the VI's diagram reads or writes its own front panel while it
        runs: a control or indicator terminal inside a While Loop, For Loop or
        Event Structure, a Local Variable of one of its controls or indicators,
        a Value Property Node bound to one of its controls, or an off-pane control
        whose terminal has a wire leaving it (the diagram reads it)."""
        vi = self.get_graph_node(vi_name)
        if vi is not None and any(
            self.front_panel_loop(t.id) is not None
            for t in vi.terminals
            if isinstance(t, FPTerminal)
        ):
            return True
        if any(self.wire_readers(c.id) for c in self.get_off_pane_inputs(vi_name)):
            return True
        for node in self.iter_nodes(vi_name):
            if isinstance(node, LocalVariableNode):
                if isinstance(
                    self.get_terminal(node.control_terminal_id or ""), FPTerminal
                ):
                    return True
            elif (
                isinstance(node, PrimitiveNode)
                and node.bound_control_uid
                and any(
                    [s.name for s in row.segments] == [VALUE_PROPERTY]
                    for row in node.properties
                )
            ):
                return True
        return False

    def bound_control(self, node: PrimitiveNode) -> FPTerminal | None:
        """The control of the VI that the bound Property Node ``node`` is tied to,
        as its terminal: the control that holds the object the node is bound to
        (a part of a type-defined control belongs to that control); None for an
        unbound node or an indicator."""
        vi = self.get_graph_node(node.vi_path)
        if not node.bound_control_dco_uid or vi is None:
            return None
        for terminal in vi.terminals:
            if (
                isinstance(terminal, FPTerminal)
                and terminal.fp_dco_uid == node.bound_control_dco_uid
                and not terminal.is_indicator
            ):
                return terminal
        return None

    def indicators_written_in_a_frame(self, vi_name: str) -> dict[str, FPTerminal]:
        """The connector-pane indicators of a VI that something inside a
        structure frame writes, by terminal id: a call of the VI may finish
        without writing them (the frame does not run, the loop runs zero
        times)."""
        vi = self.get_graph_node(vi_name)
        found: dict[str, FPTerminal] = {}
        for term in vi.terminals if vi is not None else []:
            if not (
                isinstance(term, FPTerminal) and term.is_indicator and term.is_public
            ):
                continue
            if any(self.inside_a_frame(s) for s in self.indicator_sources(term.id)):
                found[term.id] = term
        return found

    def indicator_written_on_vi_diagram(self, indicator_id: str) -> bool:
        """Whether something on the VI's own diagram (outside every structure
        frame) writes the indicator, so every call writes it."""
        return any(
            not self.inside_a_frame(source)
            for source in self.indicator_sources(indicator_id)
        )

    def inside_a_frame(self, terminal_id: str) -> bool:
        """Whether the terminal is on the diagram of a structure frame: a
        terminal of a node inside a structure, or an inside face of a
        structure's border, or a control or indicator terminal placed in one."""
        if self.inside_face(terminal_id) is not None:
            return True
        terminal = self.get_terminal(terminal_id)
        if isinstance(terminal, FPTerminal) and terminal.parent is not None:
            return True
        node = self.get_graph_node(self.terminal_node_id(terminal_id))
        return node is not None and node.parent is not None

    def written_in_frame(
        self, terminal_id: str, frame: tuple[str, str | int | None]
    ) -> bool:
        """Whether the source ``terminal_id`` gives its value on the diagram of
        the structure frame ``frame`` (a structure id and frame key; key
        ``None`` for any frame of the structure) without a node of that diagram
        producing it: an inside face of the frame, a constant in it, or a control
        terminal placed in it (it gives its value again at every pass)."""
        structure, key = frame
        face = self.inside_face(terminal_id)
        if face is not None:
            return face[0] == structure and (key is None or face[1] == key)
        terminal = self.get_terminal(terminal_id)
        if isinstance(terminal, FPTerminal) and terminal.parent is not None:
            return terminal.parent == structure and (
                key is None or terminal.frame == key
            )
        node = self.get_graph_node(self.terminal_node_id(terminal_id))
        return (
            isinstance(node, ConstantNode)
            and node.parent == structure
            and (key is None or node.frame == key)
        )

    def inside_face(self, terminal_id: str) -> tuple[str, str | int | None] | None:
        """The structure and frame whose diagram the terminal is an inside face
        on: a tunnel's (shift register's, case selector's, count terminal's)
        inside face, or a loop's iteration terminal. The frame is keyed as the
        structure's children are (``graph.operations.frame_key``): a case,
        disable or sequence frame's position as text, ``None`` for a loop's one
        diagram. ``None`` for any other terminal."""
        term = self.get_terminal(terminal_id)
        node = self.get_graph_node(self.terminal_node_id(terminal_id))
        if not isinstance(node, StructureNode) or term is None:
            return None
        if isinstance(term, TunnelTerminal):
            if term.boundary != "inner":
                return None
            if term.frame is not None:
                return node.id, term.frame
            if term.frame_position is not None:
                return node.id, str(term.frame_position)
            return node.id, None
        # A loop's one plain output is its iteration terminal, inside the loop;
        # another structure's plain terminals (an In Place Element's ports) are
        # on its outside.
        if isinstance(node, LoopNode) and term.direction == "output":
            return node.id, None
        return None

    def indicators_written_by(self, terminal_id: str, tracked: set[str]) -> list[str]:
        """The ids among ``tracked`` that a wire from ``terminal_id`` writes: the
        indicator it is wired to, or the one a Local Variable write it is wired
        to writes."""
        written: list[str] = []
        for dest in self.outgoing_edges(terminal_id):
            if dest.terminal_id in tracked:
                written.append(dest.terminal_id)
                continue
            node = self.get_graph_node(dest.node_id)
            if (
                isinstance(node, LocalVariableNode)
                and node.is_write
                and node.control_terminal_id in tracked
            ):
                written.append(node.control_terminal_id)
        return written

    def written_by_local_variable(self, control_id: str) -> bool:
        """Whether a Local Variable write of the control or indicator
        ``control_id`` is wired to it."""
        for src in self.incoming_edges(control_id):
            node = self.get_graph_node(src.node_id)
            if isinstance(node, LocalVariableNode) and node.is_write:
                return True
        return False

    def indicator_sources(self, indicator_id: str) -> list[str]:
        """The terminals whose wires write the indicator: those wired to it, and
        those wired to a Local Variable write of it."""
        sources: list[str] = []
        for src in self.incoming_edges(indicator_id):
            node = self.get_graph_node(src.node_id)
            if isinstance(node, LocalVariableNode) and node.is_write:
                sources.extend(
                    w.terminal_id for w in self.incoming_edges(src.terminal_id)
                )
            else:
                sources.append(src.terminal_id)
        return sources

    def value_is_shared(self, terminal_id: str) -> bool:
        """Whether the value that reaches the input ``terminal_id`` can still be
        read by something that does not run before the node of that terminal, so
        that node, if it changes the value in place, must work on a copy.
        Following the wire back through structure tunnels to its producer, a
        source on the way feeds another terminal whose node the node of
        ``terminal_id`` does not wait for, or the wire enters a loop through an
        input tunnel (each iteration reads the same value). A value that comes
        from the VI's own control is the caller's to copy. A reader off the
        diagram (an indicator of the VI, read when the VI returns) runs after
        every node."""
        home = self.terminal_vi(terminal_id)
        changer = self.terminal_node_id(terminal_id)
        diagram = self._diagram_of(home)
        for current, src in self._wire_back(terminal_id):
            if self._is_field_of_container(src.terminal_id):
                return True
            for dest in self.wire_readers(src.terminal_id):
                if dest.terminal_id == current:
                    continue
                if any(
                    reader != changer
                    and (
                        reader not in diagram
                        or not nx.has_path(diagram, reader, changer)
                    )
                    for reader in self._readers(dest, changer)
                ):
                    return True
            term = self.get_terminal(current)
            if (
                isinstance(term, TunnelTerminal)
                and term.boundary == "inner"
                and term.tunnel_type == "lpTun"
                and term.paired_id == src.terminal_id
                and isinstance(
                    self.get_graph_node(self.terminal_node_id(current)), LoopNode
                )
            ):
                return True
        return False

    def _diagram_of(self, vi: str | None) -> nx.MultiDiGraph:
        """The wires between the nodes of the VI's diagram: without the VI's own
        node, whose controls feed the diagram and whose indicators it feeds, and
        without the definitions of the VIs it calls. A path through either is
        not an order the diagram sets."""

        def on_diagram(node_id: str) -> bool:
            node = self.get_graph_node(node_id)
            return node_id != vi and node is not None and node.vi_path == vi

        return nx.subgraph_view(self._graph, filter_node=on_diagram)

    def _is_field_of_container(self, terminal_id: str) -> bool:
        """Whether the terminal gives a field of a cluster or class (an Unbundle
        By Name or an In Place Element's field output): the value is the one the
        container holds, so a change through it is a change of the container."""
        term = self.get_terminal(terminal_id)
        return (
            term is not None and term.nmux_role == "list" and term.direction == "output"
        )

    def _readers(self, dest: WireEnd, changer: str) -> list[str]:
        """The nodes that read the wire at ``dest``. A tunnel of a structure that
        holds the changing node is read by what is wired to its inner side, not
        by the structure (which reaches every node inside it)."""
        if self._encloses(dest.node_id, changer):
            paired = getattr(self.get_terminal(dest.terminal_id), "paired_id", None)
            if paired is not None:
                return [w.node_id for w in self.outgoing_edges(paired)]
        return [dest.node_id]

    def _encloses(self, structure: str, node_id: str) -> bool:
        """Whether the node ``node_id`` lies inside the structure ``structure``."""
        node = self.get_graph_node(node_id)
        seen: set[str] = set()
        while node is not None and node.parent is not None and node.parent not in seen:
            if node.parent == structure:
                return True
            seen.add(node.parent)
            node = self.get_graph_node(node.parent)
        return False
