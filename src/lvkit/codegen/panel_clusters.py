"""The fields of the cluster controls of a front panel, for the ``Panel`` description.

A cluster control's Controls[] property is "an array of references to the cluster
elements in cluster order" (NI: properties-and-methods/vi-server/generic/gobject/
control/cluster/controls.html): each element is a control with its own label whose
value is that field of the cluster's value. The ``Panel`` makes those element
controls from the description this module writes: for each cluster control (by its
name in the panel's name table) the fields in field order, each as
``(label, key)`` or, for a field that is itself a cluster, ``(label, key, fields)``.

``key`` is how the cluster's value holds the field: the position (an ``int``) in an
on-diagram anonymous cluster, which is a tuple (``cluster_members``), else the
attribute name (a ``str``) a named cluster gives the field
(``ast_utils.field_attribute_names``). An error cluster, whose value is a
``LabVIEWError``, and a class object have no entry.
"""

from __future__ import annotations

import ast

from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal, _is_error_cluster

from .ast_utils import field_attribute_names
from .cluster_members import is_anonymous_cluster
from .context import CodeGenContext


def _cluster_fields(ctx: CodeGenContext, lv_type: LVType | None) -> list[ClusterField]:
    """The fields of the cluster ``lv_type``, empty when it is not a cluster whose
    value is a tuple or an object with an attribute per field."""
    if (
        lv_type is None
        or lv_type.kind not in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF)
        or lv_type.classname
        or lv_type.ref_type == "UDClassInst"
        or _is_error_cluster(lv_type)
    ):
        return []
    found = ctx.graph.get_type_fields(lv_type) if ctx.graph is not None else None
    return found or lv_type.fields or []


def _specs(
    ctx: CodeGenContext, lv_type: LVType, fields: list[ClusterField]
) -> ast.Tuple:
    """The specs of ``fields``, the fields of the cluster ``lv_type``."""
    positional = is_anonymous_cluster(lv_type)
    attributes = field_attribute_names(field.name for field in fields)
    specs: list[ast.expr] = []
    for position, (field, attribute) in enumerate(zip(fields, attributes, strict=True)):
        key = ast.Constant(value=position if positional else attribute)
        parts: list[ast.expr] = [ast.Constant(value=field.name or ""), key]
        inner = _cluster_fields(ctx, field.type)
        if field.type is not None and inner:
            parts.append(_specs(ctx, field.type, inner))
        specs.append(ast.Tuple(elts=parts, ctx=ast.Load()))
    return ast.Tuple(elts=specs, ctx=ast.Load())


def cluster_specs(ctx: CodeGenContext, terminal: Terminal) -> ast.Tuple | None:
    """The fields of the cluster control or indicator ``terminal`` as the
    description's specs; None when it is not a cluster of the kind above."""
    fields = _cluster_fields(ctx, terminal.lv_type)
    if terminal.lv_type is None or not fields:
        return None
    return _specs(ctx, terminal.lv_type, fields)
