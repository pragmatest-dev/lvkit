"""Format Into String code generation: the text it writes is the text LabVIEW
writes, for a format string on the diagram and for one wired at run time."""

from __future__ import annotations

import ast
import re
from typing import Any

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import printf
from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.models import LVType, LVTypeKind, Terminal, enum_values_from_labels
from lvkit.string_literal import quote_string
from lvkit.timestamp_value import TIMESTAMP_FLAVOR
from tests.helpers import register_nodes

BACKSLASH = chr(92)


def _primitive(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


STRING = _primitive("String")
PATH = _primitive("Path")
DBL = _primitive("NumFloat64")
I8 = _primitive("NumInt8")
I32 = _primitive("NumInt32")
U8 = _primitive("NumUInt8")
BOOL = _primitive("Boolean")
COMPLEX = _primitive("NumComplex128")
ENUM = LVType(
    kind=LVTypeKind.ENUM,
    values=enum_values_from_labels(["Off", "Low", "High", "Max Power"]),
)
UNLABELLED_ENUM = LVType(kind=LVTypeKind.ENUM)
RING_I16 = LVType(kind=LVTypeKind.RING, underlying_type="NumInt16")
ARRAY = LVType(kind=LVTypeKind.ARRAY, element_type=DBL)
TIME = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)


def _build(
    format_text: str | None,
    arguments: list[tuple[Any, LVType]],
    *,
    initial: str | None = None,
    wired_format: bool = False,
) -> tuple[str, dict[str, object]]:
    """The generated code of one Format Into String node, and the values its
    inputs hold. ``wired_format`` makes the format string a run-time value."""
    graph = InMemoryVIGraph()
    inputs: list[tuple[int, str, LVType]] = []
    values: dict[str, object] = {}
    if format_text is not None:
        inputs.append((0, "fmt_text", STRING))
        values["fmt_text"] = format_text
    if initial is not None:
        inputs.append((1, "initial", STRING))
        values["initial"] = initial
    for i, (value, lv_type) in enumerate(arguments):
        inputs.append((5 + i, f"arg{i}", lv_type))
        values[f"arg{i}"] = value
    sources = [
        PrimitiveNode(
            id=f"src_{name}",
            vi_path="t.vi",
            name="source",
            terminals=[Terminal(id=f"src_{name}_t", index=0, direction="output")],
        )
        for _, name, _ in inputs
    ]
    node = PrimitiveNode(
        id="fmt",
        vi_path="t.vi",
        name="Format Into String",
        terminals=[
            *(
                Terminal(
                    id=f"fmt_in{index}", index=index, direction="input", lv_type=lv_type
                )
                for index, _, lv_type in inputs
            ),
            Terminal(id="fmt_out", index=2, direction="output", name="formatted"),
        ],
    )
    register_nodes(graph, [*sources, node], vi_name="t.vi")
    for src, (index, _, _) in zip(sources, inputs, strict=True):
        graph._graph.add_edge(
            src.id,
            node.id,
            source=WireEnd(terminal_id=src.terminals[0].id, node_id=src.id),
            dest=WireEnd(terminal_id=f"fmt_in{index}", node_id=node.id),
        )
    ctx = CodeGenContext(graph=graph)
    for src, (_, name, _) in zip(sources, inputs, strict=True):
        if name == "fmt_text" and not wired_format:
            ctx.bind(src.terminals[0].id, quote_string(str(values[name])))
        else:
            ctx.bind(src.terminals[0].id, name)
    fragment = printf.generate(node, ctx)
    body: list[ast.stmt] = [
        ast.parse(statement).body[0] for statement in sorted(ctx.imports)
    ]
    body += ctx.formula_helpers
    body += fragment.statements
    module = ast.Module(body=body, type_ignores=[])
    return ast.unparse(ast.fix_missing_locations(module)), values


def _run(
    format_text: str | None,
    arguments: list[tuple[Any, LVType]],
    *,
    initial: str | None = None,
    wired_format: bool = False,
) -> str:
    source, values = _build(
        format_text, arguments, initial=initial, wired_format=wired_format
    )
    namespace: dict[str, object] = dict(values)
    exec(compile(source, "printf", "exec"), namespace)
    result = namespace["formatted"]
    assert isinstance(result, str)
    return result


NAN = float("nan")

