"""The ``__eq__`` of a generated LabVIEW class: objects compare by value."""

from __future__ import annotations

import ast

_EQ_SOURCE = (
    "def __eq__(self, other: object) -> bool:\n"
    "    return type(self) is type(other) and vars(self) == vars(other)\n"
)


def value_equality_method() -> ast.stmt:
    """``__eq__`` for the class nearest the root: two objects are equal when they
    are of one class and every private data field, at every level of the class
    hierarchy, is equal. Objects of different classes are never equal."""
    return ast.parse(_EQ_SOURCE).body[0]
