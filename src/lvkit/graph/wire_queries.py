"""Queries over the wires into and out of one terminal, and over the Local
Variables of one VI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import FPTerminal, Terminal
from .models import AnyGraphNode, LocalVariableNode, WireEnd


class WireQueriesMixin:
    """Wire and Local Variable queries on ``InMemoryVIGraph``."""

    if TYPE_CHECKING:
        _term_to_node: dict[str, str]

        def iter_nodes(self, vi_name: str) -> list[AnyGraphNode]: ...
        def get_graph_node(self, node_id: str) -> AnyGraphNode | None: ...
        def get_terminal(self, terminal_id: str) -> Terminal | None: ...
        def incoming_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def outgoing_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def terminal_vi(self, terminal_id: str) -> str | None: ...

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

    def terminal_fed_by_error_cluster(self, terminal_id: str) -> bool:
        """Whether a wire into the terminal comes from a terminal that carries an
        error cluster, which has no value."""
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
