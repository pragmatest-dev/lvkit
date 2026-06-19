"""Element-wise numeric helpers for LabVIEW polymorphic primitives.

LabVIEW numeric functions (Add, Subtract, Sign, comparisons, …) operate
element-wise on arrays, broadcasting a scalar against an array. Generated
code routes a numeric primitive through these helpers only when one of its
operands is an array; pure-scalar uses keep the plain operator and never
touch this module.

Arrays are Python lists (lvkit's array representation); these helpers do not
change that. Nested arrays broadcast recursively, matching LabVIEW's
element-wise behaviour over multidimensional arrays.
"""

from __future__ import annotations

import operator as _op
from collections.abc import Callable


def _binop(a, b, f: Callable):
    a_arr, b_arr = isinstance(a, list), isinstance(b, list)
    if a_arr and b_arr:
        return [_binop(x, y, f) for x, y in zip(a, b)]
    if a_arr:
        return [_binop(x, b, f) for x in a]
    if b_arr:
        return [_binop(a, y, f) for y in b]
    return f(a, b)


def _unop(a, f: Callable):
    return [_unop(x, f) for x in a] if isinstance(a, list) else f(a)


def add(a, b):      return _binop(a, b, _op.add)
def sub(a, b):      return _binop(a, b, _op.sub)
def mul(a, b):      return _binop(a, b, _op.mul)
def truediv(a, b):  return _binop(a, b, _op.truediv)
def floordiv(a, b): return _binop(a, b, _op.floordiv)
def mod(a, b):      return _binop(a, b, _op.mod)
def pow_(a, b):     return _binop(a, b, _op.pow)
def gt(a, b):       return _binop(a, b, _op.gt)
def lt(a, b):       return _binop(a, b, _op.lt)
def ge(a, b):       return _binop(a, b, _op.ge)
def le(a, b):       return _binop(a, b, _op.le)
def eq(a, b):       return _binop(a, b, _op.eq)
def ne(a, b):       return _binop(a, b, _op.ne)
def neg(a):         return _unop(a, _op.neg)
