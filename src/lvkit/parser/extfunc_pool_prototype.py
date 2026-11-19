"""A Call Library Function Node's prototype in a pre-LV9 VI.

A VI saved before LabVIEW 8.6 has no ``VCTP``: the node's ``<funcTD>`` is a heap
``TypeID`` of the ``DTHP`` pool (see ``heap_types``), whose descriptor is a
Cluster with one member per prototype row, the return value first. Each member is
read by the rules ``extfunc_prototype`` applies to a ``VCTP`` descriptor, from the
descriptor bytes: a pointer (``PtrTo``) holds the index of the type it points at;
an array (``Array``, ``ArrayDataPtr``) holds its dimension count, the dimension
sizes and the index of its element type.
"""

from __future__ import annotations

import struct

from ..lv_type_codes import TD_FULL_TYPE
from .extfunc_prototype import (
    NUMERIC_C_TYPES,
    ExtFuncParam,
    ParamKind,
    ParamTd,
    param_from_td,
)
from .heap_types import read_heap_pool
from .td_pool import RawTD, read_var_size

_POINTER_TYPES = frozenset({TD_FULL_TYPE.PtrTo.name})
_ARRAY_TYPES = frozenset({TD_FULL_TYPE.Array.name, TD_FULL_TYPE.ArrayDataPtr.name})


def _type_name(td: RawTD) -> str:
    try:
        return TD_FULL_TYPE(td.type_code).name
    except ValueError:
        return f"type code 0x{td.type_code:02X}"


def _child_index(td: RawTD, name: str) -> tuple[int, int] | None:
    """``(dimension count, index of the type it points at)`` of a pointer or an
    array descriptor; None when its body is too short."""
    if name in _POINTER_TYPES:
        pointee = read_var_size(td.body, 0)
        return (0, pointee[0]) if pointee else None
    if len(td.body) < 2:
        return None
    (ndims,) = struct.unpack_from(">H", td.body, 0)
    element = read_var_size(td.body, 2 + 4 * ndims)
    return (ndims, element[0]) if element else None


def _param_td(tds: list[RawTD], index: int, depth: int = 1) -> ParamTd:
    td = tds[index]
    name = _type_name(td)
    child = None
    dimensions = 0
    if depth > 0 and (name in _POINTER_TYPES or name in _ARRAY_TYPES):
        located = _child_index(td, name)
        if located is not None and located[1] < len(tds):
            dimensions, child_index = located
            child = _param_td(tds, child_index, depth - 1)
    return ParamTd(name, NUMERIC_C_TYPES.get(name), dimensions, child)


def pool_params(dthp_bytes: bytes, func_td: int) -> tuple[ExtFuncParam, ...]:
    """The parameters of the prototype the heap ``TypeID`` ``func_td`` names, in
    row order; empty when the pool cannot be read or the type is not a Cluster."""
    pool = read_heap_pool(dthp_bytes)
    if isinstance(pool, str) or not 1 <= func_td <= len(pool.top):
        return ()
    index = pool.top[func_td - 1]
    if index >= len(pool.tds) or pool.tds[index].type_code != TD_FULL_TYPE.Cluster:
        return ()
    body = pool.tds[index].body
    counted = read_var_size(body, 0)
    if counted is None:
        return ()
    count, pos = counted
    params: list[ExtFuncParam] = []
    unreadable = ExtFuncParam(ParamKind.UNSUPPORTED, reason="an unreadable type")
    for row in range(count):
        member = read_var_size(body, pos)
        if member is None:
            params.append(unreadable)
            break
        pos = member[1]
        if member[0] >= len(pool.tds):
            params.append(unreadable)
        else:
            params.append(param_from_td(_param_td(pool.tds, member[0]), row == 0))
    return tuple(params)
