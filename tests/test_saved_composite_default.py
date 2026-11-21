"""A cluster or array control whose saved value cannot be written is a gap, not a
silent start at the type's default."""

from __future__ import annotations

import ast
import types

import pytest

from lvkit.codegen.type_default import control_default
from lvkit.conversion_gap import ConversionGap
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind

_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_CLUSTER = LVType(kind=LVTypeKind.CLUSTER, fields=[ClusterField(name="a", type=_I32)])


def _control() -> FPTerminal:
    return FPTerminal(
        default_structured=[1],
        id="fp_d0",
        index=None,
        direction="output",
        name="Pair",
        lv_type=_CLUSTER,
        default_value="(1)",
        is_public=False,
        fp_dco_uid="d0",
    )


def _ctx(expression: str | None) -> types.SimpleNamespace:
    def constant_expression(constant: object) -> str:
        if expression is None:
            raise ConversionGap("a saved field has no Python value")
        return expression

    return types.SimpleNamespace(constant_expression=constant_expression)


def test_a_writable_saved_cluster_value_is_the_control_default() -> None:
    value = control_default(_ctx("(1,)"), _control())  # type: ignore[arg-type]
    assert ast.unparse(value) == "(1,)"


def test_a_saved_cluster_value_that_cannot_be_written_is_a_gap() -> None:
    with pytest.raises(ConversionGap, match="a saved field has no Python value"):
        control_default(_ctx(None), _control())  # type: ignore[arg-type]
