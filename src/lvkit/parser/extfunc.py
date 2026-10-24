"""Terminal roles of a Call Library Function Node (``class="extFunc"``).

Every parameter row and the return-value row is one ``extFuncArg`` DCO owned by
the row's left terminal. The DCO's own ``<termList>`` names the row's two
terminals in order: the input side, then the output side. The output side's
``<term>`` holds only a bare ``<dco uid=.../>`` reference back to that DCO, so
its direction and name come from here instead of from the referenced DCO's
flags. The node's ``<funcTD>`` is a Cluster whose fields are the function's
prototype, one per row in the same order.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..models import LVType, LVTypeKind
from .flags import is_ext_func_return_value, is_output_terminal
from .metadata import _decode_pth0_components
from .type_mapping import TypeMap
from .type_resolution import type_id_number
from .utils import clean_labview_string, safe_int

NODE_CLASS_EXT_FUNC = "extFunc"
EXT_FUNC_ARG_CLASS = "extFuncArg"
PATH_ERROR_DCO_CLASS = "hGrowCItem"
RETURN_VALUE_NAME = "return value"
# A parameter's output side is named like its input side plus this suffix.
OUTPUT_SUFFIX = " output"


def extfunc_function_name(elem: ET.Element) -> str | None:
    """``<library file>:<function>`` of a Call Library Function Node, from its
    ``<libPath>`` (a ``PTH0`` path) and ``<nodeName>``. A node whose library
    path is empty (the library is wired in at run time) is just the function
    name; ``None`` when the node records no function name."""
    function = clean_labview_string(elem.findtext("nodeName"))
    if not function:
        return None
    lib_path = bytes.fromhex(elem.findtext("libPath") or "")
    components, _ = _decode_pth0_components(lib_path, 0, len(lib_path))
    return f"{components[-1]}:{function}" if components else function


@dataclass(frozen=True)
class ExtFuncTerminalRole:
    """What a Call Library Function Node's row says about one of its terminals."""

    is_output: bool
    name: str | None
    # The return-value row's input side: a terminal LabVIEW stores but the node
    # does not have.
    dropped: bool = False


def _prototype_names(elem: ET.Element, type_map: dict[int, LVType] | None) -> list[str]:
    """The prototype's field names in row order; empty when ``<funcTD>`` is not
    a Cluster in ``type_map``."""
    func_td = elem.findtext("funcTD")
    if not func_td or not type_map:
        return []
    try:
        prototype = type_map.get(int(func_td, 16))
    except ValueError:
        return []
    if prototype is None or prototype.kind != LVTypeKind.CLUSTER:
        return []
    return [f.name for f in prototype.fields or []]


def _row_name(
    label: str | None, names: list[str], row: int, is_return: bool
) -> str | None:
    """The row's name: the label of the type its DCO references, else its
    prototype field name. An unnamed field arrives from the type reader as
    ``field_<row>``; it is nameless, and the return value falls back to
    ``return value``."""
    if label:
        return label
    name = names[row] if row < len(names) else ""
    if not name or name == f"field_{row}":
        return RETURN_VALUE_NAME if is_return else None
    return name


def extfunc_terminal_roles(
    elem: ET.Element, type_map: dict[int, LVType] | None
) -> dict[str, ExtFuncTerminalRole]:
    """Role of each terminal of a Call Library Function Node, by terminal uid.

    The path and error terminals each have their own DCO: direction from its
    flags, name from the label of the type it references. A row's terminals
    come from its ``extFuncArg`` DCO."""
    names = _prototype_names(elem, type_map)
    labels = type_map.labels if isinstance(type_map, TypeMap) else {}
    roles: dict[str, ExtFuncTerminalRole] = {}
    for term in elem.findall("./termList/SL__arrayElement"):
        dco = term.find("dco")
        term_uid = term.get("uid")
        if dco is None or dco.get("class") != PATH_ERROR_DCO_CLASS or not term_uid:
            continue
        roles[term_uid] = ExtFuncTerminalRole(
            is_output=is_output_terminal(safe_int(dco.find("objFlags"))),
            name=labels.get(type_id_number(dco.findtext("typeDesc")) or -1),
        )
    rows = elem.findall(
        f"./termList/SL__arrayElement/dco[@class='{EXT_FUNC_ARG_CLASS}']"
    )
    for row, dco in enumerate(rows):
        sides = [t.get("uid") for t in dco.findall("termList/SL__arrayElement")]
        if len(sides) != 2 or not sides[0] or not sides[1]:
            continue
        is_return = is_ext_func_return_value(
            dco.get("class"), safe_int(dco.find("objFlags"))
        )
        label = labels.get(type_id_number(dco.findtext("typeDesc")) or -1)
        name = _row_name(label, names, row, is_return)
        roles[sides[0]] = ExtFuncTerminalRole(
            is_output=False, name=name, dropped=is_return
        )
        output_name = name if is_return or name is None else f"{name}{OUTPUT_SUFFIX}"
        roles[sides[1]] = ExtFuncTerminalRole(is_output=True, name=output_name)
    return roles
