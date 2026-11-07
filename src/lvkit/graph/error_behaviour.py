"""What a VI does with the error that comes in on its error in, read from its
own diagram.

A VI's error out carries only its error in when every wire into it comes,
through wires and structure tunnels, from the error in, or from the error out
of a call of a VI that itself cannot produce an error of its own. Anything else
on the way (a subVI that can fail, a primitive, a Bundle into the error, an
error constant, Merge Errors, ...) can put an error of the VI's own on it.

When it cannot, the incoming error decides only whether the VI's work runs:

* not at all: the error in reaches nothing but the error out
  (``ErrorBehaviour.RUNS_REGARDLESS``);
* the work is skipped: every node that does work sits in the No Error frame of
  an error case structure the error in selects, or is a call, on the error in's
  chain, of a VI that skips its work (``ErrorBehaviour.SKIPS_ON_ERROR``);
* any other way, such as work in an Error frame, or a node that reads the
  error's status (``ErrorBehaviour.DEPENDS_ON_ERROR``)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import TYPE_CHECKING

from ..models import FPTerminal, Terminal
from .models import (
    AnyGraphNode,
    CaseStructureNode,
    ConstantNode,
    LabelNode,
    StructureNode,
    VINode,
    WireEnd,
)
from .operations import frame_key


class ErrorBehaviour(Enum):
    """What a VI does with the error that comes in on its error in."""

    RUNS_REGARDLESS = "runs regardless"
    SKIPS_ON_ERROR = "skips on error"
    DEPENDS_ON_ERROR = "depends on error"
    CAN_FAIL = "can fail"

    @property
    def passes_error_along(self) -> bool:
        """Whether the VI's error out is its error in and the error decides at
        most whether all of its work runs."""
        return self in (ErrorBehaviour.RUNS_REGARDLESS, ErrorBehaviour.SKIPS_ON_ERROR)


class ErrorBehaviourMixin:
    """``vi_error_behaviour`` on ``InMemoryVIGraph``."""

    if TYPE_CHECKING:

        def iter_nodes(self, vi_name: str) -> list[AnyGraphNode]: ...
        def get_graph_node(self, node_id: str) -> AnyGraphNode | None: ...
        def get_terminal(self, terminal_id: str) -> Terminal | None: ...
        def is_stub(self, key: str) -> bool: ...
        def wire_readers(self, terminal_id: str) -> list[WireEnd]: ...
        def wire_origins(
            self, terminal_id: str, _seen: frozenset[str] = frozenset()
        ) -> list[tuple[AnyGraphNode, WireEnd]]: ...

    def vi_error_behaviour(self, vi_key: str) -> ErrorBehaviour:
        """What the VI at ``vi_key`` does with the error on its error in (see
        the module docstring). A VI that is not loaded can fail."""
        return self._error_behaviour(vi_key, frozenset())

    def _error_behaviour(self, vi_key: str, active: frozenset[str]) -> ErrorBehaviour:
        vi = self.get_graph_node(vi_key)
        if vi is None or self.is_stub(vi_key) or vi_key in active:
            # A VI that calls itself is taken to be able to fail.
            return ErrorBehaviour.CAN_FAIL
        active = active | {vi_key}
        pane = [t for t in vi.terminals if isinstance(t, FPTerminal) and t.is_public]
        errors_in = [t for t in pane if not t.is_indicator and t.is_error_cluster]
        errors_out = [t for t in pane if t.is_indicator and t.is_error_cluster]
        error_in_ids = {t.id for t in errors_in}
        if not self._carries_only(errors_out, error_in_ids, active):
            return ErrorBehaviour.CAN_FAIL
        reached = self._error_in_readers(errors_in, active)
        if reached is None:
            return ErrorBehaviour.DEPENDS_ON_ERROR
        cases, skipping_calls = reached
        states = {
            self._work_state(node, cases, skipping_calls)
            for node in self.iter_nodes(vi_key)
            if not isinstance(node, StructureNode | ConstantNode | LabelNode)
        }
        if states <= {_Work.RUNS}:
            return ErrorBehaviour.RUNS_REGARDLESS
        if states == {_Work.SKIPS}:
            return ErrorBehaviour.SKIPS_ON_ERROR
        return ErrorBehaviour.DEPENDS_ON_ERROR

    def _carries_only(
        self,
        errors_out: list[FPTerminal],
        error_in_ids: set[str],
        active: frozenset[str],
    ) -> bool:
        """Whether every wire into the error outs comes from an error in, through
        tunnels and through calls of VIs that pass their error along."""
        todo = [t.id for t in errors_out]
        seen: set[str] = set()
        while todo:
            terminal_id = todo.pop()
            if terminal_id in seen:
                continue
            seen.add(terminal_id)
            for node, source in self.wire_origins(terminal_id):
                if source.terminal_id in error_in_ids:
                    continue
                if not self._passes_along(node, active):
                    return False
                todo.extend(t.id for t in _error_terminals(node, "input"))
        return True

    def _error_in_readers(
        self, errors_in: list[FPTerminal], active: frozenset[str]
    ) -> tuple[dict[str, set[str | int | None]], set[str]] | None:
        """Follow the error in to everything that reads it: the error case
        structures it selects (each with the keys of its No Error frames) and
        the calls on its chain of VIs that skip their work. ``None`` when a node
        that is neither reads it."""
        cases: dict[str, set[str | int | None]] = {}
        skipping_calls: set[str] = set()
        todo = [t.id for t in errors_in]
        seen: set[str] = set()
        while todo:
            terminal_id = todo.pop()
            if terminal_id in seen:
                continue
            seen.add(terminal_id)
            for dest in self.wire_readers(terminal_id):
                term = self.get_terminal(dest.terminal_id)
                if isinstance(term, FPTerminal) and term.is_indicator:
                    continue
                node = self.get_graph_node(dest.node_id)
                if isinstance(node, StructureNode):
                    if (
                        isinstance(node, CaseStructureNode)
                        and node.selector_terminal == dest.terminal_id
                    ):
                        cases[node.id] = _no_error_keys(node)
                    todo.append(dest.terminal_id)
                    continue
                if (
                    node is None
                    or term is None
                    or not term.is_error_cluster
                    or not self._passes_along(node, active)
                ):
                    return None
                assert isinstance(node, VINode)
                if (
                    self._callee_behaviour(node, active)
                    is ErrorBehaviour.SKIPS_ON_ERROR
                ):
                    skipping_calls.add(node.id)
                todo.extend(t.id for t in _error_terminals(node, "output"))
        return cases, skipping_calls

    def _passes_along(self, node: AnyGraphNode, active: frozenset[str]) -> bool:
        """Whether ``node`` is a call of a VI whose error out is its error in."""
        return (
            isinstance(node, VINode)
            and self._callee_behaviour(node, active).passes_error_along
        )

    def _callee_behaviour(self, node: VINode, active: frozenset[str]) -> ErrorBehaviour:
        callee = node.variant_key or node.callee_key
        if callee is None:
            return ErrorBehaviour.CAN_FAIL
        return self._error_behaviour(callee, active)

    def _work_state(
        self,
        node: AnyGraphNode,
        cases: dict[str, set[str | int | None]],
        skipping_calls: set[str],
    ) -> _Work:
        """Whether the work of ``node`` is skipped on an incoming error, runs
        regardless, or runs only on one (it is in an Error frame)."""
        if node.id in skipping_calls:
            return _Work.SKIPS
        current: AnyGraphNode | None = node
        while current is not None and current.parent is not None:
            no_error = cases.get(current.parent)
            if no_error is not None:
                return _Work.SKIPS if current.frame in no_error else _Work.ON_ERROR
            current = self.get_graph_node(current.parent)
        return _Work.RUNS


class _Work(Enum):
    RUNS = "runs"
    SKIPS = "skips"
    ON_ERROR = "on error"


def _no_error_keys(case: CaseStructureNode) -> set[str | int | None]:
    """The keys the nodes of the case structure's No Error frames have."""
    return {
        frame_key(frame, position)
        for position, frame in enumerate(case.frames)
        if frame.is_no_error
    }


def _error_terminals(node: AnyGraphNode, direction: str) -> Iterable[Terminal]:
    return (
        t for t in node.terminals if t.direction == direction and t.is_error_cluster
    )
