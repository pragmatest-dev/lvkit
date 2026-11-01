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
from .extfunc_bounds import EXT_FUNC_ARG_CLASS, extfunc_mirrored_bounds, row_pairs
from .extfunc_prototype import ExtFuncCall, ExtFuncThread
from .flags import is_ext_func_return_value, is_output_terminal
from .metadata import _decode_pth0_components
from .type_mapping import TypeMap
from .type_resolution import type_id_number
from .utils import clean_labview_string, safe_int

NODE_CLASS_EXT_FUNC = "extFunc"
PATH_ERROR_DCO_CLASS = "hGrowCItem"
_CALLING_CONVENTIONS = {0: "stdcall", 1: "c"}
RETURN_VALUE_NAME = "return value"
# A parameter's output side is named like its input side plus this suffix.
OUTPUT_SUFFIX = " output"


def extfunc_display_name(library_file: str | None, function: str) -> str:
    """``<library file>:<function>``; just the function name when the node's
    library path is empty (the library is wired in at run time)."""
    return f"{library_file}:{function}" if library_file else function


def extfunc_function_name(elem: ET.Element) -> str | None:
    """``<library file>:<function>`` of a Call Library Function Node, from its
    ``<libPath>`` (a ``PTH0`` path) and ``<nodeName>``, which LabVIEW cuts to 31
    characters (the whole name is in the VI's link record, see
    ``extfunc_links``). ``None`` when the node records no function name."""
    function = clean_labview_string(elem.findtext("nodeName"))
    if not function:
        return None
    lib_path = bytes.fromhex(elem.findtext("libPath") or "")
    components, _ = _decode_pth0_components(lib_path, 0, len(lib_path))
    return extfunc_display_name(components[-1] if components else None, function)


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


def _row_terminal_ids(elem: ET.Element) -> tuple[tuple[str | None, str], ...]:
    """Each row's (input, output) terminal uids; the return value row has no
    input terminal."""
    rows: list[tuple[str | None, str]] = []
    for dco, input_uid, output_uid in row_pairs(elem):
        flags = safe_int(dco.find("objFlags"))
        is_return = is_ext_func_return_value(dco.get("class"), flags)
        rows.append((None if is_return else input_uid, output_uid))
    return tuple(rows)


def _thread(elem: ET.Element) -> ExtFuncThread | None:
    value = int(elem.findtext("threadInfo") or "0", 16)
    try:
        return ExtFuncThread(value)
    except ValueError:
        return None


def extfunc_call(elem: ET.Element) -> ExtFuncCall:
    """The node's call, without its parameter types (see ``extfunc_params``).

    The function is the node's ``<nodeName>``, which LabVIEW cuts to 31
    characters; ``extfunc_links.complete_library`` replaces it with the whole name
    from the VI's link record.

    The calling convention is the node's ``<callType>``: absent is stdcall (every
    kernel32 node in the corpus), ``01`` is C."""
    lib_path = bytes.fromhex(elem.findtext("libPath") or "")
    components, _ = _decode_pth0_components(lib_path, 0, len(lib_path))
    call_type = int(elem.findtext("callType") or "0", 16)
    path_in = next(
        (
            term.get("uid")
            for term in elem.findall("./termList/SL__arrayElement")
            if (dco := term.find("dco")) is not None
            and dco.get("class") == PATH_ERROR_DCO_CLASS
            and not is_output_terminal(safe_int(dco.find("objFlags")))
        ),
        None,
    )
    func_td = elem.findtext("funcTD")
    return ExtFuncCall(
        library=components[-1] if components else None,
        function=clean_labview_string(elem.findtext("nodeName")),
        calling_convention=_CALLING_CONVENTIONS.get(call_type, f"unknown({call_type})"),
        path_in_terminal_id=path_in,
        row_terminal_ids=_row_terminal_ids(elem),
        func_td=int(func_td, 16) if func_td else None,
        thread=_thread(elem),
    )


@dataclass(frozen=True)
class ExtFuncRow:
    """One drawn row of a Call Library Function Node: its vertical span as a
    fraction of the node's height and which sides carry a terminal."""

    top: float
    bottom: float
    has_input: bool
    has_output: bool
    # A path or error row (the node's own DCOs), as opposed to a parameter or
    # return-value row.
    fixed: bool = False


def extfunc_rows(elem: ET.Element) -> list[ExtFuncRow]:
    """The node's rows, top to bottom, from its terminals' ``<termBounds>``,
    as fractions of the vertical extent those terminals span (the extent the
    node is drawn over)."""
    roles = extfunc_terminal_roles(elem, None)
    mirrored = extfunc_mirrored_bounds(elem)
    spans: list[tuple[int, int, bool, bool]] = []  # top, bottom, output, fixed
    for term in elem.findall("./termList/SL__arrayElement"):
        uid = term.get("uid") or ""
        role = roles.get(uid)
        if role is None or role.dropped:
            continue
        text = term.findtext(".//termBounds")
        if text:
            top, _, bottom, _ = (int(v) for v in text.strip("()").split(","))
        elif uid in mirrored:
            top, _, bottom, _ = mirrored[uid]
        else:
            continue
        dco = term.find("dco")
        fixed = dco is not None and dco.get("class") == PATH_ERROR_DCO_CLASS
        spans.append((top, bottom, role.is_output, fixed))
    if not spans:
        return []
    extent_top = min(top for top, _, _, _ in spans)
    extent = max(bottom for _, bottom, _, _ in spans) - extent_top
    if extent <= 0:
        return []
    sides: dict[tuple[int, int], list[bool]] = {}
    for top, bottom, is_output, fixed in spans:
        present = sides.setdefault((top, bottom), [False, False, fixed])
        present[1 if is_output else 0] = True
    return [
        ExtFuncRow(
            (top - extent_top) / extent,
            (bottom - extent_top) / extent,
            has_input,
            has_output,
            fixed,
        )
        for (top, bottom), (has_input, has_output, fixed) in sorted(sides.items())
    ]


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
