"""A primitive's Python names come from its catalog terminals' labels, or their
``python_name`` (``codegen/catalog_names.py``)."""

from __future__ import annotations

from lvkit.codegen.catalog_names import catalog_python_names
from lvkit.models import TerminalDirection
from lvkit.primitive_resolver.models import PrimitiveTerminal

IN = TerminalDirection.INPUT
OUT = TerminalDirection.OUTPUT


def _terminal(
    index: int,
    direction: TerminalDirection,
    name: str,
    type_: str = "polymorphic",
    python_name: str | None = None,
) -> PrimitiveTerminal:
    return PrimitiveTerminal(
        index=index,
        direction=direction,
        name=name,
        type=type_,
        python_name=python_name,
    )


def test_a_label_becomes_a_python_name() -> None:
    names = catalog_python_names(
        [
            _terminal(8, IN, "error in (no error)", "Error"),
            _terminal(9, IN, "byte order (0:big-endian, network order)", "UnitUInt16"),
            _terminal(0, OUT, "error out", "Error"),
            _terminal(3, OUT, "rest of the binary string", "String"),
        ]
    )
    assert names == {
        (IN, 8): "error_in",
        (IN, 9): "byte_order",
        (OUT, 0): "error_out",
        (OUT, 3): "rest_of_the_binary_string",
    }


def test_a_python_name_is_used_as_written() -> None:
    names = catalog_python_names(
        [
            _terminal(1, IN, "x"),
            _terminal(2, IN, "y"),
            _terminal(0, OUT, "x*y", python_name="product"),
        ]
    )
    assert names[(OUT, 0)] == "product"
    assert names[(IN, 1)] == "x"


def test_names_of_one_direction_are_distinct() -> None:
    names = catalog_python_names(
        [
            _terminal(1, IN, "x"),
            _terminal(2, IN, "x"),
            _terminal(0, OUT, "x"),
        ]
    )
    assert [names[(IN, 1)], names[(IN, 2)]] == ["x", "x_2"]
    # the other direction is its own scope
    assert names[(OUT, 0)] == "x"


def test_a_terminal_without_a_name_has_no_python_name() -> None:
    assert catalog_python_names([_terminal(4, OUT, "")]) == {}
