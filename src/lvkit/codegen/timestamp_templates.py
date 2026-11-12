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

# The modules the templates import (``import`` lines of the generated module).
TIMESTAMP_IMPORTS = ["import hightime", "from datetime import UTC"]

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
    return None
