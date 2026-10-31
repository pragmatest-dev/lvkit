"""Completing each Call Library Function Node's call from the main XML."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .extfunc import extfunc_display_name
from .extfunc_links import (
    complete_library,
    read_data_space_records,
    read_node_records,
)
from .extfunc_prototype import extfunc_params
from .models import ParsedNode
from .node_types import ExtFuncNode
from .type_mapping import TypeMap
from .xml_trees import parsed_xml


def resolve_ext_func_calls(
    nodes: list[ParsedNode],
    main_xml: Path | str | None,
    type_map: dict | None,
) -> None:
    """Fill in each Call Library Function Node's ``call.params`` from the main
    XML's type table and its function, library name and recorded path from the
    VI's link records, in place. The node is named after the whole function."""
    pending = [n for n in nodes if isinstance(n, ExtFuncNode)]
    if not pending or main_xml is None or not isinstance(type_map, TypeMap):
        return
    root = parsed_xml(Path(main_xml))
    node_records = read_node_records(root, Path(main_xml))
    data_space_records = read_data_space_records(root)
    for node in pending:
        completed = complete_library(
            node.call, node.uid, node_records, data_space_records
        )
        if completed.function != node.call.function:
            node.name = extfunc_display_name(node.call.library, completed.function)
        node.call = completed
        if node.call.func_td is not None:
            node.call = replace(
                node.call, params=extfunc_params(root, type_map, node.call.func_td)
            )
