"""What Flatten To JSON and Unflatten From JSON share: the exception classes of
LabVIEW's JSON errors (their codes are in NI's General LabVIEW Error Codes,
-375000 to -375012; the catalog entries list them), reading a Boolean input
that may be a constant, and the check that refuses NaN and infinity when
``enable LabVIEW extensions?`` is FALSE."""

# What the behaviour rests on (public sources):
# - Error codes and texts: NI, General LabVIEW Error Codes,
#   https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/errors/general-labview-error-codes.html
#   (-375000 type not convertible, -375001 "The JSON root container must be an
#   array or cluster", -375003 invalid JSON, -375004 path not found, -375005 type
#   mismatch, -375007 element count with strict validation, -375008 array
#   dimensions, -375009 invalid multi-dimensional array, -375010 NaN, -375011
#   infinity, -375012 number out of range).
# - A cluster element missing from the JSON is error -375006, "the cluster element
#   is not found in the JSON string"
#   https://forums.ni.com/t5/LabVIEW/unflatten-jason/td-p/4058219
#   and `default null elements?` does not help for an element that is absent
#   https://forums.ni.com/t5/LabVIEW/Unflatten-from-JSON-stops-on-missing-cluster-elements/td-p/4273868
# - NaN and infinity are written NaN, Infinity, -Infinity; a double is written
#   with 20 significant digits (0.1 -> 0.10000000000000000555), 1.0 as 1
#   (the lvjson README https://github.com/JaviLianes8/lvjson, checked there against
#   LabVIEW 2026; and the threads
#   https://forums.ni.com/t5/LabVIEW/Flatten-To-JSON-Function-changes-the-DBL-digits-length/td-p/4310062
#   https://forums.ni.com/t5/LabVIEW/json-with-type-double-number/td-p/3221168 ).
#   Python writes the shortest text that reads back as the same double instead.
# - An unlabelled cluster element is keyed by its position: NI's Unflatten From
#   JSON page, {"0":"abc","1":false,"2":[9,8,7]}
#   (https://docs-be.ni.com/api/bundle/labview-api-ref/page/functions/unflatten-from-json.html).
# Unsettled by any public source: the code of a null with `default null elements?`
# FALSE (generic), a scalar `anything` of Flatten To JSON (a gap: NI lists arrays
# and clusters).

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...plain_values import PlainErrors

ERRORS = PlainErrors(
    type_mismatch="JSONTypeMismatchError",
    element_missing="JSONElementNotFoundError",
    extra_items="JSONExtraItemsError",
    out_of_range="JSONOutOfRangeError",
    dimensions="JSONDimensionsError",
    ragged="JSONRaggedArrayError",
)
PATH_NOT_FOUND = "JSONPathNotFoundError"
ROOT = "JSONRootError"
NAN = "JSONNaNError"
INFINITY = "JSONInfinityError"


def boolean_input(
    ctx: CodeGenContext, node: PrimitiveNode, index: int, default: bool
) -> str:
    """The expression of the Boolean input ``index``: ``"True"`` or ``"False"``
    for a constant or an unwired input (``default``), else the wire's
    expression."""
    term = next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )
    text = ctx.wired_input(term.id, node) if term else None
    return str(default) if text is None else text


def parse_constant_argument(
    ctx: CodeGenContext, node: PrimitiveNode, extensions: str
) -> tuple[list[ast.stmt], str]:
    """The ``, parse_constant=...`` argument of a ``json.loads`` call (empty when
    ``extensions`` is ``"True"``, as Python reads NaN and the infinities then)
    and the statements that define the function it names: a function that raises
    the error of NaN (-375010) or of an infinity (-375011)."""
    if extensions == "True":
        return [], ""
    name = ctx.make_output_var("refuse_constant", node.id)
    source = (
        f"def {name}(constant):\n"
        f"    if constant == 'NaN':\n"
        f"        raise {NAN}('NaN needs enable LabVIEW extensions?')\n"
        f"    raise {INFINITY}(f'{{constant}} needs enable LabVIEW extensions?')\n"
    )
    if extensions == "False":
        return ast.parse(source).body, f", parse_constant={name}"
    argument = f", parse_constant=(None if {extensions} else {name})"
    return ast.parse(source).body, argument