CASES: list[tuple[str, list[tuple[Any, LVType]], str]] = [
    ("Temp: %5.1f", [(12.67, DBL)], "Temp:  12.7"),
    ("%.3e", [(12.67, DBL)], "1.267E+1"),
    ("%.2e", [(0.00123, DBL)], "1.23E-3"),
    ("%e", [(1e20, DBL)], "1.000000E+20"),
    ("score= %d%%", [(12.67, DBL)], "score= 13%"),
    ("%d", [(2.5, DBL)], "2"),
    ("%d", [(3.5, DBL)], "4"),
    ("%d", [(NAN, DBL)], "9223372036854775807"),
    ("%d", [(1e20, DBL)], "9223372036854775807"),
    ("%02x", [(12, I32)], "0C"),
    ("%06o", [(12, I32)], "000014"),
    ("%b", [(12, I32)], "1100"),
    ("%x", [(-1, I8)], "FF"),
    ("%x", [(-1, I32)], "FFFFFFFF"),
    ("%u", [(-1, I8)], "255"),
    ("%x", [(12.4, DBL)], "C"),
    ("%04b", [(2.5, DBL)], "0010"),
    ("%-5d|", [(42, I32)], "42   |"),
    ("%+d", [(5, I32)], "+5"),
    ("%05d", [(-42, I32)], "-0042"),
    ("%08.3e", [(12.67, DBL)], "1.267E+1"),
    ("%010.3e", [(12.67, DBL)], "001.267E+1"),
    ("%5.1f", [(3 + 4j, COMPLEX)], "  3.0"),
    ("%d", [(3, ENUM)], "3"),
    ("Mode: %s|", [(3, ENUM)], "Mode: Max Power|"),
    ("%-6s|", [(1, ENUM)], "Low   |"),
    ("%.2s", [(2, ENUM)], "Hi"),
    ("%d", [(3, RING_I16)], "3"),
    ("%x", [(-1, RING_I16)], "FFFF"),
    ("%08.3f", [(NAN, DBL)], "00000nan"),
    ("%010.3e", [(NAN, DBL)], "0000000nan"),
    ("%b", [(True, BOOL)], "1"),
    ("%x", [(False, BOOL)], "0"),
    ("Name: %s, %s.", [("Smith", STRING), ("John", STRING)], "Name: Smith, John."),
    ("String: %10.6s", [("Hello, World", STRING)], "String:     Hello,"),
    ("%2$s %1$s", [("Smith", STRING), ("John", STRING)], "John Smith"),
    ("%%", [], "%"),
    # NI, Format Specifier Syntax, Localization Codes: they take no input and write
    # nothing; they set the decimal separator of all further numbers until the next
    # ``%;``.
    ("%,;%f", [(1.5, DBL)], "1,500000"),
    ("%.;%f", [(1.5, DBL)], "1.500000"),
    ("%,;%.2f and %.3e", [(1.5, DBL), (12.67, DBL)], "1,50 and 1,267E+1"),
    (
        "%f %,;%f %.;%f",
        [(1.5, DBL), (2.5, DBL), (3.5, DBL)],
        "1.500000 2,500000 3.500000",
    ),
    ("%,;%d %s", [(7, I32), ("a.b", STRING)], "7 a.b"),
    ("%,;%2$s %1$f", [(1.5, DBL), ("x", STRING)], "x 1,500000"),
    ("%-8.1f|%,;", [(1.5, DBL)], "1.5     |"),
    ("%-8.1f|", [(1.5, DBL)], "1.5     |"),
    ("%,;%-8.1f|", [(1.5, DBL)], "1,5     |"),
    ("%-4s|", [("ab", STRING)], "ab  |"),
    ("%4s|", [("ab", STRING)], "  ab|"),
    ("%s", [("C:" + BACKSLASH + "x", PATH)], "C:" + BACKSLASH + "x"),
    ("%s", [(True, BOOL)], "TRUE"),
    ("%s", [(False, BOOL)], "FALSE"),
    ("%d", [(True, BOOL)], "1"),
    ("%f", [(True, BOOL)], "1.000000"),
    ("a" + BACKSLASH + "nb", [], "a\nb"),
    (BACKSLASH + "41", [], "A"),
    (BACKSLASH + "B", [], "\x0b"),
    (BACKSLASH + "BFare", [], "\xbfare"),
    (BACKSLASH + "q", [], "q"),
    (BACKSLASH * 2, [], BACKSLASH),
    (BACKSLASH + ">", [], ">"),
    ("x" + BACKSLASH * 2 + "%s", [("y", STRING)], "x" + BACKSLASH + "y"),
    (BACKSLASH + "b" + BACKSLASH + "f" + BACKSLASH + "r", [], "\b\f\r"),
    (BACKSLASH + "t" + BACKSLASH + "s|", [], "\t |"),
    (
        "Program Files\\\\NI\\\\LVAddons\\\\%s\\\\%s\\\\lvaddoninfo.json".replace(
            "\\\\", BACKSLASH * 2
        ),
        [("a", STRING), ("b", STRING)],
        "Program Files\\NI\\LVAddons\\a\\b\\lvaddoninfo.json",
    ),
    ('{\n    "AddonName": "%s"\n}', [("<7>", STRING)], '{\n    "AddonName": "<7>"\n}'),
    ("a } b { c %s", [("<7>", STRING)], "a } b { c <7>"),
    ('say """%s"""', [("<7>", STRING)], 'say """<7>"""'),
    ("it's %s", [("<7>", STRING)], "it's <7>"),
    ("100%% of %s", [("<7>", STRING)], "100% of <7>"),
    ("no placeholder {x}", [], "no placeholder {x}"),
    ("%s" + BACKSLASH, [("<7>", STRING)], "<7>"),
]


