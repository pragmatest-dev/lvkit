"""A LabVIEW value as plain Python dicts, lists and scalars, and back.

``to_plain`` writes the expression that turns a value of a LabVIEW type into the
plain form: a cluster with labelled elements is a dict keyed by its labels, a
cluster without labels a dict keyed ``"0"``, ``"1"``, ... by position, an array
a list (a list of lists for each further dimension), a Boolean, number or string
itself. ``from_plain`` writes the statements that build a value of a LabVIEW type
from the plain form, checking each part against the type. Both work from the
type at generation time, so the generated code is plain Python with the types
written out. Only Booleans, integers, floating-point numbers, strings and the
arrays and clusters of them have a plain form; any other type raises
``UnsupportedPlainType`` naming it.

A cluster is a tuple for an on-diagram cluster and a ``SimpleNamespace`` for a
typedef (``cluster_members``); the labels come from the type's fields.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lvkit.lv_type_codes import (
    INTEGER_TYPE_NAMES,
    REAL_FLOAT_TYPE_NAMES,
    is_boolean_type_name,
    is_string_type_name,
)
from lvkit.models import ClusterField, LVType, LVTypeKind, unique_names

from .ast_utils import field_attribute_names
from .type_default import type_default

if TYPE_CHECKING:
    from .context import CodeGenContext

# The range of each integer type.
_INTEGER_RANGES = {
    "NumInt8": (-(2**7), 2**7 - 1),
    "NumInt16": (-(2**15), 2**15 - 1),
    "NumInt32": (-(2**31), 2**31 - 1),
    "NumInt64": (-(2**63), 2**63 - 1),
    "NumUInt8": (0, 2**8 - 1),
    "NumUInt16": (0, 2**16 - 1),
    "NumUInt32": (0, 2**32 - 1),
    "NumUInt64": (0, 2**64 - 1),
}


class UnsupportedPlainType(Exception):
    """The type has no plain form; the message names it."""


@dataclass(frozen=True)
class PlainErrors:
    """The names of the exception classes ``from_plain``'s statements raise,
    one per way the plain form can fail to fit the type. A null that is not
    allowed raises ``ValueError``."""

    type_mismatch: str
    element_missing: str
    extra_items: str
    out_of_range: str
    dimensions: str
    ragged: str


@dataclass(frozen=True)
class PlainOptions:
    """How ``from_plain`` reads: ``null_to_default`` and ``strict`` are Python
    expressions (``"True"`` and ``"False"`` when known at generation time)."""

    null_to_default: str
    strict: str
    errors: PlainErrors


@dataclass(frozen=True)
class _Cluster:
    fields: list[ClusterField]
    keys: list[str]
    # The attribute of each field; None for a tuple read by position.
    attributes: list[str] | None

    def member(self, expr: str, position: int) -> str:
        if self.attributes is None:
            return f"{expr}[{position}]"
        return f"{expr}.{self.attributes[position]}"


def _describe(lv_type: LVType) -> str:
    return lv_type.typedef_name or lv_type.underlying_type or lv_type.kind.value


def _fields_of(lv_type: LVType, ctx: CodeGenContext) -> list[ClusterField] | None:
    if ctx.graph is not None:
        return ctx.graph.get_type_fields(lv_type)
    return lv_type.fields


def _is_unlabelled(fields: list[ClusterField]) -> bool:
    """Whether no element of the cluster has a label (an unlabelled element is
    named by its position)."""
    return not any(f.labelled for f in fields)


def _cluster(lv_type: LVType, ctx: CodeGenContext) -> _Cluster:
    if lv_type.classname:
        raise UnsupportedPlainType(f"the class {_describe(lv_type)}")
    fields = _fields_of(lv_type, ctx)
    if not fields:
        raise UnsupportedPlainType(f"the cluster {_describe(lv_type)} with no fields")
    keys = (
        [str(i) for i in range(len(fields))]
        if _is_unlabelled(fields)
        else unique_names(f.name for f in fields)
    )
    attributes = (
        field_attribute_names(f.name for f in fields) if lv_type.typedef_name else None
    )
    return _Cluster(fields, keys, attributes)


def _scalar_kind(lv_type: LVType) -> str:
    """``bool``, ``string``, ``int`` or ``float`` for a type with a plain scalar
    form."""
    underlying = lv_type.underlying_type
    if lv_type.kind == LVTypeKind.PRIMITIVE:
        if is_boolean_type_name(underlying):
            return "bool"
        if is_string_type_name(underlying):
            return "string"
        if underlying in INTEGER_TYPE_NAMES:
            return "int"
        if underlying in REAL_FLOAT_TYPE_NAMES:
            return "float"
    raise UnsupportedPlainType(f"{_describe(lv_type)} ({lv_type.kind.value})")


def _is_cluster(lv_type: LVType) -> bool:
    return lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF)


def is_container(lv_type: LVType) -> bool:
    """Whether the plain form of ``lv_type`` is a dict or a list."""
    return lv_type.kind == LVTypeKind.ARRAY or _is_cluster(lv_type)


def to_plain(expr: str, lv_type: LVType, ctx: CodeGenContext) -> str:
    """The expression of the plain form of ``expr``, a value of ``lv_type``."""
    return _plain(expr, lv_type, ctx, 0)


def _plain(expr: str, lv_type: LVType, ctx: CodeGenContext, depth: int) -> str:
    if lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        return _plain_list(
            expr, lv_type.element_type, lv_type.dimensions or 1, ctx, depth
        )
    if _is_cluster(lv_type):
        cluster = _cluster(lv_type, ctx)
        items = [
            f"{key!r}: "
            + _plain(cluster.member(expr, i), _field_type(f, lv_type), ctx, depth)
            for i, (key, f) in enumerate(zip(cluster.keys, cluster.fields))
        ]
        return "{" + ", ".join(items) + "}"
    _scalar_kind(lv_type)
    return expr


def _field_type(field: ClusterField, owner: LVType) -> LVType:
    if field.type is None:
        raise UnsupportedPlainType(f"an element of {_describe(owner)} with no type")
    return field.type


def _plain_list(
    expr: str, element: LVType, dimensions: int, ctx: CodeGenContext, depth: int
) -> str:
    item = f"_item{depth}"
    if dimensions > 1:
        inner = _plain_list(item, element, dimensions - 1, ctx, depth + 1)
    else:
        inner = _plain(item, element, ctx, depth + 1)
    return f"[{inner} for {item} in {expr}]"


class _Lines:
    """The source lines of the statements being written."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.level = 0

    def add(self, text: str) -> None:
        self.lines.append("    " * self.level + text)

    @contextmanager
    def indented(self) -> Iterator[None]:
        self.level += 1
        try:
            yield
        finally:
            self.level -= 1


