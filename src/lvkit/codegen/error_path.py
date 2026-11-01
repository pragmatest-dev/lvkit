"""Which nodes of a diagram handle errors, and which nodes produce an error a
handler receives.

An error cluster wire is not carried into the generated code: a failure raises
``LabVIEWError`` (or the Python exception of the call that failed) and a handler
catches it. A handler therefore needs the statements that can raise the error it
handles, which are the nodes upstream of it on its error wire.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from lvkit.graph.models import (
    AnyGraphNode,
    CaseStructureNode,
    PrimitiveNode,
    VINode,
)
from lvkit.models import _is_error_cluster
from lvkit.parser.constants import (
    VI_NAME_CLEAR_ERRORS,
    VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE,
    VI_NAME_MERGE_ERRORS,
)

if TYPE_CHECKING:
    from lvkit.codegen.context import CodeGenContext
    from lvkit.vilib_resolver import VILibResolver

# Merge Errors primitive; not 2401, which is Swap Values (#59).
PRIM_ID_MERGE_ERRORS = 2147

# The vi.lib VIs whose call is generated as exception handling at the call site,
# never as a call of a generated module.
ERROR_WIRE_VI_NAMES = frozenset(
    {
        VI_NAME_MERGE_ERRORS,
        VI_NAME_CLEAR_ERRORS,
        VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE,
    }
)


def is_error_wire_vi(recorded_path: str | None, vilib_resolver: VILibResolver) -> bool:
    """Whether the VI a call records ``recorded_path`` for is one of the vi.lib
    error VIs a call site handles. A project VI that shares one's file name is
    not."""
    return (
        vilib_resolver.catalog_file_name(recorded_path) in ERROR_WIRE_VI_NAMES
        and vilib_resolver.resolve_recorded(recorded_path) is not None
    )


class ErrorRole(Enum):
    """What a node does with an error wire."""

    NONE = auto()
    MERGE = auto()
    CLEAR = auto()
    CASE = auto()


def is_error_selector(op: AnyGraphNode) -> bool:
    """Whether ``op`` is a case structure selected by an error cluster."""
    if not isinstance(op, CaseStructureNode) or not op.selector_terminal:
        return False
    return any(
        term.id == op.selector_terminal
        and term.lv_type is not None
        and _is_error_cluster(term.lv_type)
        for term in op.terminals
    )


def classify_error_node(op: AnyGraphNode) -> ErrorRole:
    """The role ``op`` plays on an error wire, from the node itself."""
    if isinstance(op, PrimitiveNode) and op.prim_id == PRIM_ID_MERGE_ERRORS:
        return ErrorRole.MERGE
    if isinstance(op, VINode):
        if op.name == VI_NAME_MERGE_ERRORS:
            return ErrorRole.MERGE
        if op.name == VI_NAME_CLEAR_ERRORS:
            return ErrorRole.CLEAR
    if is_error_selector(op):
        return ErrorRole.CASE
    return ErrorRole.NONE


@dataclass(frozen=True)
class ErrorPath:
    """The nodes an error wire comes through, and where it starts."""

    # The nodes among ``operations`` that the error can come from.
    op_ids: frozenset[str]
    # The terminals the wire starts from outside ``operations`` (a tunnel, a
    # control, a constant).
    roots: frozenset[str]


def error_inputs(op: AnyGraphNode) -> list[str]:
    """The ids of ``op``'s error cluster input terminals."""
    return [t.id for t in op.terminals if t.direction == "input" and t.is_error_cluster]


def trace_error_path(
    starts: Iterable[str],
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    stops: Callable[[AnyGraphNode], bool] | None = None,
) -> ErrorPath:
    """Trace the error wires arriving at the terminals ``starts`` back through
    ``operations``.

    The trace follows each node's error inputs, so a Merge Errors passes it on
    through every input; a Clear Errors ends it, since nothing past it carries
    the error, and so does a node for which ``stops`` is true.
    """
    output_to_op: dict[str, AnyGraphNode] = {}
    for op in operations:
        for term in op.terminals:
            if term.direction == "output":
                output_to_op[term.id] = op

    op_ids: set[str] = set()
    roots: set[str] = set()
    pending = list(starts)
    visited: set[str] = set()
    while pending:
        tid = pending.pop()
        if tid in visited:
            continue
        visited.add(tid)
        source = ctx.get_source(tid)
        if source is None:
            continue
        producer = output_to_op.get(source.src_terminal)
        if producer is None:
            roots.add(source.src_terminal)
            continue
        if classify_error_node(producer) is ErrorRole.CLEAR or (
            stops and stops(producer)
        ):
            continue
        op_ids.add(producer.id)
        pending.extend(error_inputs(producer))
    return ErrorPath(frozenset(op_ids), frozenset(roots))


def error_reaches(
    starts: Iterable[str],
    sources: set[str],
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
) -> bool:
    """Whether the error wires arriving at the terminals ``starts`` carry the
    error that comes in at one of the terminals ``sources``, directly or through
    ``operations``. Every node in between is taken as passing its error in to its
    error out (see :func:`trace_error_path`: a Clear Errors, or a node whose
    error in is unwired, does not)."""
    return bool(trace_error_path(starts, operations, ctx).roots & sources)
