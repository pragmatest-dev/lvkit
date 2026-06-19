"""Tests for element-wise array arithmetic (LabVIEW numeric polymorphism)."""

from __future__ import annotations

import ast

from lvkit.codegen.ast_utils import parse_expr, to_var_name
from lvkit.codegen.elementwise import arrayify
from lvkit.runtime import lv

# --- runtime helper: scalar / list / broadcast ---


def test_scalar_passthrough():
    assert lv.sub(5, 2) == 3
    assert lv.add(5, 2) == 7
    assert lv.gt(5, 2) is True


def test_list_list_elementwise():
    assert lv.sub([3, 5, 7], [1, 2, 3]) == [2, 3, 4]
    assert lv.add([1, 2], [10, 20]) == [11, 22]


def test_mismatched_arrays_truncate_to_shortest():
    # LabVIEW: a binary numeric op on two arrays of different sizes outputs an
    # array the size of the *smaller* input. zip() stops at the shorter one.
    assert lv.add([1, 2, 3, 4, 5], [10, 20, 30]) == [11, 22, 33]
    assert lv.sub([10, 20], [1, 2, 3, 4]) == [9, 18]
    # recurses for 2D — ragged inner rows also truncate to the shorter row
    assert lv.mul([[1, 2, 3], [4, 5]], [[2, 2], [10, 10, 10]]) == [[2, 4], [40, 50]]


def test_scalar_array_broadcast():
    assert lv.sub([10, 20, 30], 5) == [5, 15, 25]
    assert lv.sub(100, [1, 2, 3]) == [99, 98, 97]


def test_comparison_and_sign_pattern():
    # Sign(x) = (x>0) - (x<0), element-wise
    x = [-2.0, 0.0, 3.0]
    sign = lv.sub(lv.gt(x, 0), lv.lt(x, 0))
    assert sign == [-1, 0, 1]


def test_nested_arrays_broadcast():
    assert lv.add([[1, 2], [3, 4]], 1) == [[2, 3], [4, 5]]


# --- the AST transform ---


def test_arrayify_binop():
    expr = parse_expr("a - b")
    new, used = arrayify(expr)
    assert used
    assert ast.unparse(new) == "_lv.sub(a, b)"


def test_arrayify_nested_compare():
    expr = parse_expr("(x > 0) - (x < 0)")
    new, used = arrayify(expr)
    assert used
    assert ast.unparse(new) == "_lv.sub(_lv.gt(x, 0), _lv.lt(x, 0))"


def test_arrayify_unary_neg():
    expr = parse_expr("-a")
    new, used = arrayify(expr)
    assert used
    assert ast.unparse(new) == "_lv.neg(a)"


def test_arrayify_no_ops_unchanged():
    expr = parse_expr("foo(a, b)")
    new, used = arrayify(expr)
    assert not used
    assert ast.unparse(new) == "foo(a, b)"


# --- builtin shadowing ---


def test_var_name_avoids_builtins():
    assert to_var_name("sum") == "sum_"
    assert to_var_name("list") == "list_"
    assert to_var_name("type") == "type_"
    # ordinary names untouched
    assert to_var_name("angle out 1") == "angle_out_1"
