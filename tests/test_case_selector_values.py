"""Case frames carry the selector values the heap stores.

Covers the empty string, several strings per frame, a frame that matches values
AND is the default, enum default frames, heap values surviving a positionally
paired dataspace table, open integer ranges, the generated ``match`` patterns,
and the lvnet frame header form (``frame "Error", default :``).
"""

from __future__ import annotations

import ast

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes.case import _generate_match_case, generate
from lvkit.graph.lvnet_parse import _parse_case_frame_header
from lvkit.graph.models import CaseStructureNode
from lvkit.graph.op_walk import _selector_label, selector_frame_values
from lvkit.graph.render_lvnet import _lvnet_case_header
from lvkit.models import CaseFrame, LVType, LVTypeKind, SelectorRange, Terminal
from lvkit.parser.nodes.case import extract_case_structures
from tests.helpers import make_ctx
from tests.test_case_parser import (
    _build_case_xml,
    _ds_selector_table_obj,
    _make_terminal_info,
)


def _hex(text: str) -> str:
    return text.encode().hex()


def _one_case(
    type_name: str,
    select_ranges: list[tuple[int, ...]],
    **kwargs,
):
    root = _build_case_xml(
        "case", "selector", select_ranges=select_ranges, sel_type_id=42, **kwargs
    )
    return extract_case_structures(root, _make_terminal_info("selector", type_name))[0]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_empty_string_frame_keeps_its_empty_value() -> None:
    # Frame 0 matches "" (an element without text), frame 1 matches "x".
    case = _one_case(
        "String",
        [(0, 0), (1, 1)],
        string_array=["", _hex("x")],
        default_diag=2,
        num_diags=3,
    )
    assert [f.selector_value for f in case.frames] == ["", "x", "Default"]
    assert [f.selector_strings for f in case.frames] == [[""], ["x"], []]


def test_string_frame_keeps_every_value() -> None:
    # Diagram 0 matches strings 0 and 1; diagram 1 matches string 2.
    case = _one_case(
        "String",
        [(0, 0, 0), (1, 1, 0), (2, 2, 1)],
        string_array=[_hex("jpg"), _hex("jpeg"), _hex("png")],
        default_diag=2,
        num_diags=3,
    )
    assert case.frames[0].selector_strings == ["jpg", "jpeg"]
    assert case.frames[1].selector_strings == ["png"]


def test_string_frame_that_is_also_the_default_keeps_its_value() -> None:
    case = _one_case(
        "String",
        [(0, 0)],
        string_array=[_hex("TestCase")],
        default_diag=0,
        num_diags=2,
    )
    assert case.frames[0].is_default
    assert case.frames[0].selector_strings == ["TestCase"]


def test_enum_selector_without_saved_default_defaults_to_frame_zero() -> None:
    case = _one_case("UnitUInt8", [(0, 0), (1, 1)])
    assert case.selector_type == "enum"
    assert [f.is_default for f in case.frames] == [True, False]


@pytest.mark.parametrize("with_positional_table", [False, True])
def test_heap_values_survive_a_positionally_paired_table(
    with_positional_table: bool,
) -> None:
    root = _build_case_xml(
        "case", "selector", select_ranges=[(0, 0), (1, 1)], sel_type_id=42
    )
    # A table that belongs to some other case: one range, frame 1 unmapped.
    tables = (
        [_ds_selector_table_obj(33, 0, [(-(2**31), 0, 0)])]
        if with_positional_table
        else None
    )
    case = extract_case_structures(
        root, _make_terminal_info("selector", "UnitUInt8"), tables
    )[0]
    assert [[(r.start, r.end) for r in f.selector_ranges] for f in case.frames] == [
        [(0, 0)],
        [(1, 1)],
    ]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        # 0.. : bounded start, open end (I32)
        ((0, 2147483647, 1, 3), (0, False, True)),
        # ..-1 : open start, bounded end
        ((-2147483648, -1, 3, 1), (-1, True, False)),
        # 126.. on an I16 selector: the filler is that type's limit
        ((126, 32767, 1, 3), (126, False, True)),
    ],
)
def test_open_integer_range_drops_its_filler(
    entries: tuple[int, int, int, int], expected: tuple[int, bool, bool]
) -> None:
    start, end, start_type, end_type = entries
    root = _build_case_xml(
        "case",
        "selector",
        select_ranges=[(start, end, 0), (0, 0, 1)],
        range_types=[(start_type, end_type), (0, 0)],
        default_diag=1,
        sel_type_id=42,
    )
    case = extract_case_structures(root, _make_terminal_info("selector", "NumInt32"))[0]
    (r,) = case.frames[0].selector_ranges
    bound = r.end if r.open_start else r.start
    assert (bound, r.open_start, r.open_end) == expected
    label = _selector_label(case.frames[0], LVType(kind=LVTypeKind.PRIMITIVE), False)
    assert label == (f"..{bound}" if r.open_start else f"{bound}..")


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_string_frame_label_lists_every_value_then_default() -> None:
    string_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    frame = CaseFrame(selector_value="a", selector_strings=["a", ""], is_default=True)
    assert _selector_label(frame, string_type, False) == '"a", "", Default'
    assert selector_frame_values(frame, string_type, False) == ("a", "")


def test_integer_multi_value_frame_has_separate_values() -> None:
    frame = CaseFrame(
        selector_value="0",
        selector_ranges=[
            SelectorRange(start=0, end=0),
            SelectorRange(start=43, end=43),
        ],
    )
    assert selector_frame_values(frame, None, False) == ("0", "43")
    assert _selector_label(frame, None, False) == "0, 43"


