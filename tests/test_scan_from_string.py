"""Scan From String code generation: a format string written on the diagram is an
``re`` pattern in the generated code, the code reads the text the way NI says,
and each output is converted to its type; a scan that stops early leaves the
later outputs at their defaults and gives error 85."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors_module import ERRORS_MODULE_NAME
from lvkit.codegen.gaps import Gap
from lvkit.graph.models import Constant, PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, LVType, Terminal
from tests.helpers import GENERATED_PACKAGE, build_graph, exec_generated
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.vilib_caller import BOOL, DBL, I32, STRING, U32, primitive

U8 = primitive("NumUInt8")
I8 = primitive("NumInt8")


def _scan(
    format_text: str | None,
    outputs: list[LVType],
    *,
    wired: dict[str, Any] | None = None,
    defaults: list[Any] | None = None,
    initial: bool = False,
    error: bool = False,
    wire_format: bool = False,
    gaps: list[Gap] | None = None,
) -> tuple[str, Any]:
    """The generated caller of one Scan From String node and the function itself.

    ``format_text`` is a string constant wired to the format string (``None``
    leaves it unwired; ``wire_format`` wires it from a control instead).
    ``outputs`` are the types of the scanned values, each wired to an indicator
    ``out0``, ``out1`` ...; ``defaults`` are the values of controls wired to the
    matching default inputs. The input string is the control ``text``; the
    remaining string, the offset and (``error``) the error out are indicators."""
    inputs: list[FPTerminal] = []
    indicators: list[FPTerminal] = []
    wires: list[Wire] = []
    terminals: list[Terminal] = []
    constants: list[Constant] = []

    def control(name: str, lv_type: LVType, index: int) -> None:
        inputs.append(
            FPTerminal(
                id=f"fp:{name}",
                index=len(inputs),
                direction="input",
                name=name,
                is_public=True,
                is_indicator=False,
                lv_type=lv_type,
            )
        )
        wires.append(
            Wire.from_terminals(
                from_terminal_id=f"fp:{name}", to_terminal_id=f"s:in{index}"
            )
        )

    def indicator(name: str, lv_type: LVType, index: int) -> None:
        indicators.append(
            FPTerminal(
                id=f"fp:{name}",
                index=len(indicators),
                direction="output",
                name=name,
                is_public=True,
                is_indicator=True,
                lv_type=lv_type,
            )
        )
        wires.append(
            Wire.from_terminals(
                from_terminal_id=f"s:out{index}", to_terminal_id=f"fp:{name}"
            )
        )

    def slot(index: int, direction: str, lv_type: LVType) -> None:
        side = "in" if direction == "input" else "out"
        terminals.append(
            Terminal(
                id=f"s:{side}{index}",
                index=index,
                direction=direction,  # type: ignore[arg-type]
                lv_type=lv_type,
            )
        )

    slot(0, "input", STRING)
    slot(1, "input", STRING)
    slot(2, "output", STRING)
    slot(3, "input", U32)
    slot(4, "output", I32)
    slot(5, "input", ERROR_CLUSTER)
    slot(6, "output", ERROR_CLUSTER)
    for position, lv_type in enumerate(outputs):
        slot(7 + 2 * position, "input", lv_type)
        slot(8 + 2 * position, "output", lv_type)

    if format_text is not None and not wire_format:
        constants.append(Constant(id="fmt", value=format_text, lv_type=STRING))
        wires.append(
            Wire.from_terminals(from_terminal_id="fmt", to_terminal_id="s:in0")
        )
    elif wire_format:
        control("format", STRING, 0)
    control("text", STRING, 1)
    indicator("remaining", STRING, 2)
    indicator("offset", I32, 4)
    if initial:
        control("initial", U32, 3)
    if error:
        control("error_in", ERROR_CLUSTER, 5)
        indicator("error_out", ERROR_CLUSTER, 6)
    for position, lv_type in enumerate(outputs):
        indicator(f"out{position}", lv_type, 8 + 2 * position)
    for position, value in enumerate(defaults or []):
        if value is not None:
            control(f"default{position}", outputs[position], 7 + 2 * position)

    node = PrimitiveNode(
        id="scan",
        vi_path="caller.vi",
        name="Scan From String",
        node_type="scanf",
        terminals=terminals,
    )
    context = VIContext(
        name="caller.vi",
        inputs=inputs,
        outputs=indicators,
        constants=constants,
        data_flow=wires,
    )
    graph = build_graph(context, "caller.vi", [node])
    found = gaps if gaps is not None else []
    source = build_module(context, "caller.vi", graph=graph, gaps=found)
    namespace = exec_generated(source)
    return source, namespace["caller"]


def _result(
    format_text: str | None,
    outputs: list[LVType],
    text: str,
    **options: Any,
) -> Any:
    arguments = options.pop("arguments", {})
    _, function = _scan(format_text, outputs, **options)
    return function(text=text, **arguments)


def _values(result: Any, count: int) -> list[Any]:
    return [getattr(result, f"out{i}") for i in range(count)]


def test_a_decimal_integer_reads_its_sign_and_digits() -> None:
    assert _values(_result("%d", [I32], "-42"), 1) == [-42]
    assert _values(_result("%d", [I32], "+7"), 1) == [7]


def test_white_space_before_a_number_is_ignored() -> None:
    assert _values(_result("%d", [I32], "  \t 12"), 1) == [12]


def test_the_remaining_string_and_offset_follow_the_scan() -> None:
    result = _result("%d", [I32], "12abc")
    assert (result.remaining, result.offset) == ("abc", 2)


def test_the_initial_scan_location_starts_the_scan_there() -> None:
    result = _result("%d", [I32], "ab 34 cd", initial=True, arguments={"initial": 2})
    assert _values(result, 1) == [34]
    assert (result.remaining, result.offset) == (" cd", 5)


def test_a_time_of_day_reads_each_part_by_its_type() -> None:
    result = _result("%d:%d:%f", [I32, I32, DBL], "12:34:56.25")
    assert _values(result, 3) == [12, 34, 56.25]
    assert result.remaining == ""


def test_a_float_reads_an_exponent() -> None:
    assert _values(_result("%g", [DBL], "-1.5e3"), 1) == [-1500.0]
    assert _values(_result("%f", [DBL], ".5"), 1) == [0.5]


def test_a_period_decimal_code_sets_the_separator() -> None:
    assert _values(_result("%.;%g", [DBL], "3.25"), 1) == [3.25]
    assert _values(_result("%,;%g", [DBL], "3,25"), 1) == [3.25]


def test_a_string_stops_at_white_space_and_hex_reads_hexadecimal() -> None:
    result = _result("%s %x", [STRING, I32], "name FF rest")
    assert _values(result, 2) == ["name", 255]
    assert result.remaining == " rest"


def test_a_string_reads_up_to_white_space_even_across_literal_text() -> None:
    result = _result("%s->%x", [STRING, I32], "name->FF")
    assert _values(result, 2) == ["name->FF", 0]


def test_a_status_line_scans_its_literal_text_and_spaces() -> None:
    result = _result("HTTP/%d.%d %d ", [I32, I32, I32], "HTTP/1.1   200 OK")
    assert _values(result, 3) == [1, 1, 200]
    assert result.remaining == "OK"


def test_a_flag_text_before_a_number() -> None:
    assert _values(_result("-p:%d", [I32], "-p:5000"), 1) == [5000]


def test_a_set_reads_only_its_characters() -> None:
    result = _result("%[a-z]%d%[a-z]%d", [STRING, I32, STRING, I32], "abcd012xyz3")
    assert _values(result, 4) == ["abcd", 12, "xyz", 3]


def test_a_negated_set_stops_at_a_listed_character() -> None:
    result = _result("%[^,],%s", [STRING, STRING], "welcome to LabVIEW,Smith")
    assert _values(result, 2) == ["welcome to LabVIEW", "Smith"]


def test_a_set_with_a_backslash_code_reads_white_space() -> None:
    result = _result("<size(s)=%[\\s0-9]>", [STRING], "<size(s)=1 2 3>")
    assert _values(result, 1) == ["1 2 3"]


def test_a_width_limits_the_characters_read() -> None:
    assert _values(_result("%3d%3d", [I32, I32], "0123456789"), 2) == [12, 345]
    assert _values(_result("%3s, %s", [STRING, STRING], "abc, xyz"), 2) == [
        "abc",
        "xyz",
    ]


def test_a_number_too_large_reads_as_the_largest_that_fits() -> None:
    assert _values(_result("%d", [U8], "300"), 1) == [255]
    assert _values(_result("%d", [I8], "-300"), 1) == [-128]
    assert _values(_result("%d", [U8], "-5"), 1) == [0]


def test_an_integer_is_read_into_a_float_and_a_number_into_a_boolean() -> None:
    assert _values(_result("%d", [DBL], "3"), 1) == [3.0]
    assert _values(_result("%d", [BOOL], "1"), 1) == [True]
    assert _values(_result("%d", [BOOL], "0"), 1) == [False]


def test_a_boolean_word_with_s_is_true_or_false() -> None:
    assert _values(_result("%s", [BOOL], "YES"), 1) == [True]
    assert _values(_result("%s", [BOOL], "off"), 1) == [False]


def test_a_literal_percent_is_scanned() -> None:
    result = _result("%d%%", [I32], "50%")
    assert _values(result, 1) == [50]
    assert result.remaining == ""


def test_a_scan_that_stops_early_keeps_the_defaults_and_reports_error_85() -> None:
    result = _result(
        "%d:%d",
        [I32, I32],
        "12:ab",
        error=True,
        defaults=[None, 7],
        arguments={"default1": 7, "error_in": None},
    )
    assert _values(result, 2) == [12, 7]
    assert result.error_out.code == 85
    assert result.error_out.source == "Scan From String (arg 2)"
    assert result.error_out.message.startswith("Scan failed")
    assert (result.remaining, result.offset) == (":ab", 2)


def test_a_scan_that_reaches_nothing_gives_the_type_defaults() -> None:
    result = _result("%d", [I32], "abc", error=True, arguments={"error_in": None})
    assert _values(result, 1) == [0]
    assert result.error_out.source == "Scan From String (arg 1)"
    assert result.remaining == "abc"


def test_a_scan_that_succeeds_has_no_error() -> None:
    result = _result("%d", [I32], "5", error=True, arguments={"error_in": None})
    assert result.error_out is None


def test_an_error_in_skips_the_scan_and_passes_the_error_on() -> None:
    exec_generated("")
    errors = sys.modules[f"{GENERATED_PACKAGE}.{ERRORS_MODULE_NAME}"]
    incoming = errors.LabVIEWError(code=7, source="x", message="m")
    result = _result("%d", [I32], "5", error=True, arguments={"error_in": incoming})
    assert result.error_out is incoming
    assert _values(result, 1) == [0]


def test_an_unwired_format_reads_each_output_by_its_type() -> None:
    assert _values(_result(None, [I32], "12 x"), 1) == [12]
    assert _values(_result(None, [DBL], "2.5"), 1) == [2.5]
    assert _values(_result(None, [STRING], "word more"), 1) == ["word"]
    assert _values(_result(None, [BOOL], "TRUE"), 1) == [True]
    assert _values(_result(None, [I32, DBL, STRING], "1 2.5 z"), 3) == [1, 2.5, "z"]


def test_the_pattern_is_written_into_the_module_with_no_lvkit_import() -> None:
    source, _ = _scan("%d:%d", [I32, I32])
    assert "re.match(" in source
    assert "import re" in source
    assert "lvkit" not in source


@pytest.mark.parametrize(
    ("format_text", "outputs", "reason"),
    [
        ("%5.2f", [DBL], "precision"),
        ("%b", [I32], "conversion code 'b'"),
        ("%;%f", [DBL], "the system decimal separator"),
        ("%3f", [DBL], "width with a floating-point code"),
        ("%f", [I32], "into a int output"),
        ("%d", [STRING], "a number into a string output"),
        ("%s", [I32], "a string into a int output"),
        ("%d", [I32, I32], "1 specifiers and the node has 2 outputs"),
    ],
)
def test_what_has_no_conversion_is_a_gap_with_its_reason(
    format_text: str, outputs: list[LVType], reason: str
) -> None:
    gaps: list[Gap] = []
    source, function = _scan(format_text, outputs, gaps=gaps)
    assert len(gaps) == 1
    assert reason in gaps[0].message
    with pytest.raises(NotImplementedError, match="Scan From String"):
        function(text="1")


def test_a_format_wired_at_run_time_is_a_gap() -> None:
    gaps: list[Gap] = []
    _, function = _scan("%d", [I32], wire_format=True, gaps=gaps)
    assert len(gaps) == 1
    assert "only known when the code runs" in gaps[0].message
