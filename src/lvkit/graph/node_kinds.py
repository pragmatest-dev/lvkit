"""The closed sets of dependency-graph vocabulary: what kind of file a node is
(``node_type``) and what an edge means (``rel``). Both are ``(str, Enum)`` so a
member equals the raw string the graph stores, drop-in for ``== "class"``."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    """The ``node_type`` a dependency-graph node carries."""

    VI = "vi"
    CLASS = "class"
    LIBRARY = "library"
    TYPEDEF = "typedef"
    UNKNOWN = "unknown"


class EdgeRel(str, Enum):
    """The ``rel`` an ownership edge carries (a plain reference has none).

    ``OWNS``: a library owns its member VIs / controls / classes, and a class
    owns its method VIs. ``PRIVATE_DATA``: a class -> the control that is its
    private data -- kept apart from ``OWNS`` because a class's ``OWNS`` edges are
    its method list."""

    OWNS = "owns"
    PRIVATE_DATA = "private_data"


# The rels by which one node OWNS another; any other incoming edge is a plain
# reference.
OWNERSHIP_RELS = (EdgeRel.OWNS, EdgeRel.PRIVATE_DATA)


def node_type_of(data: Mapping[str, Any]) -> NodeType:
    """A dep-graph node's type. A loaded VI carries no ``node_type`` (the
    graph's default is a VI); any other value must be a known ``NodeType`` or
    this raises."""
    return NodeType(data.get("node_type", NodeType.VI))
