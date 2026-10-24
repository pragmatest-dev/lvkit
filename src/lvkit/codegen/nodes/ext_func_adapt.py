"""An Adapt to Type parameter of a Call Library Function Node: how its wire type
is passed.

NI ("Configuring the Call Library Function Node"): "Use Adapt to Type to pass
arbitrary LabVIEW data types to DLLs in the following ways: Scalars are passed by
reference. A pointer to the scalar is passed to the library. Arrays and strings
are passed according to the Data Format setting ... Clusters are passed by
reference. Scalar elements in arrays or clusters are in line. For example, a
cluster containing a numeric is passed as a pointer to a structure containing a
numeric. Clusters within arrays are in line. Strings and arrays within clusters
are referenced by a handle."

A numeric, Boolean or cluster wire type is therefore passed as a pointer to its
value; a string, array or path at the top level depends on the Data Format
setting, which no corpus node uses and so is not decoded.
"""

from __future__ import annotations

from typing import Any

from lvkit.models import LVType, LVTypeKind, unique_names
from lvkit.parser.extfunc_prototype import NUMERIC_C_TYPES, array_handle_problem

from ..ast_utils import to_var_name

Spec = Any  # see ``lvkit.runtime.clfn``


def _scalar(lv_type: LVType) -> str | None:
    if lv_type.underlying_type == "Boolean":
        return "bool"  # LabVIEW stores a Boolean as 8 bits
    return NUMERIC_C_TYPES.get(lv_type.underlying_type or "")


def _member(lv_type: LVType | None) -> tuple[Spec | None, str]:
    """A cluster member's spec, or why it has none."""
    if lv_type is None:
        return None, "a member of unknown type"
    if (scalar := _scalar(lv_type)) is not None:
        return scalar, ""
    if lv_type.kind == LVTypeKind.CLUSTER:
        return cluster_spec(lv_type)
    if lv_type.underlying_type == "String":
        return ("string",), ""
    if lv_type.kind == LVTypeKind.ARRAY:
        element = lv_type.element_type
        c_type = NUMERIC_C_TYPES.get((element.underlying_type if element else "") or "")
        if c_type is None:
            return None, "an array of something other than numbers"
        if problem := array_handle_problem(c_type):
            return None, problem
        return ("array", c_type, lv_type.dimensions or 1), ""
    return None, f"a {lv_type.underlying_type or lv_type.kind.value} member"


def cluster_spec(lv_type: LVType) -> tuple[Spec | None, str]:
    """The spec of a cluster: a C structure of its members, or why it has none.
    A cluster with a typedef or class name is a ``SimpleNamespace`` in generated
    code and an anonymous one a tuple."""
    members = []
    names = unique_names(f.name for f in lv_type.fields or [])
    for field, name in zip(lv_type.fields or [], names):
        spec, why = _member(field.type)
        if spec is None:
            return None, f"a cluster with {why}"
        members.append((to_var_name(name), spec))
    named = bool(lv_type.typedef_name or lv_type.classname)
    return ("cluster", named, tuple(members)), ""


def adapt_spec(lv_type: LVType | None) -> tuple[Spec | None, str]:
    """The spec a top-level Adapt to Type parameter of ``lv_type`` is passed by
    (as a pointer to it), or why it has none."""
    if lv_type is None:
        return None, "of unknown type"
    if (scalar := _scalar(lv_type)) is not None:
        return scalar, ""
    if lv_type.kind == LVTypeKind.CLUSTER:
        spec, why = cluster_spec(lv_type)
        return spec, why
    return None, (
        f"of type {lv_type.underlying_type or lv_type.kind.value} (its Data Format "
        "setting is not decoded: no corpus node has one)"
    )
