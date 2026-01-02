"""Shared dataclasses for VI graph representation.

These dataclasses provide typed access to graph nodes while maintaining
backward compatibility with dict-style access patterns (.get(), [], 'in').
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


class DictMixin:
    """Mixin to provide dict-like access to dataclass fields.

    Allows gradual migration from dict['key'] to obj.key syntax.
    """

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style get for backward compatibility."""
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        """Dict-style [] access."""
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        """Support 'in' operator."""
        return hasattr(self, key)

    def items(self) -> Iterator[tuple[str, Any]]:
        """Iterate over field name-value pairs."""
        for k in self.__dataclass_fields__:  # type: ignore
            yield k, getattr(self, k)

    def keys(self) -> Iterator[str]:
        """Iterate over field names."""
        return iter(self.__dataclass_fields__)  # type: ignore


@dataclass
class Terminal(DictMixin):
    """A terminal on an operation node."""

    id: str
    index: int
    direction: str  # "input" or "output"
    type: str = "Any"
    name: str | None = None
    typedef_path: str | None = None  # Filesystem path to .ctl
    typedef_name: str | None = None  # Qualified name (e.g., "sysdir.llb:Type.ctl")
    callee_param_name: str | None = None  # Name in SubVI's signature


@dataclass
class Tunnel(DictMixin):
    """A tunnel connecting loop outer/inner terminals."""

    outer_terminal_uid: str
    inner_terminal_uid: str
    tunnel_type: str  # "lSR", "rSR", "lpTun", "lMax"
    paired_terminal_uid: str | None = None


@dataclass
class Operation(DictMixin):
    """An operation node (SubVI, primitive, loop)."""

    id: str
    name: str | None
    labels: list[str]
    primResID: int | None = None
    terminals: list[Terminal] = field(default_factory=list)
    node_type: str | None = None
    loop_type: str | None = None
    tunnels: list[Tunnel] = field(default_factory=list)
    inner_nodes: list[Operation] = field(default_factory=list)
    stop_condition_terminal: str | None = None


@dataclass
class Constant(DictMixin):
    """A constant value node."""

    id: str
    value: Any
    type: str
    raw_value: str | None = None
    label: str | None = None


@dataclass
class FPTerminalNode(DictMixin):
    """A front panel terminal (input/output)."""

    id: str
    kind: str  # "input" or "output"
    name: str | None
    is_indicator: bool
    is_public: bool
    slot_index: int | None = None
    wiring_rule: int = 0
    type_desc: str | None = None
    control_type: str | None = None
    default_value: Any = None
    enum_values: list = field(default_factory=list)
    type: str | None = None  # Resolved type
    type_info: Any = None  # TypeInfo object (added during enrichment)


@dataclass
class Wire(DictMixin):
    """A wire (edge) in the dataflow graph."""

    from_terminal_id: str
    to_terminal_id: str
    from_parent_id: str | None = None
    to_parent_id: str | None = None
    from_parent_name: str | None = None
    to_parent_name: str | None = None
    from_parent_labels: list[str] = field(default_factory=list)
    to_parent_labels: list[str] = field(default_factory=list)
