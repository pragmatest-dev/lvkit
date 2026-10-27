"""Queries over every VI of a generated package: the queues the VIs obtain and
the Shared Variables they use.

Nothing is stored. Each query traverses the loaded VIs' nodes and wires on demand
and returns typed results sorted so the answer does not depend on the order the
VIs are given in.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import LVType
from .models import (
    AnyGraphNode,
    ConstantNode,
    PrimitiveNode,
    SharedVariableNode,
    WireEnd,
)

# Obtain Queue's primResID and the connector-pane indices of the terminals read.
OBTAIN_QUEUE_PRIM_ID = 9108
_NAME, _MAX_SIZE, _CREATED = 0, 4, 10
_SHARED_VARIABLE_VALUE = 0


@dataclass(frozen=True)
class ObtainQueueUse:
    """One Obtain Queue node: the constant wired to its ``name`` (``name_wired``
    with no ``name`` constant is a name computed at run time), the constant wired
    to its ``max queue size``, and whether anything reads ``created?``."""

    node_id: str
    vi_key: str
    name: ConstantNode | None
    name_wired: bool
    max_size: ConstantNode | None
    max_size_wired: bool
    created_used: bool


@dataclass(frozen=True)
class SharedVariableUse:
    """One Shared Variable node whose library is found: the library's file path
    and name, the variable, and the type of its value."""

    node_id: str
    vi_key: str
    library_key: str
    library_name: str
    variable: str
    value_type: LVType | None


class PackageQueriesMixin:
    """Package-wide queries on ``InMemoryVIGraph``."""

    if TYPE_CHECKING:

        def iter_nodes(self, vi_name: str) -> list[AnyGraphNode]: ...
        def get_graph_node(self, node_id: str) -> AnyGraphNode | None: ...
        def incoming_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def outgoing_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def shared_variable_library_key(
            self, node: SharedVariableNode
        ) -> str | None: ...

    def _wired_constant(self, terminal_id: str) -> tuple[ConstantNode | None, bool]:
        """The constant that feeds ``terminal_id`` directly, and whether anything
        is wired to it."""
        sources = self.incoming_edges(terminal_id)
        for source in sources:
            node = self.get_graph_node(source.node_id)
            if isinstance(node, ConstantNode):
                return node, True
        return None, bool(sources)

    def obtain_queue_uses(self, vi_keys: Iterable[str]) -> list[ObtainQueueUse]:
        """Every Obtain Queue node of the VIs, sorted by node id."""
        uses: list[ObtainQueueUse] = []
        for vi_key in sorted(set(vi_keys)):
            for node in self.iter_nodes(vi_key):
                if (
                    not isinstance(node, PrimitiveNode)
                    or node.prim_id != OBTAIN_QUEUE_PRIM_ID
                ):
                    continue
                by_index = {t.index: t for t in node.terminals}
                name, name_wired = self._wired_constant(
                    by_index[_NAME].id if _NAME in by_index else ""
                )
                size, size_wired = self._wired_constant(
                    by_index[_MAX_SIZE].id if _MAX_SIZE in by_index else ""
                )
                created = by_index.get(_CREATED)
                uses.append(
                    ObtainQueueUse(
                        node_id=node.id,
                        vi_key=vi_key,
                        name=name,
                        name_wired=name_wired,
                        max_size=size,
                        max_size_wired=size_wired,
                        created_used=created is not None
                        and not created.is_error_cluster
                        and bool(self.outgoing_edges(created.id)),
                    )
                )
        return sorted(uses, key=lambda use: use.node_id)

    def shared_variable_uses(self, vi_keys: Iterable[str]) -> list[SharedVariableUse]:
        """Every Shared Variable node of the VIs whose library is found, sorted by
        library path, variable and node id."""
        uses: list[SharedVariableUse] = []
        for vi_key in sorted(set(vi_keys)):
            for node in self.iter_nodes(vi_key):
                if not isinstance(node, SharedVariableNode) or node.library is None:
                    continue
                library_key = self.shared_variable_library_key(node)
                if library_key is None:
                    continue
                value = next(
                    (t for t in node.terminals if t.index == _SHARED_VARIABLE_VALUE),
                    None,
                )
                uses.append(
                    SharedVariableUse(
                        node_id=node.id,
                        vi_key=vi_key,
                        library_key=library_key,
                        library_name=node.library.qualified_name,
                        variable=node.variable_name,
                        value_type=value.lv_type if value is not None else None,
                    )
                )
        return sorted(uses, key=lambda u: (u.library_key, u.variable, u.node_id))
