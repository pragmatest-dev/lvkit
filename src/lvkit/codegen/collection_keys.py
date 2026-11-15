"""How a Map key or a Set element is written in generated code.

A cluster is a ``SimpleNamespace`` (named) or a tuple (anonymous) and an array a
list, none of which Python can hash. A key or element that is a cluster or an array
is made into a tuple of its values, in cluster order, with a nested cluster or array
a nested tuple, at the node where it ENTERS the collection; the value stored in a
Map is never converted. A number, Boolean, string, path, enum or ring is its own
key. A class object, a Variant, a reference, a Map, a Set or a waveform cannot be
a key even then: that is a gap naming it.

Order: NI's "Collection Maps" page (https://docs-be.ni.com/bundle/labview/page/
grouping-data-with-collection-maps.html) says "A map groups key-value pairs in
sorted order where all the keys are unique. The key and value can each be any data
type", and a forum reply (https://forums.ni.com/t5/LabVIEW/Map-collection/m-p/
4185020) says "If you extract all elements using a FOR loop, they are necessarily
sorted by key". A cluster is compared element by element from the first, so a
tuple of its values sorts the same way. The nodes that use these keys (Look In Map,
Insert Into Map, Insert Into Set, Remove From Set, Map Get / Replace Value) never
list the keys, so none depends on the order; a node that lists or iterates a
collection must give sorted order. Not settled by NI's pages or the forum: whether
two clusters equal in value but labelled differently are one key (a tuple ignores
the labels), and how a NaN key compares or orders (no page found settles it).
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_variant_type_name
from lvkit.models import LVType, LVTypeKind

from .ast_utils import field_attribute_names
from .cluster_members import is_anonymous_cluster
from .context import CodeGenContext
from .native_array import operand

# What no tuple of values can stand in for, by the type's underlying name.
_UNHASHABLE = {
    "Map": "a Map",
    "Set": "a Set",
    "Refnum": "a reference",
}


def _held(lv_type: LVType) -> str | None:
    """What makes ``lv_type`` impossible as a key, None when it is possible."""
    underlying = lv_type.underlying_type or ""
    if (
        lv_type.kind == LVTypeKind.CLASS
        or lv_type.classname
        or lv_type.ref_type == "UDClassInst"
    ):
        return "a class object"
    if is_variant_type_name(underlying):
        return "a Variant"
    if underlying == "MeasureData" and not lv_type.is_timestamp:
        return "a waveform"
    return _UNHASHABLE.get(underlying)


def key_expression(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    text: str,
    lv_type: LVType | None,
    what: str,
    depth: int = 0,
) -> str:
    """The expression for ``text`` (a wired key or element of ``lv_type``) as a
    dictionary key or set element. ``what`` ("a key", "an element") names it in a
    gap."""
    if lv_type is None:
        raise ConversionGap(
            f"{node.name} with {what} of an unknown type has no code generation yet",
            node,
        )
    held = _held(lv_type)
    if held is not None:
        raise ConversionGap(
            f"{node.name} with {what} holding {held}: {held} cannot be a key or "
            "element of a Map or Set",
            node,
        )
    if lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        return _array_key(
            ctx, node, text, lv_type.element_type, lv_type.dimensions or 1, what, depth
        )
    if lv_type.kind == LVTypeKind.CLUSTER:
        return _cluster_key(ctx, node, text, lv_type, what, depth)
    if lv_type.kind in (LVTypeKind.ARRAY, LVTypeKind.TYPEDEF_REF):
        raise ConversionGap(
            f"{node.name} with {what} of {lv_type.kind.value} type whose members "
            "are not known has no code generation yet",
            node,
        )
    return text


def _array_key(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    text: str,
    element: LVType,
    dimensions: int,
    what: str,
    depth: int,
) -> str:
    """A tuple of the keys of the elements, one nested tuple per dimension."""
    if dimensions > 1:
        name = f"_k{depth}"
        inner = _array_key(ctx, node, name, element, dimensions - 1, what, depth + 1)
        return f"tuple({inner} for {name} in {text})"
    name = f"_k{depth}"
    inner = key_expression(ctx, node, name, element, what, depth + 1)
    return f"tuple({text})" if inner == name else f"tuple({inner} for {name} in {text})"


def _cluster_key(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    text: str,
    lv_type: LVType,
    what: str,
    depth: int,
) -> str:
    """A tuple of the keys of the cluster's fields, read by position from an
    anonymous cluster and by attribute from a named one."""
    fields = ctx.graph.get_type_fields(lv_type) if ctx.graph is not None else None
    fields = fields or lv_type.fields
    if not fields:
        raise ConversionGap(
            f"{node.name} with {what} of a cluster whose fields are not known has "
            "no code generation yet",
            node,
        )
    anonymous = is_anonymous_cluster(lv_type)
    text = operand(text)
    attributes = field_attribute_names(f.name for f in fields)
    parts = []
    for position, (field, attribute) in enumerate(zip(fields, attributes, strict=True)):
        member = f"{text}[{position}]" if anonymous else f"{text}.{attribute}"
        parts.append(key_expression(ctx, node, member, field.type, what, depth))
    trailing = "," if len(parts) == 1 else ""
    return f"({', '.join(parts)}{trailing})"
