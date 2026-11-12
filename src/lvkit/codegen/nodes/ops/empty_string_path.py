"""Empty String/Path? (prim 1112).

NI (labview-api-ref functions/empty-string-path): the input "can be a string, a
path, a variant, ..." and "This function is also designed to work with
variants". A variant is empty when it holds no data, which is how the JKI VI
Tester equality tests (``failUnlessEqual.vi``, ``passIfEqual.vi``,
``failIfEqual.vi``) tell whether their optional Variant ``delta`` was wired.
A variant holding ``""`` is not empty (NI forum "How to detect an empty
Variant": "Empty String/Path is false if the empty string/Path is converted to a
variant first"). An empty variant is ``None`` in generated code.

in_1 = string, path or variant -> out index 0 = empty?
  - a variant is empty only when it is ``None``;
  - a path is empty when it has no component, and ``None`` (Not a Path) is not an
    empty path;
  - a string is empty when it has no characters.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_variant_type_name
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op
from ._flat_node import terminal

_INPUT = 1
_EMPTY_VARIANT = "in_1 is None"
_EMPTY_STRING_OR_PATH = (
    "(in_1 == Path('')) if isinstance(in_1, Path) "
    "else (in_1 is not None and len(in_1) == 0)"
)


@register_op("EMPTY_STRING_PATH")
def empty_string_path(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    wired = terminal(node, "input", _INPUT)
    is_variant = wired is not None and (
        wired.lv_type is not None
        and is_variant_type_name(wired.lv_type.underlying_type)
    )
    return {"is_empty": _EMPTY_VARIANT if is_variant else _EMPTY_STRING_OR_PATH}
