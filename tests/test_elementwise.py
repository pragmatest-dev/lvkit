"""Element-wise array arithmetic is written as list comprehensions."""

from __future__ import annotations

import ast

from lvkit.codegen.ast_utils import parse_expr, to_var_name
from lvkit.codegen.elementwise import arrayify, arrayify_module


def lifted(source: str, **ranks: int) -> str:
    new, _ = arrayify(parse_expr(source), ranks)
    return ast.unparse(new)


def evaluated(source: str, **values):
    ranks = {
        name: (1 + (1 if value and isinstance(value[0], list) else 0))
        for name, value in values.items()
        if isinstance(value, list)
    }
    new, _ = arrayify(parse_expr(source), ranks)
    return eval(ast.unparse(new), {}, values)


def test_array_and_array_zip():
    assert lifted("a - b", a=1, b=1) == "[_e0 - _e1 for _e0, _e1 in zip(a, b)]"
    assert evaluated("a - b", a=[3, 5, 7], b=[1, 2, 3]) == [2, 3, 4]


def test_mismatched_lengths_stop_at_the_shorter_array():
    assert evaluated("a + b", a=[1, 2, 3, 4, 5], b=[10, 20, 30]) == [11, 22, 33]


def test_scalar_with_array_has_no_zip():
    assert lifted("a - 5", a=1) == "[_e0 - 5 for _e0 in a]"
    assert evaluated("a - 5", a=[10, 20, 30]) == [5, 15, 25]
    assert evaluated("100 - a", a=[1, 2, 3]) == [99, 98, 97]


def test_a_chain_of_operators_is_one_comprehension():
    assert lifted("(x > 0) - (x < 0)", x=1) == "[(_e0 > 0) - (_e0 < 0) for _e0 in x]"
    assert evaluated("(x > 0) - (x < 0)", x=[-2.0, 0.0, 3.0]) == [-1, 0, 1]


def test_nested_arrays_nest_comprehensions():
    assert lifted("m + 1", m=2) == "[[_e1 + 1 for _e1 in _e0] for _e0 in m]"
    assert evaluated("m + 1", m=[[1, 2], [3, 4]]) == [[2, 3], [4, 5]]


def test_conversions_apply_to_every_element():
    assert lifted("int(round(a))", a=1) == "[int(round(_e0)) for _e0 in a]"
    assert lifted("-a", a=1) == "[-_e0 for _e0 in a]"


def test_unary_math_functions_apply_to_every_element():
    assert lifted("math.log10(a)", a=1) == "[math.log10(_e0) for _e0 in a]"


def test_fixed_width_conversions_apply_to_every_element():
    assert (
        lifted("ctypes.c_uint8(a).value", a=1)
        == "[ctypes.c_uint8(_e0).value for _e0 in a]"
    )


def test_scalar_expressions_are_unchanged():
    new, used = arrayify(parse_expr("foo(a, b)"), {})
    assert not used
    assert ast.unparse(new) == "foo(a, b)"
    assert lifted("a - b") == "a - b"


def test_new_names_avoid_names_in_the_expression():
    assert lifted("a - _e0", a=1) == "[_e1 - _e0 for _e1 in a]"


def test_module_pass_rewrites_scalar_plus_sliced_array():
    # `5.0 + arr[:3]` is a scalar plus a sliced array; a single index is a scalar.
    body = ast.parse("x = 5.0 + arr[:3]").body
    assert arrayify_module(body, {"arr": 1})
    assert ast.unparse(body[0]) == "x = [5.0 + _e0 for _e0 in arr[:3]]"
    scalar_index = ast.parse("y = 5.0 + arr[2]").body
    assert not arrayify_module(scalar_index, {"arr": 1})


def test_module_pass_keeps_list_concatenation():
    body = ast.parse("x = arr[:2] + [0] * 3\nz = arr[:2] + arr[3:]").body
    assert not arrayify_module(body, {"arr": 1})


def test_module_pass_reaches_call_keywords_and_conditions():
    body = ast.parse("return_value = f(r=a + 1)\nif a == 0:\n    pass").body
    assert arrayify_module(body, {"a": 1})
    assert ast.unparse(body[0]) == "return_value = f(r=[_e0 + 1 for _e0 in a])"
    assert ast.unparse(body[1].test) == "[_e0 == 0 for _e0 in a]"  # type: ignore[attr-defined]


def test_var_name_avoids_builtins():
    assert to_var_name("sum") == "sum_"
    assert to_var_name("list") == "list_"
    assert to_var_name("type") == "type_"
    # ordinary names untouched
    assert to_var_name("angle out 1") == "angle_out_1"
