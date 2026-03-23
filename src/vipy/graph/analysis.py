"""Analysis mixin for InMemoryVIGraph.

Methods: find_branch_points, trace_branch, get_parallel_branches,
has_parallel_branches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..graph_types import (
    BranchPoint,
    ParallelBranch,
    VINode,
)
from .core import _OPERATION_KINDS, _graph_node_to_op_kind

if TYPE_CHECKING:
    import networkx as nx


class AnalysisMixin:
    """Mixin providing parallel branch detection and analysis."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]

    def find_branch_points(self, vi_name: str) -> list[BranchPoint]:
        """Find terminals where one output feeds multiple inputs."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        branch_points: list[BranchPoint] = []

        for uid in node_uids:
            if uid not in self._graph:
                continue
            successors = list(self._graph.successors(uid))
            if len(successors) > 1:
                gnode = self._graph.nodes[uid].get("node")
                source_op = uid if gnode else None

                branch_points.append(BranchPoint(
                    source_terminal=uid,
                    source_operation=source_op,
                    destinations=successors,
                    vi_name=vi_name,
                ))

        return branch_points

    def trace_branch(
        self,
        vi_name: str,
        start_terminal: str,
        all_branch_starts: set[str],
    ) -> ParallelBranch:
        """Trace a single branch from a start terminal to its merge point."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return ParallelBranch(
                branch_id=0,
                source_terminal=start_terminal,
                operation_ids=[],
                merge_terminal=None,
                merge_operation=None,
            )

        visited: set[str] = set()
        operations: list[str] = []
        merge_terminal: str | None = None
        merge_operation: str | None = None

        def trace(node_id: str) -> bool:
            nonlocal merge_terminal, merge_operation

            if node_id in visited:
                return False
            visited.add(node_id)

            if node_id not in self._graph:
                return False
            gnode = self._graph.nodes[node_id].get("node")

            # If we hit the VINode (an output terminal), branch ends
            if isinstance(gnode, VINode) and gnode.id == gnode.vi:
                merge_terminal = node_id
                return True

            # If this is an operation, collect it
            if gnode is not None:
                op_kind = _graph_node_to_op_kind(gnode)
                if op_kind in _OPERATION_KINDS:
                    operations.append(node_id)

            successors = list(self._graph.successors(node_id))

            for succ in successors:
                predecessors = list(self._graph.predecessors(succ))
                other_inputs = [
                    p for p in predecessors
                    if p != node_id and p in all_branch_starts
                ]
                if other_inputs:
                    merge_terminal = succ
                    merge_operation = succ
                    return True

                if trace(succ):
                    return True

            return False

        trace(start_terminal)

        return ParallelBranch(
            branch_id=0,
            source_terminal=start_terminal,
            operation_ids=operations,
            merge_terminal=merge_terminal,
            merge_operation=merge_operation,
        )

    def get_parallel_branches(
        self, vi_name: str
    ) -> list[tuple[BranchPoint, list[ParallelBranch]]]:
        """Get all parallel branch structures in a VI."""
        branch_points = self.find_branch_points(vi_name)
        result: list[tuple[BranchPoint, list[ParallelBranch]]] = []

        for bp in branch_points:
            branches: list[ParallelBranch] = []
            all_starts = set(bp.destinations)

            for i, dest in enumerate(bp.destinations):
                branch = self.trace_branch(vi_name, dest, all_starts)
                branch = ParallelBranch(
                    branch_id=i,
                    source_terminal=branch.source_terminal,
                    operation_ids=branch.operation_ids,
                    merge_terminal=branch.merge_terminal,
                    merge_operation=branch.merge_operation,
                )
                branches.append(branch)

            result.append((bp, branches))

        return result

    def has_parallel_branches(self, vi_name: str) -> bool:
        """Check if a VI has any parallel branch points."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return False

        for uid in node_uids:
            if uid not in self._graph:
                continue
            successors = list(self._graph.successors(uid))
            if len(successors) > 1:
                return True

        return False
