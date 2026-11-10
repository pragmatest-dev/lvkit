"""What Array To Cluster and Cluster To Array share: the cluster type of the
node's cluster terminal."""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind, _is_error_cluster

from ._flat_node import terminal


def cluster_type(node: PrimitiveNode, direction: str) -> LVType:
    """The cluster type of the node's cluster terminal (index 0 out of Array To
    Cluster, index 1 into Cluster To Array). A cluster with no type, no fields, an
    error cluster or a class has no Python value to build or read."""
    index = 0 if direction == "output" else 1
    term = terminal(node, direction, index)
    lv_type = term.lv_type if term is not None else None
    if lv_type is None or lv_type.kind not in (
        LVTypeKind.CLUSTER,
        LVTypeKind.TYPEDEF_REF,
    ):
        raise ConversionGap(
            f"{node.name} has no cluster type on its cluster terminal", node
        )
    if _is_error_cluster(lv_type) or lv_type.classname:
        raise ConversionGap(
            f"{node.name} of an error cluster or a class has no Python equivalent",
            node,
        )
    if not lv_type.fields:
        raise ConversionGap(f"{node.name} has a cluster with no fields", node)
    return lv_type