class _Reader:
    def __init__(
        self, ctx: CodeGenContext, node_id: str, options: PlainOptions
    ) -> None:
        self.ctx = ctx
        self.node_id = node_id
        self.options = options
        self.out = _Lines()

    def fresh(self, base: str) -> str:
        return self.ctx.make_output_var(base, self.node_id)

    def read(
        self, src: str, lv_type: LVType, default: str, target: str, where: str
    ) -> None:
        """Statements that give ``target`` the value of ``lv_type`` made from
        the plain value ``src`` (``default`` is the expression of the value for
        a null); ``where`` names the part for the error messages."""
        if lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
            self.array(
                src,
                lv_type.element_type,
                lv_type.dimensions or 1,
                default,
                target,
                where,
            )
        elif _is_cluster(lv_type):
            self.cluster(src, lv_type, default, target, where)
        else:
            self.scalar(src, lv_type, default, target, where)

    def guarded(
        self,
        src: str,
        test: str,
        default: str,
        target: str,
        where: str,
    ) -> None:
        """``if src is None`` (the default or an error) and ``elif test:``, whose
        body the caller writes indented, then ``otherwise``."""
        out = self.out
        null = self.options.null_to_default
        out.add(f"if {src} is None:")
        with out.indented():
            if null == "True":
                out.add(f"{target} = {default}")
            elif null == "False":
                out.add(f"raise ValueError({where + ': null'!r})")
            else:
                out.add(f"if {null}:")
                with out.indented():
                    out.add(f"{target} = {default}")
                out.add("else:")
                with out.indented():
                    out.add(f"raise ValueError({where + ': null'!r})")
        out.add(f"elif {test}:")

    def otherwise(self, expected: str, where: str, error: str | None = None) -> None:
        self.out.add("else:")
        with self.out.indented():
            self.out.add(
                f"raise {error or self.options.errors.type_mismatch}"
                f"({where + ': expected ' + expected!r})"
            )

    def scalar(
        self, src: str, lv_type: LVType, default: str, target: str, where: str
    ) -> None:
        kind = _scalar_kind(lv_type)
        out = self.out
        tests = {
            "bool": f"isinstance({src}, bool)",
            "string": f"isinstance({src}, str)",
            "int": f"isinstance({src}, int) and not isinstance({src}, bool)",
            "float": f"isinstance({src}, (int, float)) and not isinstance({src}, bool)",
        }
        expected = {
            "bool": "a Boolean",
            "string": "a string",
            "int": "an integer",
            "float": "a number",
        }[kind]
        self.guarded(src, tests[kind], default, target, where)
        with out.indented():
            if kind == "int":
                low, high = _INTEGER_RANGES[lv_type.underlying_type or ""]
                out.add(f"if not {low} <= {src} <= {high}:")
                with out.indented():
                    out.add(
                        f"raise {self.options.errors.out_of_range}"
                        f"({where + ': out of range'!r})"
                    )
            out.add(
                f"{target} = float({src})" if kind == "float" else f"{target} = {src}"
            )
        self.otherwise(expected, where)

    def cluster(
        self, src: str, lv_type: LVType, default: str, target: str, where: str
    ) -> None:
        cluster = _cluster(lv_type, self.ctx)
        out = self.out
        errors = self.options.errors
        self.guarded(src, f"isinstance({src}, dict)", default, target, where)
        with out.indented():
            if self.options.strict != "False":
                extra = self.fresh("unexpected")
                known = ", ".join(repr(k) for k in cluster.keys)
                out.add(f"{extra} = sorted({src}.keys() - {{{known}}})")
                test = (
                    extra
                    if self.options.strict == "True"
                    else f"{self.options.strict} and {extra}"
                )
                out.add(f"if {test}:")
                with out.indented():
                    out.add(
                        f"raise {errors.extra_items}"
                        f"({where + ': items not in the cluster: '!r} + str({extra}))"
                    )
            values = []
            for i, (key, field) in enumerate(zip(cluster.keys, cluster.fields)):
                element = self.fresh("element")
                values.append(element)
                part = f"{where}.{key}"
                out.add(f"if {key!r} not in {src}:")
                with out.indented():
                    out.add(
                        f"raise {errors.element_missing}"
                        f"({part + ': the cluster element is not found'!r})"
                    )
                self.read(
                    f"{src}[{key!r}]",
                    _field_type(field, lv_type),
                    cluster.member(default, i),
                    element,
                    part,
                )
            if cluster.attributes is None:
                trailing = "," if len(values) == 1 else ""
                out.add(f"{target} = ({', '.join(values)}{trailing})")
            else:
                arguments = ", ".join(
                    f"{a}={v}" for a, v in zip(cluster.attributes, values)
                )
                out.add(f"{target} = SimpleNamespace({arguments})")
        self.otherwise("an object", where)

    def array(
        self,
        src: str,
        element: LVType,
        dimensions: int,
        default: str,
        target: str,
        where: str,
        inner: bool = False,
    ) -> None:
        out = self.out
        self.guarded(src, f"isinstance({src}, list)", default, target, where)
        with out.indented():
            out.add(f"{target} = []")
            item = self.fresh("item")
            out.add(f"for {item} in {src}:")
            with out.indented():
                value = self.fresh("element")
                if dimensions > 1:
                    self.array(item, element, dimensions - 1, "[]", value, where, True)
                else:
                    self.read(
                        item,
                        element,
                        ast.unparse(type_default(self.ctx, element)),
                        value,
                        f"an element of {where}",
                    )
                out.add(f"{target}.append({value})")
            if dimensions > 1:
                out.add(f"if len({{len(row) for row in {target}}}) > 1:")
                with out.indented():
                    out.add(
                        f"raise {self.options.errors.ragged}"
                        f"({where + ': the rows differ in length'!r})"
                    )
        self.otherwise(
            "an array", where, self.options.errors.dimensions if inner else None
        )


def from_plain(
    source: str,
    lv_type: LVType,
    default: str,
    target: str,
    ctx: CodeGenContext,
    node_id: str,
    options: PlainOptions,
    where: str = "the value",
) -> list[ast.stmt]:
    """The statements that give ``target`` the value of ``lv_type`` made from
    ``source``, a variable holding the plain value. ``default`` is the
    expression of a value of the type (the default of a null, and of an
    element's own default). The statements raise ``options.errors`` for a part
    that does not fit the type."""
    reader = _Reader(ctx, node_id, options)
    reader.read(source, lv_type, default, target, where)
    return ast.parse("\n".join(reader.out.lines)).body
