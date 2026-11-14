"""A Python name drops a label's parenthetical only when it states the
terminal's default value (``codegen/label_names.py``)."""

from __future__ import annotations

import inspect

import pytest

from lvkit.codegen.ast_utils import interface_names
from lvkit.codegen.label_names import catalog_python_label, python_label
from lvkit.models import (
    EnumValue,
    LVType,
    LVTypeKind,
    ScalarValue,
    Terminal,
    TerminalDirection,
)
from lvkit.primitive_resolver.models import PrimitiveTerminal
from tests.error_diagrams import (
    ERR,
    I32,
    RECOMMENDED,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)
from tests.helpers import error_cluster_type


def _primitive(underlying: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)


def _term(
    label: str, lv_type: LVType, default: ScalarValue = None, uid: str = "1"
) -> Terminal:
    return Terminal(
        id=uid,
        index=0,
        direction=TerminalDirection.INPUT,
        name=label,
        lv_type=lv_type,
        default_value=default,
    )


MODE = LVType(
    kind=LVTypeKind.ENUM,
    underlying_type="NumUInt16",
    values={"read": EnumValue(value=0), "Write": EnumValue(value=1)},
)


@pytest.mark.parametrize(
    ("label", "lv_type", "default", "expected"),
    [
        # an error cluster: "no error" is its default
        ("error in (no error)", error_cluster_type(), None, "error in"),
        ("error in (none)", error_cluster_type(), None, "error in (none)"),
        # a Boolean: T/F/TRUE/FALSE equal to the default
        ("enable (T)", _primitive("Boolean"), "True", "enable"),
        ("enable (false)", _primitive("Boolean"), None, "enable"),
        ("enable (T)", _primitive("Boolean"), "False", "enable (T)"),
        # a numeric: a number equal to the default (0 when none is saved)
        ("count (0)", _primitive("NumInt32"), None, "count"),
        ("verbosity (1)", _primitive("NumUInt32"), "1", "verbosity"),
        ("verbosity (1)", _primitive("NumUInt32"), "2", "verbosity (1)"),
        ("limit (1,000)", _primitive("NumFloat64"), "1000.0", "limit"),
        # a unit is not a value: it stays
        ("timeout (ms)", _primitive("NumInt32"), "-1", "timeout (ms)"),
        # an enum: the item name of the default value
        ("mode (Write)", MODE, "1", "mode"),
        ("mode (read)", MODE, None, "mode"),
        ("mode (Write)", MODE, None, "mode (Write)"),
        # a string or path: a quoted string equal to the default
        ('name ("runTest")', _primitive("String"), "'runTest'", "name"),
        ('name ("other")', _primitive("String"), "'runTest'", 'name ("other")'),
        ('indent ("\\s")', _primitive("String"), "' '", "indent"),
        ('separator ("\\09")', _primitive("String"), "'\\t'", "separator"),
        ('prefix ("")', _primitive("String"), None, "prefix"),
        # only the last parenthetical counts
        ("x (old value) (0)", _primitive("NumInt32"), None, "x (old value)"),
        ("x (0) (old value)", _primitive("NumInt32"), None, "x (0) (old value)"),
        # a label that is only the parenthetical keeps it
        ("(0)", _primitive("NumInt32"), None, "(0)"),
    ],
)
def test_python_label_drops_only_a_parenthetical_that_states_the_default(
    label: str, lv_type: LVType, default: ScalarValue, expected: str
) -> None:
    assert python_label(_term(label, lv_type, default)) == expected


def test_labels_that_collide_after_the_drop_are_numbered() -> None:
    first = _term("count (0)", _primitive("NumInt32"), None, uid="a")
    second = _term("count (5)", _primitive("NumInt32"), "5", uid="b")
    unit = _term("timeout (ms)", _primitive("NumInt32"), None, uid="c")
    names = interface_names([first, second, unit], [], [])
    assert names.parameters == {"a": "count", "b": "count_2", "c": "timeout_ms"}