@pytest.mark.parametrize("wired_format", [False, True], ids=["diagram", "wired"])
@pytest.mark.parametrize(
    ("format_text", "arguments", "expected"),
    CASES,
    ids=[repr(case[0]) for case in CASES],
)
def test_the_text_is_what_labview_writes(
    format_text: str,
    arguments: list[tuple[Any, LVType]],
    expected: str,
    wired_format: bool,
) -> None:
    assert _run(format_text, arguments, wired_format=wired_format) == expected


@pytest.mark.parametrize("wired_format", [False, True], ids=["diagram", "wired"])
def test_the_system_localization_code_uses_the_locale_separator(
    wired_format: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import locale

    monkeypatch.setattr(locale, "localeconv", lambda: {"decimal_point": ";"})
    assert _run("%;%f", [(1.5, DBL)], wired_format=wired_format) == "1;500000"
    assert _run(
        "%,;%;%f %.;%f", [(1.5, DBL), (2.5, DBL)], wired_format=wired_format
    ) == ("1;500000 2.500000")


@pytest.mark.parametrize("wired_format", [False, True], ids=["diagram", "wired"])
def test_the_initial_string_comes_first(wired_format: bool) -> None:
    result = _run(
        "%s",
        [("x", STRING)],
        initial="C:" + BACKSLASH,
        wired_format=wired_format,
    )
    assert result == "C:" + BACKSLASH + "x"


def test_a_wired_format_string_is_applied_at_run_time() -> None:
    source, _ = _build("found %d items", [(3, I32)], wired_format=True)
    assert "_lv_format_into_string(fmt_text, ''," in source


def test_the_diagram_format_string_is_one_f_string_of_nodes() -> None:
    source, _ = _build('{"k": %s}', [("v", STRING)])
    assert source == "formatted = f'{{\"k\": {arg0}}}'"


def test_no_format_string_joins_strings_and_paths_with_a_space() -> None:
    assert _run(None, [("ab", STRING), ("cd", PATH)]) == "ab cd"


DEFAULT_CASES: list[tuple[list[tuple[Any, LVType]], str, str | None]] = [
    ([("a", STRING), ("b", STRING)], "a b", None),
    ([("a", STRING)], "a", None),
    ([(42, I32)], "42", None),
    ([(-7, I32)], "-7", None),
    ([(200, U8)], "200", None),
    ([(3, RING_I16)], "3", None),
    ([(1.234, DBL)], "1.234000", None),
    ([(2.0, DBL)], "2.000000", None),
    ([(3 + 4j, COMPLEX)], "3.000000", None),
    ([(True, BOOL)], "TRUE", None),
    ([(False, BOOL)], "FALSE", None),
    (
        [("x", STRING), (3, I32), (0.5, DBL), (True, BOOL)],
        "x 3 0.500000 TRUE",
        None,
    ),
    ([(1, I32)], "pre1", "pre"),
]


@pytest.mark.parametrize(
    ("arguments", "expected", "initial"),
    DEFAULT_CASES,
    ids=[case[1] for case in DEFAULT_CASES],
)
def test_no_format_string_writes_each_value_by_its_type(
    arguments: list[tuple[Any, LVType]], expected: str, initial: str | None
) -> None:
    assert _run(None, arguments, initial=initial) == expected


def test_no_format_string_and_a_timestamp_is_a_gap_for_its_time_format() -> None:
    with pytest.raises(ConversionGap, match=r"%<%\.3X %x>T.*embedded time format"):
        _build(None, [(1.5, TIME)])


def test_no_format_string_writes_an_enum_label() -> None:
    assert _run(None, [(3, ENUM)]) == "Max Power"


def test_no_format_string_and_an_enum_with_no_labels_is_a_gap() -> None:
    with pytest.raises(ConversionGap, match="enum with no item labels input"):
        _build(None, [(1, UNLABELLED_ENUM)])


NEGATIVE_FLOAT = "'%x' (a negative floating-point value) has no conversion"


@pytest.mark.parametrize("wired_format", [False, True], ids=["diagram", "wired"])
def test_a_negative_float_with_a_bit_code_raises_where_it_runs(
    wired_format: bool,
) -> None:
    assert _run("%x", [(12.4, DBL)], wired_format=wired_format) == "C"
    with pytest.raises(NotImplementedError, match=re.escape(NEGATIVE_FLOAT)):
        _run("%x", [(-4.2, DBL)], wired_format=wired_format)


def test_a_float_with_a_bit_code_is_a_gap_raised_only_when_negative() -> None:
    source, _ = _build("%x", [(1.0, DBL)])
    guard, formatted = source.split("\n")[-3:-1], source.split("\n")[-1]
    assert guard[0] == "if _lv_round_int(arg0) < 0:"
    assert NEGATIVE_FLOAT in guard[1] and "gap kind: unsupported_node" in guard[1]
    assert formatted == "formatted = f'{_lv_round_int(arg0):X}'"


def test_the_diagram_helpers_are_copied_only_when_used() -> None:
    plain, _ = _build("%s", [("v", STRING)])
    assert "_lv_" not in plain
    scientific, _ = _build("%e", [(1.0, DBL)])
    assert "def _lv_scientific" in scientific


GAPS: list[tuple[str, list[tuple[Any, LVType]], str]] = [
    ("%_3g", [(1.5, DBL)], "'%_3g' (significant digits)"),
    ("%g", [(1.5, DBL)], "'%g' (conversion code 'g')"),
    ("%p", [(1.5, DBL)], "'%p' (conversion code 'p')"),
    ("%t", [(1.5, DBL)], "'%t' (conversion code 't')"),
    ("%T", [(1.5, DBL)], "'%T' (conversion code 'T')"),
    ("%q", [(1.5, DBL)], "'%q' (conversion code 'q')"),
    ("%#x", [(1, I32)], "'%#x' ('#' modifier)"),
    ("%^d", [(1, I32)], "'%^d' ('^' modifier)"),
    ("%{m}f", [(1.5, DBL)], "'%{m}f' (unit)"),
    ("%<%H>t", [(1.5, DBL)], "'%<%H>t' (embedded time format)"),
    ("%+x", [(1, I32)], "'%+x' ('+' flag with code 'x')"),
    ("%d", [("a", STRING)], "'%d' (code 'd' with a string input)"),
    ("%s", [(1.5, DBL)], "'%s' ('s' with a numeric (float) input)"),
    ("%s", [(1, UNLABELLED_ENUM)], "'%s' (enum with no item labels input)"),
    ("%d", [([1.0], ARRAY)], "'%d' (array input)"),
    ("%d", [(0, TIME)], "'%d' (timestamp input)"),
    ("%s %s", [("a", STRING)], "specifier '%s' has no input to take"),
]


@pytest.mark.parametrize(
    ("format_text", "arguments", "part"),
    GAPS,
    ids=[repr(case[0]) for case in GAPS],
)
def test_a_specifier_with_no_conversion_is_a_gap(
    format_text: str, arguments: list[tuple[Any, LVType]], part: str
) -> None:
    with pytest.raises(ConversionGap) as gap:
        _build(format_text, arguments)
    assert part in str(gap.value)
    assert str(gap.value).startswith("Format Into String specifier")
    with pytest.raises(NotImplementedError) as at_run_time:
        _run(format_text, arguments, wired_format=True)
    assert str(at_run_time.value) == str(gap.value)
