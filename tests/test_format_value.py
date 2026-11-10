"""Format Value (1512): a number written by a format string after a string."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.primitive_resolver import get_resolver
from tests.error_diagrams import (
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)
from tests.prim_vi import Pin, describe_and_lvnet, generate, scalar

_DOUBLE = '<TypeDesc Type="NumFloat64" Prop1="0" Format="inline" />'


def _vi(number_type: str = "NumFloat64", *, format_wired: bool = True):
    controls = {"value": scalar(number_type)}
    if format_wired:
        controls["template"] = scalar("String")
    controls["prefix"] = scalar("String")
    return generate(
        1512,
        "Format Value",
        [
            Pin(1, "input", scalar(number_type), source="value"),
            Pin(
                2,
                "input",
                scalar("String"),
                source="template" if format_wired else None,
            ),
            Pin(3, "input", scalar("String"), source="prefix"),
            Pin(0, "output", scalar("String"), sink="text"),
        ],
        controls,
        {"text": scalar("String")},
    )


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=1512)
    assert entry is not None
    assert [(t.index, t.direction, t.type) for t in entry.terminals] == [
        (0, "output", "String"),
        (1, "input", "polymorphic"),
        (2, "input", "String"),
        (3, "input", "String"),
    ]


def test_describe_and_lvnet_name_the_function(tmp_path: Path) -> None:
    text, lvnet = describe_and_lvnet(
        tmp_path, 1512, [(0, True), (1, False), (2, False), (3, False)]
    )
    assert text.endswith("## Operations\nFormat Value [prim 1512]")
    assert "function Format_Value_10 : Format Value" in lvnet


def test_a_run_time_format_string_appends_the_formatted_number() -> None:
    source, fn = _vi()
    assert "lvkit" not in source
    assert fn(3.14159, "%.2f", "pi=").text == "pi=3.14"
    assert fn(42.0, "%d", "").text == "42"
    assert fn(2.5, "%6.1f", ">").text == ">   2.5"
    assert fn(255, "%x", "0x").text == "0xFF"


def test_an_integer_value() -> None:
    _, fn = _vi("NumUInt32")
    assert fn(255, "%d", "n=").text == "n=255"
    assert fn(255, "%x", "").text == "FF"


def test_the_same_rules_as_format_into_string() -> None:
    """Scientific notation and a zero-padded width are Format Into String's."""
    _, fn = _vi()
    assert fn(12345.678, "%e", "").text == "1.234568E+4"
    assert fn(7.0, "%05d", "").text == "00007"


def test_no_format_string_is_a_gap() -> None:
    _, fn = _vi(format_wired=False)
    with pytest.raises(NotImplementedError, match="no format string"):
        fn(1.0, "")


def _string_hex(text: str) -> str:
    return f"{len(text):08X}" + text.encode().hex().upper()


def test_a_format_string_constant_is_turned_into_an_f_string(
    project: Project,  # noqa: F811
) -> None:
    """The format string written on the diagram is one f-string while generating,
    as for Format Into String."""
    vi = Vi("Show Time.vi")
    value = vi.control("value", vi.add_type(_DOUBLE), REQUIRED)
    shown = vi.indicator("shown", STR)
    node = vi.primitive(
        1512,
        [
            ("output string", 0, STR, True),
            ("value", 1, vi.add_type(_DOUBLE), False),
            ("format string", 2, STR, False),
            ("string", 3, STR, False),
        ],
    )
    vi.wire(value, node["value"])
    vi.wire(vi.constant(STR, _string_hex("%.2f s")), node["format string"])
    vi.wire(vi.constant(STR, _string_hex("Time: ")), node["string"])
    vi.wire(node["output string"], shown)
    project.add(vi)
    package = project.generate("Show Time.vi")
    assert "lvkit" not in package.source
    assert "{value:.2f} s" in package.source
    assert package.function(3.14159).shown == "Time: 3.14 s"
