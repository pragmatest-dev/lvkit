"""Scan From String code generation: a format string written on the diagram is an
``re`` pattern in the generated code, the code reads the text the way NI says,
and each output is converted to its type; a scan that stops early leaves the
later outputs at their defaults and gives error 85."""

from __future__ import annotations

import ast
import sys
from datetime import UTC
from typing import Any

import hightime
import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors_module import ERRORS_MODULE_NAME
from lvkit.codegen.gaps import Gap
from lvkit.codegen.labview_format import _lv_format_into_string
from lvkit.codegen.scan_module import SCAN_MODULE_SOURCE
from lvkit.graph.models import Constant, PrimitiveNode, VIContext, Wire
from lvkit.models import EnumValue, FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.timestamp_value import TIMESTAMP_FLAVOR
from tests.helpers import GENERATED_PACKAGE, build_graph, exec_generated
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.time_zone import needs_posix_tz
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


CPLX = primitive("NumComplex128")


@pytest.mark.parametrize(
    ("format_text", "text"),
    [(None, "1.5"), ("%g", "1.5"), ("%f", "1.5"), ("%e", "1.5E+0")],
)
def test_a_complex_output_reads_a_real_number_into_its_real_part(
    format_text: str | None, text: str
) -> None:
    assert _values(_result(format_text, [CPLX], text), 1) == [complex(1.5, 0.0)]


def test_a_complex_output_stops_after_the_real_part_of_a_sum() -> None:
    result = _result("%f", [CPLX], "2+2")
    assert _values(result, 1) == [complex(2, 0)]
    assert result.remaining == "+2"


def test_what_a_format_writes_for_a_complex_value_a_scan_reads_back() -> None:
    written = _lv_format_into_string("%f", "", [(complex(2.5, 7), "complex", 128, [])])
    assert written == "2.500000"
    assert _values(_result("%f", [CPLX], written), 1) == [complex(2.5, 0)]


def test_a_complex_format_wired_at_run_time_reads_what_a_constant_one_reads() -> None:
    for format_text in ("%g", "%f", "%e"):
        wired = _wired_result(format_text, [CPLX], "-1.5e3")
        assert _values(wired, 1) == [complex(-1500, 0)]


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
        ("%d", [primitive("NumComplex128")], "code 'd' into a complex output"),
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


def _enum(*labels: str) -> LVType:
    return LVType(
        kind=LVTypeKind.ENUM,
        underlying_type="UnitUInt16",
        values={label: EnumValue(value=i) for i, label in enumerate(labels)},
    )


COLOR = _enum("Red", "Green", "Blue")


def test_an_enum_reads_the_label_of_an_item_as_its_value() -> None:
    assert _values(_result("%s", [COLOR], "Blue"), 1) == [2]
    assert _values(_result(None, [COLOR], "Green rest"), 1) == [1]
    result = _result("color: %s", [COLOR], "color: Red")
    assert _values(result, 1) == [0]
    assert result.remaining == ""


def test_text_after_the_label_is_left_and_the_match_is_case_sensitive() -> None:
    result = _result("%s", [COLOR], "Greenish")
    assert _values(result, 1) == [1]
    assert result.remaining == "ish"
    result = _result("%s", [COLOR], "green", error=True, arguments={"error_in": None})
    assert _values(result, 1) == [0]
    assert result.error_out.code == 85


def test_a_label_that_matches_nothing_keeps_the_default_and_is_error_85() -> None:
    result = _result(
        "%s",
        [COLOR],
        "Purple",
        error=True,
        defaults=[2],
        arguments={"default0": 2, "error_in": None},
    )
    assert _values(result, 1) == [2]
    assert result.error_out.code == 85
    assert result.remaining == "Purple"


@pytest.mark.parametrize(
    ("format_text", "outputs", "reason"),
    [
        ("%s", [_enum("", "A")], "an empty one"),
        ("%3s", [COLOR], "a width with an enum output"),
        ("%d", [COLOR], "into a enum output"),
        ("%[a-z]", [COLOR], "a string into a enum output"),
    ],
)
def test_an_enum_scan_with_no_documented_result_is_a_gap(
    format_text: str, outputs: list[LVType], reason: str
) -> None:
    gaps: list[Gap] = []
    _, function = _scan(format_text, outputs, gaps=gaps)
    assert len(gaps) == 1
    assert reason in gaps[0].message
    with pytest.raises(NotImplementedError, match="Scan From String"):
        function(text="One")


