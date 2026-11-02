"""Which nodes of a diagram handle errors: the vi.lib error VIs a call site
generates itself, the Merge Errors primitive and the error case selector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.graph.models import (
    AnyGraphNode,
    CaseStructureNode,
)
from lvkit.models import _is_error_cluster
from lvkit.parser.constants import (
    VI_NAME_CLEAR_ERRORS,
    VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE,
    VI_NAME_MERGE_ERRORS,
    VI_NAME_SIMPLE_ERROR_HANDLER,
)

if TYPE_CHECKING:
    from lvkit.vilib_resolver import VILibResolver

# Merge Errors primitive; not 2401, which is Swap Values (#59).
PRIM_ID_MERGE_ERRORS = 2147

# The vi.lib error VIs a call site generates itself, never as a call of a
# generated module.
ERROR_WIRE_VI_NAMES = frozenset(
    {
        VI_NAME_MERGE_ERRORS,
        VI_NAME_CLEAR_ERRORS,
        VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE,
        VI_NAME_SIMPLE_ERROR_HANDLER,
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