def test_a_caller_passes_the_names_the_called_vi_takes(
    project: Project,  # noqa: F811
) -> None:
    """A subVI with ``timeout (ms)`` and ``error in (no error)`` takes
    ``timeout_ms`` and ``error_in``; its caller passes them by those names and
    reads its results, and the code runs."""
    callee = Vi("Wait Step.vi")
    timeout = callee.control("timeout (ms)", I32, REQUIRED)
    ein = callee.control("error in (no error)", ERR, RECOMMENDED)
    elapsed = callee.indicator("elapsed (ms)", I32)
    eout = callee.error_out()
    callee.wire(timeout, elapsed)
    callee.wire(ein, eout)
    project.add(callee)

    caller = Vi("Caller.vi")
    x = caller.control("x", I32, REQUIRED)
    cin = caller.error_in()
    result = caller.indicator("result", I32)
    cout = caller.error_out()
    node = caller.call(
        "Wait Step.vi",
        [
            ("timeout (ms)", 0, I32, False),
            ("error in (no error)", 1, ERR, False),
            ("elapsed (ms)", 2, I32, True),
            ("error out", 3, ERR, True),
        ],
    )
    caller.wire(x, node["timeout (ms)"])
    caller.wire(cin, node["error in (no error)"])
    caller.wire(node["elapsed (ms)"], result)
    caller.wire(node["error out"], cout)
    project.add(caller)

    package = project.generate("Caller.vi")
    callee_module = package.stub("wait_step")
    assert list(inspect.signature(callee_module.wait_step).parameters) == [
        "timeout_ms",
        "error_in",
    ]
    assert "wait_step(timeout_ms=x, error_in=error_in)" in package.source
    assert package.function(x=5).result == 5
    error = package.error(7)
    assert package.function(x=5, error_in=error).error_out is error


def test_an_enum_default_may_be_written_value_colon_item() -> None:
    assert python_label(_term("mode (1:Write)", MODE, "1")) == "mode"
    assert python_label(_term("mode (0:read)", MODE, None)) == "mode"
    # the value must be the default's, and the item must be that value's
    assert python_label(_term("mode (0:Write)", MODE, "1")) == "mode (0:Write)"
    assert python_label(_term("mode (1:read)", MODE, "1")) == "mode (1:read)"


def _catalog(label: str, type_: str, default: ScalarValue = None) -> PrimitiveTerminal:
    return PrimitiveTerminal(
        index=0,
        direction=TerminalDirection.INPUT,
        name=label,
        type=type_,
        default=default,
    )


@pytest.mark.parametrize(
    ("label", "type_", "default", "expected"),
    [
        ("error in (no error)", "Error", None, "error in"),
        ("error in (no error)", "ErrorCluster", None, "error in"),
        ("data includes size? (T)", "Boolean", True, "data includes size?"),
        ("data includes size? (T)", "boolean", True, "data includes size?"),
        ("data includes size? (F)", "Boolean", True, "data includes size? (F)"),
        ("count (0)", "NumInt32", None, "count"),
        ("count (5)", "NumInt32", 5, "count"),
        # the catalog records a default of a polymorphic terminal only when NI
        # documents one; a number equal to it states it
        ("offset (0)", "polymorphic", 0, "offset"),
        ("offset (0)", "polymorphic", None, "offset (0)"),
        ("offset (0)", "numeric", 0, "offset"),
        # an enumerated default: the value is the default's; the item is text
        (
            "byte order (0:big-endian, network order)",
            "UnitUInt16",
            0,
            "byte order",
        ),
        ("mode (1:other)", "Enum16", 1, "mode"),
        ("mode (1:other)", "Enum16", 0, "mode (1:other)"),
        # a unit or a note is not a default
        ("timeout (ms)", "NumInt32", -1, "timeout (ms)"),
        ("abs(x)", "polymorphic", 0, "abs(x)"),
        ("delimiter (Tab)", "String", None, "delimiter (Tab)"),
        ('delimiter ("\\09")', "String", "'\\t'", "delimiter"),
    ],
)
def test_catalog_python_label_follows_the_same_rule(
    label: str, type_: str, default: ScalarValue, expected: str
) -> None:
    assert catalog_python_label(_catalog(label, type_, default)) == expected