def test_an_enum_label_with_braces_and_regular_expression_characters() -> None:
    odd = _enum("a{b}", "c.d")
    assert _values(_result("%s", [odd], "a{b}"), 1) == [0]
    assert _values(_result("%s", [odd], "c.d"), 1) == [1]


def test_the_longest_label_wins_whatever_the_order_of_the_items() -> None:
    for labels in (("Generic", "GenericClass"), ("GenericClass", "Generic")):
        enum = _enum(*labels)
        assert _values(_result("%s", [enum], "GenericClass"), 1) == [
            labels.index("GenericClass")
        ]
        assert _values(_result("%s", [enum], "Generic"), 1) == [
            labels.index("Generic")
        ]


# A time stamp: ``%<...>T`` reads the codes of the embedded time format.
TIME = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)


def _utc(*parts: int, **keywords: int) -> hightime.datetime:
    return hightime.datetime(*parts, **keywords, tzinfo=UTC)


def test_a_utc_time_stamp_reads_its_date_and_time() -> None:
    result = _result(
        "%^<%Y-%m-%dT%H:%M:%S>T", [TIME], "2026-11-16T10:20:30.123Z"
    )
    assert _values(result, 1) == [_utc(2026, 11, 16, 10, 20, 30)]
    assert result.remaining == ".123Z"


def test_the_fraction_of_a_second_reads_its_digits_exactly() -> None:
    result = _result("%^<%S%99u>T", [TIME], "30.123456789012")
    value = _values(result, 1)[0]
    assert (value.second, value.microsecond, value.femtosecond) == (
        30,
        123456,
        789012000,
    )
    result = _result("%,;%^<%99u>T", [TIME], ",5")
    assert _values(result, 1)[0].microsecond == 500000
    result = _result("%^<%H.%M.%S.%3u>T", [TIME], "01.02.03.456")
    assert _values(result, 1)[0].microsecond == 456000


def test_a_field_the_format_does_not_name_keeps_the_default_time_stamp() -> None:
    result = _result("%^<%H:%M>T", [TIME], "07:30")
    assert _values(result, 1) == [_utc(1904, 1, 1, 7, 30)]


def test_two_digit_years_follow_the_century_rule() -> None:
    assert _values(_result("%^<%y>T", [TIME], "68"), 1)[0].year == 2068
    assert _values(_result("%^<%y>T", [TIME], "69"), 1)[0].year == 1969


@needs_posix_tz
def test_a_time_stamp_without_the_flag_is_local_time(time_zone) -> None:
    time_zone("America/Denver")
    result = _result("%<%Y-%m-%d %H:%M>T", [TIME], "2026-07-01 12:00")
    assert _values(result, 1) == [_utc(2026, 7, 1, 18, 0)]
    result = _result("%<%Y-%m-%d %H:%M>T", [TIME], "2026-01-01 12:00")
    assert _values(result, 1) == [_utc(2026, 1, 1, 19, 0)]


def test_a_text_that_does_not_match_gives_the_default() -> None:
    result = _result("%^<%Y-%m-%d>T", [TIME], "20x6-11-16")
    assert _values(result, 1) == [_utc(1904, 1, 1)]


@pytest.mark.parametrize(
    ("format_text", "reason"),
    [
        ("%^<%b %d>T", "the code '%b'"),
        ("%^<%I:%M %p>T", "the code '%I'"),
        ("%^<%x>T", "the code '%x'"),
        ("%^<%Y %Y>T", "'%Y' twice"),
        ("%;%^<%99u>T", "the system decimal separator"),
        ("%^<%u>T", "without its digit count"),
        ("%+<%Y>T", "a flag"),
    ],
)
def test_a_time_code_the_scan_cannot_read_is_a_gap(
    format_text: str, reason: str
) -> None:
    gaps: list[Gap] = []
    _scan(format_text, [TIME], gaps=gaps)
    assert len(gaps) == 1
    assert reason in gaps[0].message


