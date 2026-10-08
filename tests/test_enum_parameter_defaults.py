"""Connector-pane enum defaults must retain their recorded ordinal."""

from __future__ import annotations

import ast

import pytest

from lvkit.codegen.builder import _param_default_expr
from lvkit.models import LVType, LVTypeKind, Terminal


def terminal(value, underlying_type="UnitUInt16", kind=LVTypeKind.ENUM):
    return Terminal(
        id="mode",
        name="mode",
        index=0,
        direction="input",
        default_value=value,
        lv_type=LVType(kind=kind, underlying_type=underlying_type),
    )


@pytest.mark.parametrize("underlying_type", ["UnitUInt8", "UnitUInt16", "UnitUInt32"])
@pytest.mark.parametrize("value", ["1", 1, "2", 2])
def test_enum_saved_nonzero_ordinal(value, underlying_type):
    expression = _param_default_expr(terminal(value, underlying_type))
    assert ast.literal_eval(expression) == int(value)


@pytest.mark.parametrize("value", ["1", 1])
def test_enum_kind_preserves_default_without_underlying_token(value):
    assert ast.literal_eval(_param_default_expr(terminal(value, None))) == 1


@pytest.mark.parametrize("value", [None, "not-an-ordinal", "0", 0])
def test_enum_missing_invalid_or_zero_default(value):
    assert ast.literal_eval(_param_default_expr(terminal(value))) == 0


def test_integer_primitive_saved_default_is_preserved():
    expression = _param_default_expr(terminal("7", "NumUInt16", LVTypeKind.PRIMITIVE))
    assert ast.literal_eval(expression) == 7


@pytest.mark.parametrize("value", ["3", 3])
def test_ring_saved_nonzero_ordinal(value):
    expression = _param_default_expr(terminal(value, kind=LVTypeKind.RING))
    assert ast.literal_eval(expression) == 3
