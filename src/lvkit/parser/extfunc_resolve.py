"""Completing each Call Library Function Node's call from the main XML."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
    XML's type table, in place."""
    pending = [n for n in nodes if isinstance(n, ExtFuncNode)]
    if not pending or main_xml is None or not isinstance(type_map, TypeMap):
        return
    root = parsed_xml(Path(main_xml))
    for node in pending:
        if node.call.func_td is not None:
            node.call = replace(
                node.call, params=extfunc_params(root, type_map, node.call.func_td)
            )