def test_a_time_into_a_number_is_seconds_since_1904() -> None:
    # JDP's "%[+-]%^<%H:%M>T" reads an hour and minute offset as seconds.
    result = _result("%^<%H:%M>T", [DBL], "05:30")
    assert _values(result, 1) == [5.5 * 3600]
    # The default is 100 s = 00:01:40: the seconds the format does not name stay.
    result = _result(
        "%^<%H:%M>T",
        [DBL],
        "05:30",
        defaults=[100.0],
        arguments={"default0": 100.0},
    )
    assert _values(result, 1) == [5.5 * 3600 + 40]
    result = _result("%[+-]%^<%H:%M>T", [STRING, DBL], "-01:15")
    assert _values(result, 2) == ["-", 4500.0]


def test_a_time_into_an_integer_is_a_gap() -> None:
    gaps: list[Gap] = []
    _scan("%^<%H:%M>T", [I32], gaps=gaps)
    assert "a time into a int output" in gaps[0].message


def test_a_fraction_of_a_second_is_exact_to_the_digit() -> None:
    result = _result("%^<%Y-%m-%dT%H:%M:%S%3u>T", [TIME], "2026-11-16T10:20:30.123Z")
    value = _values(result, 1)[0]
    assert value == _utc(2026, 11, 16, 10, 20, 30, 123000)
    assert (value.microsecond, value.femtosecond, value.yoctosecond) == (123000, 0, 0)
    assert result.remaining == "Z"


# A format string wired from a value the code computes is translated when the
# code runs, by the same translator (the package's ``scan`` module).


def _wired_result(
    format_text: str, outputs: list[LVType], text: str, **options: Any
) -> Any:
    arguments = {**options.pop("arguments", {}), "format_": format_text}
    _, function = _scan(format_text, outputs, wire_format=True, **options)
    return function(text=text, **arguments)


_RUN_TIME_CASES = [
    ("%d", [I32], "-42"),
    ("%d", [I32], "  \t 12abc"),
    ("%d:%d:%f", [I32, I32, DBL], "12:34:56.25"),
    ("%g", [DBL], "-1.5e3"),
    ("%,;%g", [DBL], "3,25"),
    ("%s %x", [STRING, I32], "name FF rest"),
    ("HTTP/%d.%d %d ", [I32, I32, I32], "HTTP/1.1   200 OK"),
    ("%[^,],%s", [STRING, STRING], "welcome to LabVIEW,Smith"),
    ("%3d%3d", [I32, I32], "0123456789"),
    ("%d", [U8], "300"),
    ("%d", [I8], "-300"),
    ("%d", [BOOL], "1"),
    ("%s", [BOOL], "YES"),
    ("%d%%", [I32], "50%"),
    ("%d:%d", [I32, I32], "12:ab"),
    ("%d", [I32], "abc"),
    ("%s", [COLOR], "Blue"),
    ("%s", [COLOR], "Purple"),
    ("%^<%Y-%m-%dT%H:%M:%S%3u>T", [TIME], "2026-11-16T10:20:30.123Z"),
    ("%^<%H:%M>T", [DBL], "05:30"),
    ("%p", [DBL], "12M"),
    ("%p", [DBL], "12.00M"),
    ("%p", [DBL], "10.0kHz"),
    ("%,;%p", [DBL], "1,5k"),
    ("%p %p", [DBL, DBL], "3u 4m"),
    ("%p", [CPLX], "2.5n"),
]

# NI, Format Specifier Syntax Examples, SI Notation (%p): format ``%.2p`` writes
# 12000000 as "12.00M" and ``%_2p`` as "12M"; a scan reads them back. Forum
# reports ("Bug with Scan from String using SI (%p) format code"): "10.0k" reads
# 10000, "10.0kHz" reads 10.
_SI_CASES = [
    ("12.00M", 12000000.0, ""),
    ("12M", 12000000.0, ""),
    ("1.5k", 1500.0, ""),
    ("3u", 3e-6, ""),
    ("-2.5m", -0.0025, ""),
    ("7", 7.0, ""),
    ("1y", 1e-24, ""),
    ("1Y", 1e24, ""),
    ("10.0k", 10000.0, ""),
    ("10.0kHz", 10.0, "kHz"),
    ("12uA", 12.0, "uA"),
    ("5k rest", 5000.0, " rest"),
    ("  5k", 5000.0, ""),
]


@pytest.mark.parametrize(("text", "value", "remaining"), _SI_CASES)
def test_an_si_prefix_scales_the_number_it_follows(
    text: str, value: float, remaining: str
) -> None:
    for read in (_result, _wired_result):
        result = read("%p", [DBL], text)
        assert _values(result, 1) == [pytest.approx(value, rel=1e-12)]
        assert result.remaining == remaining


