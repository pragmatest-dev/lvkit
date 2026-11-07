"""The catalog entries a VI looks up, read from its graph.

Each catalog lookup the graph makes for a VI is keyed by something the graph
holds: a primitive node's primResID (``graph/construction.py``,
``graph/unwired_default.py``, ``render/nodes.py``), any node's node class
(``resolve_by_node_type``), the file name the catalog knows a subVI call by
(``VILibResolver.catalog_file_name`` of ``graph.catalog_path``), and the typedef
name a terminal's type carries (``graph/core.py`` ``_enrich_type``). These are
the keys a project's ``.lvkit/`` entries are compared with
(:mod:`lvkit.catalog_stamp`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog_stamp import CatalogKey, primitive_name_key
from .graph.models import PrimitiveNode, VINode
from .models import LVType
from .vilib_resolver import VILibResolver

if TYPE_CHECKING:
    from .graph.core import InMemoryVIGraph


def _typedef_names(lv_type: LVType | None, names: set[str]) -> None:
    if lv_type is None:
        return
    if lv_type.typedef_name:
        names.add(lv_type.typedef_name)
    _typedef_names(lv_type.element_type, names)
    _typedef_names(lv_type.value_type, names)
    for field in lv_type.fields or ():
        _typedef_names(field.type, names)


def catalog_keys(graph: InMemoryVIGraph, vi_key: str) -> frozenset[CatalogKey]:
    """Every catalog entry the VI ``vi_key`` looks up: for each node at every
    structure level, its node class and primResID, the catalog file name of the
    VI it calls, and the typedef names its terminals' types carry; and the VI's
    own catalog file name, when a caller records it as a vi.lib VI."""
    keys: set[CatalogKey] = set()
    typedefs: set[str] = set()
    own = graph.get_graph_node(vi_key)
    nodes = [*graph.iter_nodes(vi_key), *([own] if own is not None else [])]
    for node in nodes:
        if node.node_type:
            keys.add(CatalogKey("node_type", node.node_type))
        if isinstance(node, PrimitiveNode) and node.prim_id:
            keys.add(CatalogKey("primitive", str(node.prim_id)))
        if isinstance(node, PrimitiveNode) and node.name:
            # The render finds a primitive by name when its primResID is not
            # listed (render/nodes.py resolve(prim_id=..., name=...)).
            keys.add(CatalogKey("primitive_name", primitive_name_key(node.name)))
        if isinstance(node, VINode) and node.id != node.vi_path:
            file_name = VILibResolver.catalog_file_name(graph.catalog_path(node))
            if file_name is not None:
                keys.add(CatalogKey("vilib", file_name))
        for terminal in node.terminals:
            _typedef_names(terminal.lv_type, typedefs)
    file_name = VILibResolver.catalog_file_name(graph.recorded_path(vi_key))
    if file_name is not None:
        keys.add(CatalogKey("vilib", file_name))
    keys.update(CatalogKey("type", name) for name in typedefs)
    return frozenset(keys)
