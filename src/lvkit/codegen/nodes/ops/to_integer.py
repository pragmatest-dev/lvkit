"""The To <integer> conversions (To Byte / Word / Long / Quad Integer and their
unsigned forms), for numbers, enums, arrays and clusters of them.

NI, "To Long Integer Function" (labview-api-ref, ``functions/to-long-integer.html``):
"Converts a number to a 32-bit integer in the range -(2^31) to (2^31)-1. This
function rounds all floating-point and fixed-point numeric values to the nearest
integer." "If the fractional part of the floating-point or fixed-point value is .5,
the function rounds the value to the nearest even integer. For example, the
function rounds 13.5 to 14 and rounds 14.5 to 14." ``round`` rounds halves to even.
The input "can be a scalar number, array or cluster of numbers, array of clusters
of numbers, and so on" (the pages of To Quad Integer, To Unsigned Long Integer and
To Unsigned Quad Integer add "a time stamp"; the others do not list one), and the
output "is of the same data type structure": a cluster of the converted members.

NI, "Numeric Conversion" (labview, ``numeric-conversion.html``): "Floating-point
number to signed or unsigned integer -- LabVIEW coerces out-of-range values to the
minimum or maximum value of the integer." "LabVIEW treats enums as unsigned
integers." "Integer to integer -- LabVIEW does not coerce out-of-range values to the
minimum or maximum value of an integer. ... If the source is larger than the
destination, LabVIEW copies only the least significant bits of the value."

So a floating-point value is compared with the integer's range before it is
rounded (+infinity is the maximum, -infinity the minimum; ``round`` of infinity
raises), and an integer, an enum or a ring keeps its low bits. NI documents
nothing for NaN; it is left to ``round``, which raises.

One op serves every width: the range comes from the type of the output terminal
in the catalog entry. A cluster is a tuple, or a ``SimpleNamespace`` when it is a
typedef (the forms cluster constants take), each member converted by its own type.
An input that is not a number, an enum, a ring, or an array or cluster of them is a
gap. Terminals: in 1 = number; out 0 = the integer.
"""

from __future__ import annotations

import re

from lvkit.codegen.timestamp_templates import timestamp_template
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import INTEGER_BYTE_WIDTHS, SIGNED_INTEGER_TYPE_NAMES
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import field_attribute_names
from ...cluster_members import is_anonymous_cluster
from ...context import CodeGenContext
from . import register_context_op
from ._numeric import _scalar_underlying, is_float, is_real
from ._rounding import number_input, over_type_rank, saturating_round

_ROOT = re.compile(r"(?<![\w.])in_1\b")


def _element(lv_type: LVType | None) -> LVType | None:
    """The type under every array dimension of ``lv_type``."""
    while lv_type is not None and lv_type.kind == LVTypeKind.ARRAY:
        lv_type = lv_type.element_type
    return lv_type


def _is_cluster(lv_type: LVType | None) -> bool:
    return (
        lv_type is not None
        and lv_type.kind == LVTypeKind.CLUSTER
        and not lv_type.classname
    )