def test_what_a_format_writes_with_p_a_scan_reads_back() -> None:
    for number in (12000000.0, 0.0015, -3.5e-9, 42.0):
        written = _lv_format_into_string("%.3p", "", [(number, "float", 64, [])])
        assert _values(_result("%p", [DBL], written), 1) == [
            pytest.approx(number, rel=1e-3)
        ]


def test_the_si_prefix_scan_is_written_into_the_module_with_no_lvkit_import() -> None:
    source, _ = _scan("%p", [DBL])
    assert "lvkit" not in source
    assert "re.match(" in source


@pytest.mark.parametrize("format_text", ["%#f", "%#d", "%#p", "%+p", "%#g"])
def test_a_scan_reads_no_modifier(format_text: str) -> None:
    gaps: list[Gap] = []
    _, function = _scan(format_text, [DBL], gaps=gaps)
    assert len(gaps) == 1
    assert "a modifier" in gaps[0].message
    with pytest.raises(NotImplementedError, match="no modifiers"):
        function(text="1")


def test_p_into_an_integer_output_is_a_gap() -> None:
    gaps: list[Gap] = []
    _scan("%p", [I32], gaps=gaps)
    assert len(gaps) == 1
    assert "'%p'" in gaps[0].message


@pytest.mark.parametrize(("format_text", "outputs", "text"), _RUN_TIME_CASES)
def test_a_format_wired_at_run_time_reads_what_a_constant_one_reads(
    format_text: str, outputs: list[LVType], text: str
) -> None:
    options: dict[str, Any] = {
        "error": True,
        "arguments": {"error_in": None},
    }
    constant = _result(format_text, outputs, text, **options)
    wired = _wired_result(format_text, outputs, text, **options)
    assert _values(wired, len(outputs)) == _values(constant, len(outputs))
    assert (wired.remaining, wired.offset) == (constant.remaining, constant.offset)
    assert (wired.error_out is None) == (constant.error_out is None)
    if wired.error_out is not None:
        assert wired.error_out.code == constant.error_out.code == 85
        assert wired.error_out.source == constant.error_out.source


def test_a_wired_format_keeps_the_default_and_the_initial_scan_location() -> None:
    result = _wired_result(
        "%d:%d",
        [I32, I32],
        "ab 12:cd",
        initial=True,
        error=True,
        defaults=[None, 7],
        arguments={"initial": 2, "default1": 7, "error_in": None},
    )
    assert _values(result, 2) == [12, 7]
    assert result.error_out.source == "Scan From String (arg 2)"
    assert (result.remaining, result.offset) == (":cd", 5)


def test_a_wired_format_is_translated_by_the_scan_module_not_written_in() -> None:
    source, _ = _scan("%d", [I32], wire_format=True)
    assert "scan_text(format_, text, " in source
    assert "re.match(" not in source
    assert "lvkit" not in source


def test_two_formats_wired_at_run_time_in_one_vi_each_read_by_their_own() -> None:
    _, function = _scan("%d", [I32], wire_format=True)
    assert function(text="12 34", format_="%d").out0 == 12
    assert function(text="12 34", format_="%x").out0 == 0x12
    first = function(text="ab-5", format_="ab%d")
    second = function(text="ab-5", format_="%x")
    assert (first.out0, first.remaining) == (-5, "")
    assert (second.out0, second.remaining) == (0xAB, "-5")


def test_a_format_with_no_conversion_raises_naming_it_when_the_code_runs() -> None:
    gaps: list[Gap] = []
    _, function = _scan("%d", [DBL], wire_format=True, gaps=gaps)
    assert gaps == []
    with pytest.raises(NotImplementedError) as refused:
        function(text="1.5", format_="%5.2f")
    assert "'%5.2f'" in str(refused.value)
    assert "a precision" in str(refused.value)
    with pytest.raises(NotImplementedError, match="has 2 specifiers"):
        function(text="1", format_="%d %d")


def test_the_scan_module_imports_nothing_from_the_package_or_lvkit() -> None:
    imported = [
        node
        for node in ast.walk(ast.parse(SCAN_MODULE_SOURCE))
        if isinstance(node, ast.ImportFrom | ast.Import)
    ]
    for node in imported:
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert (node.module or "").split(".")[0] != "lvkit"
        else:
            assert all(a.name.split(".")[0] != "lvkit" for a in node.names)
