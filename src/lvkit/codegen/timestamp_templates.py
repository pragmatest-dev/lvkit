"""The Python of the numeric functions that take or make a timestamp.

A timestamp is a ``hightime.datetime`` and a duration a number of seconds, so the
function a primitive's catalog entry writes for numbers is written differently
when an operand is a timestamp (NI Subtract: two time stamps give "a numeric value
(difference in time)", a timestamp minus a number "yields a time stamp"). Chosen by
the operands' types, never by name.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal
from lvkit.timestamp_value import EPOCH_1904_EXPRESSION

ADD_PRIM_ID = 1050
SUBTRACT_PRIM_ID = 1051
TO_DOUBLE_PRIM_ID = 1147

# The unsigned integer conversions whose NI page lists "a time stamp" as the input
# (To Unsigned Long Integer, To Unsigned Quad Integer), with the ctypes type each
# keeps the low bits of, as the catalog's code for a number does.
#
# Rounding: those pages say "This function rounds all floating-point and
# fixed-point numeric values to the nearest integer. If the fractional part of the
# value is .5, the function rounds the value to the nearest even integer"
# (https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/functions/
# to-unsigned-long-integer.html), and a timestamp is a 64.64 fixed-point count of
# seconds (LAVA "Timestamp Behavior", lavag.org/topic/16198), so the seconds round
# to nearest, ties to even; "the fractional part of the seconds will disappear"
# (forums.ni.com/t5/LabVIEW/timestamp-as-simple-long/td-p/910142). The rounding is
# done on the exact fraction (a float of the seconds holds only ~0.5 us). No
# source says a timestamp past the range converts differently from a number.
_INTEGER_CTYPES = {1145: "c_uint32", 1156: "c_uint64"}
# The other To <number type> conversions (To Byte / Word / Long / Quad Integer, the
# unsigned byte / word, To Single / Extended Precision Float, the three complex
# types): their NI pages do not list a time stamp as an input.
_UNDOCUMENTED_PRIM_IDS = frozenset(
    {1140, 1141, 1142, 1143, 1144, 1146, 1149, 1151, 1152, 1154, 1155}
)

# The modules the templates import (``import`` lines of the generated module).
TIMESTAMP_IMPORTS = ["import hightime", "from datetime import UTC"]
_INTEGER_IMPORTS = ["from fractions import Fraction", "import ctypes"]

_Y_INPUT = 1
_X_INPUT = 2
_NUMBER_INPUT = 1


def _input(node: PrimitiveNode, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )


def _is_timestamp(term: Terminal | None) -> bool:
    return term is not None and term.lv_type is not None and term.lv_type.is_timestamp


def _seconds(operand: str) -> str:
    return f"hightime.timedelta(seconds={operand})"


def timestamp_template(node: PrimitiveNode) -> str | None:
    """The expression of Add, Subtract or To Double Precision Float of ``node``
    when an operand is a timestamp; None when none is."""
    x = _input(node, _X_INPUT)
    y = _input(node, _Y_INPUT)
    if node.prim_id == SUBTRACT_PRIM_ID and _is_timestamp(x):
        if _is_timestamp(y):
            return "(in_2 - in_1).total_seconds()"
        return f"in_2 - {_seconds('in_1')}"
    if node.prim_id == ADD_PRIM_ID:
        if _is_timestamp(x):
            return f"in_2 + {_seconds('in_1')}"
        if _is_timestamp(y):
            return f"in_1 + {_seconds('in_2')}"
    if node.prim_id == TO_DOUBLE_PRIM_ID and _is_timestamp(_input(node, _NUMBER_INPUT)):
        return f"(in_1 - {EPOCH_1904_EXPRESSION}).total_seconds()"
    if node.prim_id in _INTEGER_CTYPES and _is_timestamp(_input(node, _NUMBER_INPUT)):
        yoctoseconds = (
            f"(in_1 - {EPOCH_1904_EXPRESSION}) // hightime.timedelta(yoctoseconds=1)"
        )
        ctype = _INTEGER_CTYPES[node.prim_id]
        return f"ctypes.{ctype}(round(Fraction({yoctoseconds}, 10**24))).value"
    return None


def timestamp_imports(node: PrimitiveNode) -> list[str]:
    """The ``import`` lines ``timestamp_template`` of ``node`` needs."""
    if node.prim_id in _INTEGER_CTYPES:
        return [*TIMESTAMP_IMPORTS, *_INTEGER_IMPORTS]
    return TIMESTAMP_IMPORTS


def timestamp_gap(node: PrimitiveNode) -> str | None:
    """Why a To <number type> conversion of a timestamp has no Python (None when
    ``node`` is not one). The conversions whose NI page does not list a time stamp
    as an input stay gaps."""
    if node.prim_id in _UNDOCUMENTED_PRIM_IDS and _is_timestamp(
        _input(node, _NUMBER_INPUT)
    ):
        return (
            "NI's page for this conversion does not list a time stamp as its "
            "input, so the number a timestamp converts to is not documented"
        )
    return None