class _Conversion:
    """One node's conversion of the input value of any structure to the integer
    its output terminal records."""

    def __init__(
        self,
        node: PrimitiveNode,
        bits: int,
        signed: bool,
        out: str,
        ctx: CodeGenContext,
    ) -> None:
        self.node = node
        self.bits = bits
        self.signed = signed
        self.out = out
        self.ctx = ctx

    def _fields(self, lv_type: LVType) -> list[ClusterField]:
        graph = self.ctx.graph
        found = graph.get_type_fields(lv_type) if graph else None
        fields = found or lv_type.fields
        if not fields:
            raise ConversionGap(
                f"{self.node.name} has a cluster with no fields", self.node
            )
        return fields

    def _gap(self, lv_type: LVType | None) -> ConversionGap:
        what = lv_type.kind.value if lv_type is not None else "value"
        return ConversionGap(
            f"{self.node.name}: NI lists a scalar number, an array or cluster of "
            f"numbers or an array of clusters of numbers as its input; the input "
            f"here is a {what}",
            self.node,
        )

    def leaf(self, lv_type: LVType | None) -> str:
        """The conversion of a number, an enum or a ring, or an array of them,
        written over ``in_1``."""
        element = _element(lv_type)
        if element is not None and element.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            return over_type_rank(self._low_bits(), lv_type)
        if lv_type is not None and not is_real(lv_type):
            raise self._gap(element)
        if is_real(lv_type) and not is_float(lv_type):
            if _scalar_underlying(lv_type) == self.out:
                return "in_1"
            return over_type_rank(self._low_bits(), lv_type)
        # An input whose type is not recorded takes the range test, which also
        # holds for an integer.
        return over_type_rank(saturating_round("in_1", self.bits, self.signed), lv_type)

    def _low_bits(self) -> str:
        ctype = f"c_{'' if self.signed else 'u'}int{self.bits}"
        return f"ctypes.{ctype}(in_1).value"

    def convert(
        self,
        read: str,
        lv_type: LVType | None,
        form: LVType | None,
        depth: int,
    ) -> str:
        """The expression that converts ``read`` of type ``lv_type``; ``form`` is
        the type of the result (it decides a typedef cluster's attributes)."""
        element = _element(lv_type)
        if _is_cluster(lv_type):
            assert lv_type is not None
            return self._cluster(read, lv_type, form, depth)
        if _is_cluster(element):
            assert lv_type is not None
            variables = [f"_c{depth + i}" for i in range(lv_type.dimensions or 1)]
            inner = self.convert(
                variables[-1], element, _element(form), depth + len(variables)
            )
            for position in reversed(range(len(variables))):
                source = variables[position - 1] if position else read
                inner = f"[{inner} for {variables[position]} in {source}]"
            return inner
        return _ROOT.sub(lambda _m: read, self.leaf(lv_type))

    def _cluster(
        self, read: str, lv_type: LVType, form: LVType | None, depth: int
    ) -> str:
        fields = self._fields(lv_type)
        out_form = form if _is_cluster(form) else lv_type
        assert out_form is not None
        out_fields = self._fields(out_form)
        in_names = field_attribute_names(f.name for f in fields)
        out_names = field_attribute_names(f.name for f in out_fields)
        anonymous = is_anonymous_cluster(lv_type)
        members: list[str] = []
        for position, (field, name) in enumerate(zip(fields, in_names, strict=True)):
            member = f"{read}[{position}]" if anonymous else f"{read}.{name}"
            out_field = out_fields[position] if position < len(out_fields) else field
            members.append(self.convert(member, field.type, out_field.type, depth))
        if is_anonymous_cluster(out_form):
            return "(" + "".join(f"{m}, " for m in members) + ")"
        self.ctx.add_import("from types import SimpleNamespace")
        return (
            "SimpleNamespace("
            + ", ".join(f"{n}={m}" for n, m in zip(out_names, members, strict=False))
            + ")"
        )


@register_context_op("TO_INTEGER")
def to_integer(
    node: PrimitiveNode, resolved: ResolvedPrimitive, ctx: CodeGenContext
) -> str | dict[str, str]:
    number = number_input(node)
    number_type = number.lv_type if number is not None else None
    if timestamp_template(node) is not None or (
        number_type is not None and number_type.is_timestamp
    ):
        # primitive.py replaces the expression with the shared timestamp
        # template (or raises the conversion's gap); a dict keeps it out of the
        # string form's "=" stripping.
        return {"integer": "in_1"}
    out = next((t for t in resolved.terminals if t.direction == "output"), None)
    width = INTEGER_BYTE_WIDTHS.get(out.type or "") if out is not None else None
    if out is None or width is None:
        raise ConversionGap(
            f"{node.name} has no integer type on its output in the catalog", node
        )
    conversion = _Conversion(
        node, width * 8, out.type in SIGNED_INTEGER_TYPE_NAMES, out.type or "", ctx
    )
    output = next((t for t in node.terminals if t.direction == "output"), None)
    form = output.lv_type if output is not None else None
    expression = conversion.convert("in_1", number_type, form, 0)
    if "SimpleNamespace(" in expression:
        # A dict keeps the expression out of the string form's "=" stripping (a
        # typedef cluster's attributes are keyword arguments).
        return {"integer": expression}
    return expression