# ---------------------------------------------------------------------------
# Generated Python
# ---------------------------------------------------------------------------


def _run_match(
    frames: list[CaseFrame], selector_type: LVType, selector_value: object
) -> int | None:
    """Generate the ``match`` for ``frames``, make each case return its frame
    number, and run it on ``selector_value``."""
    node = CaseStructureNode(
        id="case1",
        vi_path="test.vi",
        name="Case",
        node_type="caseStruct",
        terminals=[
            Terminal(id="sel", index=0, direction="input", lv_type=selector_type)
        ],
        selector_terminal="sel",
        frames=frames,
    )
    ctx: CodeGenContext = make_ctx("sel")
    fragment = _generate_match_case(node, "selector", ctx, {})
    (match_stmt,) = [s for s in fragment.statements if isinstance(s, ast.Match)]
    for case, frame in zip(match_stmt.cases, _case_order(frames), strict=True):
        case.body = [ast.Return(value=ast.Constant(value=frames.index(frame)))]
    func = ast.FunctionDef(
        name="run",
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="selector")],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=[match_stmt, ast.Return(value=ast.Constant(value=None))],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[]))
    namespace: dict[str, object] = {}
    exec(compile(module, "<case>", "exec"), namespace)  # noqa: S102
    run = namespace["run"]
    assert callable(run)
    return run(selector_value)


def _case_order(frames: list[CaseFrame]) -> list[CaseFrame]:
    """Frames in the order their ``case`` blocks are emitted: the default frame
    (a bare wildcard) goes last."""
    return [f for f in frames if not f.is_default] + [f for f in frames if f.is_default]


def test_string_selector_match_runs_for_empty_multiple_and_default() -> None:
    string_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    frames = [
        CaseFrame(selector_value="", selector_strings=[""]),
        CaseFrame(selector_value="jpg", selector_strings=["jpg", "jpeg", "jpe"]),
        CaseFrame(selector_value="Default", is_default=True),
        CaseFrame(selector_value="1", selector_strings=["1"]),
    ]
    assert _run_match(frames, string_type, "") == 0
    assert _run_match(frames, string_type, "jpg") == 1
    assert _run_match(frames, string_type, "jpeg") == 1
    assert _run_match(frames, string_type, "jpe") == 1
    assert _run_match(frames, string_type, "1") == 3
    assert _run_match(frames, string_type, "other") == 2


def test_string_frame_without_strings_matches_a_string_not_an_int() -> None:
    string_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    frames = [CaseFrame(selector_value="10"), CaseFrame(is_default=True)]
    assert _run_match(frames, string_type, "10") == 0
    assert _run_match(frames, string_type, "11") == 1


def test_enum_default_frame_is_a_catch_all() -> None:
    enum_type = LVType(kind=LVTypeKind.ENUM, underlying_type="UnitUInt8")
    frames = [
        CaseFrame(
            selector_value="0",
            is_default=True,
            selector_ranges=[SelectorRange(start=0, end=0)],
        ),
        CaseFrame(selector_value="1", selector_ranges=[SelectorRange(start=1, end=1)]),
    ]
    assert _run_match(frames, enum_type, 1) == 1
    assert _run_match(frames, enum_type, 7) == 0


def test_open_integer_ranges_compare_one_sided() -> None:
    int_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="I32")
    frames = [
        CaseFrame(
            selector_value="-1",
            selector_ranges=[SelectorRange(start=-(2**31), end=-1, open_start=True)],
        ),
        CaseFrame(
            selector_value="0",
            selector_ranges=[SelectorRange(start=0, end=2**31 - 1, open_end=True)],
        ),
    ]
    assert _run_match(frames, int_type, -5) == 0
    assert _run_match(frames, int_type, 0) == 1
    assert _run_match(frames, int_type, 2**40) == 1


# ---------------------------------------------------------------------------
# lvnet frame header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "is_default", "header"),
    [
        ((), True, "default"),
        (("Error",), True, '"Error", default'),
        (("A", "B"), False, '"A", "B"'),
        (("0", "43"), False, '"0", "43"'),
        (("", "x"), True, '"", "x", default'),
        (("a, default", 'q"uote', "line\nbreak"), False, None),
    ],
)
def test_case_frame_header_round_trips(
    values: tuple[str, ...], is_default: bool, header: str | None
) -> None:
    text = _lvnet_case_header(values, is_default)
    if header is not None:
        assert text == header
    _label, parsed_values, parsed_default = _parse_case_frame_header(text, 1, text)
    assert (parsed_values, parsed_default) == (values, is_default)


@pytest.mark.parametrize(
    ("underlying", "statement"), [("Boolean", ast.If), ("String", ast.Match)]
)
def test_boolean_selector_is_decided_by_type_not_by_frame_text(
    underlying: str, statement: type[ast.stmt]
) -> None:
    """A String case whose strings are "True" and "False" is a match, a Boolean
    case is an if/else."""
    selector_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)
    node = CaseStructureNode(
        id="case1",
        vi_path="test.vi",
        name="Case",
        node_type="caseStruct",
        terminals=[
            Terminal(id="sel", index=0, direction="input", lv_type=selector_type)
        ],
        selector_terminal="sel",
        frames=[
            CaseFrame(selector_value="False", selector_strings=["False"]),
            CaseFrame(selector_value="True", selector_strings=["True"]),
        ],
    )
    fragment = generate(node, make_ctx("sel"))
    assert [type(s) for s in fragment.statements if isinstance(s, statement)] == [
        statement
    ]
