"""A primitive's terminals show NI's label in every view and name the generated
Python by their ``python_name``, or by the label with a default parenthetical
dropped. One tiny diagram per kind of label (``codegen/catalog_names.py``)."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import render_lvnet
from tests.native_vi import generate, node, scalar
from tests.structure_diagrams import (
    BOOL,
    I32,
    STR,
    Views,
    load,
    primitive,
    root_diagram,
)

F64 = scalar("NumFloat64")
BOOLEAN = scalar("Boolean")


def _views(tmp_path: Path, prim_id: int, inputs: list, out_type: int) -> Views:
    tmp_path.mkdir(parents=True, exist_ok=True)
    xml = root_diagram([], [primitive(10, prim_id, inputs, out_type=out_type)], [])
    return load(tmp_path, xml)


def test_a_label_with_default_parentheticals_and_an_enum_default(
    tmp_path: Path,
) -> None:
    """Unflatten From String: the labels are NI's, parentheticals included; the
    lvnet quotes the one that holds ``, ``."""
    views = _views(tmp_path, 1165, [(8, STR), (9, I32), (10, BOOL), (11, STR)], STR)
    assert views.diagram.splitlines()[1:] == [
        "      in   error in (no error) : String default",
        '      in   "byte order (0:big-endian, network order)" : I32    default 0',
        "      in   data includes array or string size? (T) : TF     default True",
        "      in   binary string       : String default",
        "      out  error out           : String",
    ]
    assert [t["name"] for t in views.json["components"][0]["inputs"]] == [
        "error in (no error)",
        "byte order (0:big-endian, network order)",
        "data includes array or string size? (T)",
        "binary string",
    ]
    assert views.describe.endswith("Unflatten From String [prim 1165]")


def test_an_expression_label_is_shown_whole(tmp_path: Path) -> None:
    for prim_id, inputs, label in (
        (1049, [(1, I32)], '"-1, 0, 1"'),
        (1050, [(1, I32), (2, I32)], "x+y"),
        (1210, [(1, I32)], "log(x)"),
    ):
        views = _views(tmp_path / str(prim_id), prim_id, inputs, I32)
        assert f"      out  {label} : I32" in views.diagram


def test_a_label_that_is_not_a_default_keeps_its_parenthetical(
    tmp_path: Path,
) -> None:
    """``offset (in bytes)`` is a unit; its ``(0)`` is the default."""
    views = _views(tmp_path, 8073, [(5, I32), (6, I32)], STR)
    assert "in   offset (in bytes) (0)" in views.diagram


def test_every_label_round_trips_through_lvnet(tmp_path: Path) -> None:
    for prim_id, inputs in (
        (1165, [(8, STR), (9, I32), (10, BOOL), (11, STR)]),
        (1049, [(1, I32)]),
        (1102, [(1, I32), (2, I32)]),
        (1070, []),
    ):
        views = _views(tmp_path / str(prim_id), prim_id, inputs, I32)
        rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
        assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def test_the_generated_python_names_come_from_python_names() -> None:
    """Multiply's output is NI's ``x*y``; the Python variable is ``product``."""
    multiply = node(
        "m", 1052, [(0, "output", F64), (1, "input", F64), (2, "input", F64)]
    )
    generated = generate(
        {"x": F64, "y": F64},
        {"a": F64, "b": F64},
        [multiply],
        [("fp:x", "m:1"), ("fp:y", "m:2"), ("m:0", "fp:a"), ("m:0", "fp:b")],
    )
    assert "    product = x * y\n" in generated.source
    assert generated.function(3.0, 4.0).a == 12.0  # type: ignore[operator]


def test_a_comparison_names_its_output_as_before() -> None:
    equal = node(
        "e", 1102, [(0, "output", BOOLEAN), (1, "input", F64), (2, "input", F64)]
    )
    generated = generate(
        {"x": F64, "y": F64},
        {"a": BOOLEAN, "b": BOOLEAN},
        [equal],
        [("fp:x", "e:1"), ("fp:y", "e:2"), ("e:0", "fp:a"), ("e:0", "fp:b")],
    )
    assert "    equal = x == y\n" in generated.source


def test_sign_and_log10_generate_the_same_code() -> None:
    """Sign (1049) and Logarithm Base 10 (1210) are in no corpus sample."""
    sign = node("s", 1049, [(0, "output", scalar("NumInt32")), (1, "input", F64)])
    generated = generate(
        {"x": F64},
        {"s": scalar("NumInt32")},
        [sign],
        [("fp:x", "s:1"), ("s:0", "fp:s")],
    )
    assert "return NativeTestResult(s=(x > 0) - (x < 0))" in generated.source
    assert generated.function(-2.5).s == -1  # type: ignore[operator]
    log = node("l", 1210, [(0, "output", F64), (1, "input", F64)])
    generated = generate(
        {"x": F64}, {"y": F64}, [log], [("fp:x", "l:1"), ("l:0", "fp:y")]
    )
    expected = (
        "    logx = math.log10(x) if x > 0 else -math.inf if x == 0 else math.nan\n"
    )
    assert expected in generated.source
    assert generated.function(100.0).y == 2.0  # type: ignore[operator]
