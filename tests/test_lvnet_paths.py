"""A saved path is read by its recorded type (#177): absolute, relative or Not a
Path. Each case is a tiny diagram of Path constants and one Path control, written
as heap XML (``structure_diagrams.py``): the text lvnet, describe and JSON print,
that it reads back to the same graph, and the Python the path generates.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.context import _format_constant
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import Constant
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.models import LVType, LVTypeKind
from tests.structure_diagrams import (
    BOOL,
    PATH,
    PATH_TYPES,
    TYPES,
    Views,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    one_input_primitive,
    path_bytes,
    path_constant,
    root_diagram,
    srn,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity

_TYPES = TYPES + PATH_TYPES

CASES: list[tuple[str, bytes, str]] = [
    ("absolute", path_bytes(0, [b"C", b"Users"]), 'Path("/C/Users")'),
    ("absolute, no component", path_bytes(0, []), 'Path("/")'),
    ("relative", path_bytes(1, [b"test.xml"]), 'Path("test.xml")'),
    ("relative, no component", path_bytes(1, []), 'Path("")'),
    ("Not a Path", path_bytes(2, []), "NotAPath"),
    ("up a level", path_bytes(1, [b"", b"image.tif"]), 'Path("../image.tif")'),
    ("slash in a component", path_bytes(1, [b"a/b", b"c"]), 'Path("a\\/b/c")'),
    ("backslash in a component", path_bytes(1, [b"c\\d"]), 'Path("c\\\\d")'),
    ("quote in a component", path_bytes(1, [b'a"b']), 'Path("a\\"b")'),
]


def _constant_views(tmp_path: Path, saved: bytes) -> Views:
    xml = root_diagram(
        [], [srn(800, [path_constant(701, saved)]), increment(10, PATH)],
        [wire(901, 701, 13)],
    )
    return load(tmp_path, xml, _TYPES)


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


@pytest.mark.parametrize(("name", "saved", "text"), CASES)
def test_a_path_constant_is_written_by_its_type(
    tmp_path: Path, name: str, saved: bytes, text: str
) -> None:
    views = _constant_views(tmp_path, saved)
    assert views.diagram.splitlines()[1] == f"      in   x   : Path = {text}", name
    assert f"(unnamed): Path = {text}" in views.describe
    _round_trips(views)


@pytest.mark.parametrize(("name", "saved", "text"), CASES)
def test_a_path_control_default_is_written_by_its_type(
    tmp_path: Path, name: str, saved: bytes, text: str
) -> None:
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({20: "p"}, {}, pane={0: 20}, paths={20: saved})
    )
    xml = root_diagram(
        [increment(10, PATH)],
        [srn(800, [fp_terminal(5020, 20)])],
        [wire(901, 5020, 13)],
    )
    views = load(tmp_path, xml, _TYPES)
    assert "in   " in views.lvnet and f" default {text} @0\n" in views.lvnet, name
    _round_trips(views)


def test_a_path_of_an_unknown_type_is_unreadable(tmp_path: Path) -> None:
    views = _constant_views(tmp_path, path_bytes(3, [b"x"]))
    line = views.diagram.splitlines()[1]
    assert line.startswith("      in   x   : Path = ")
    assert "unknown type 3" in line


def _generated_function(tmp_path: Path, saved: bytes):
    """The generated function of a VI whose indicator ``result`` shows the Path
    constant ``saved``, with the source."""
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({}, {30: "result"}, pane={0: 30}, paths={30: path_bytes(2, [])})
    )
    xml = root_diagram(
        [], [srn(800, [path_constant(701, saved), fp_terminal(5030, 30)])],
        [wire(901, 701, 5030)],
    )
    return _generate(load(tmp_path, xml, _TYPES))


def _generate(views: Views):
    """The generated function ``t`` of ``views`` and the module source."""
    context = views.graph.get_vi_context(views.vi)
    source = build_module(context, "T.vi", graph=views.graph)
    namespace: dict[str, object] = {}
    exec(compile(source, "T.py", "exec"), namespace)  # noqa: S102
    return namespace["t"], source


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        (path_bytes(0, [b"C", b"Users"]), PurePosixPath("/C/Users")),
        (path_bytes(0, []), PurePosixPath("")),
        (path_bytes(1, [b"test.xml"]), PurePosixPath("test.xml")),
        (path_bytes(1, [b"", b"image.tif"]), PurePosixPath("../image.tif")),
        (path_bytes(2, []), None),
    ],
)
def test_a_path_constant_generates_a_path_or_none(
    tmp_path: Path, saved: bytes, expected: PurePosixPath | None
) -> None:
    function, source = _generated_function(tmp_path, saved)
    result = function().result
    assert (result is None) if expected is None else (str(result) == str(expected))
    assert "lvkit" not in source


# An absolute path with no component has no Python meaning, so it generates the
# empty path like a relative one.


def test_a_path_is_typed_path_or_none(tmp_path: Path) -> None:
    _, source = _generated_function(tmp_path, path_bytes(1, [b"x"]))
    assert "result: Path | None" in source


def _flag_function(tmp_path: Path, prim_res_id: int, saved: bytes):
    """The generated function of a VI whose Boolean indicator ``result`` is the
    primitive ``prim_res_id`` applied to the Path constant ``saved``."""
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({}, {30: "result"}, pane={0: 30})
    )
    node = one_input_primitive(10, prim_res_id, PATH, out_type=BOOL)
    xml = root_diagram(
        [],
        [srn(800, [path_constant(701, saved), fp_terminal(5030, 30)]), node],
        [wire(901, 701, 13), wire(902, 11, 5030)],
    )
    return _generate(load(tmp_path, xml, _TYPES))[0]


_NOT_A_NUMBER_PATH_REFNUM = 1128
_EMPTY_STRING_PATH = 1112


@pytest.mark.parametrize(
    ("saved", "is_not_a_path"),
    [
        (path_bytes(2, []), True),
        (path_bytes(0, []), False),
        (path_bytes(1, [b"x"]), False),
    ],
)
def test_not_a_number_path_refnum_is_true_for_not_a_path(
    tmp_path: Path, saved: bytes, is_not_a_path: bool
) -> None:
    function = _flag_function(tmp_path, _NOT_A_NUMBER_PATH_REFNUM, saved)
    assert function().result is is_not_a_path


@pytest.mark.parametrize(
    ("saved", "is_empty"),
    [
        (path_bytes(0, []), True),
        (path_bytes(1, []), True),
        (path_bytes(1, [b"x"]), False),
        (path_bytes(2, []), False),
    ],
)
def test_empty_string_path_is_true_for_a_path_with_no_component(
    tmp_path: Path, saved: bytes, is_empty: bool
) -> None:
    function = _flag_function(tmp_path, _EMPTY_STRING_PATH, saved)
    assert function().result is is_empty


def test_an_array_of_paths_generates_paths_and_none() -> None:
    lv_type = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array")
    value = '[Path("/a/b"), Path("x/.."), Path(""), NotAPath]'
    expression = _format_constant(Constant(id="c", value=value, lv_type=lv_type))
    actual = eval(expression, {"Path": PurePosixPath})
    assert actual == [
        PurePosixPath("/a/b"),
        PurePosixPath("x/.."),
        PurePosixPath(""),
        None,
    ]


def test_a_path_control_default_reads_the_same_in_describe(tmp_path: Path) -> None:
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml(
            {20: "p"}, {}, pane={0: 20}, paths={20: path_bytes(0, [b"C", b"x"])}
        )
    )
    xml = root_diagram(
        [increment(10, PATH)],
        [srn(800, [fp_terminal(5020, 20)])],
        [wire(901, 5020, 13)],
    )
    describe = load(tmp_path, xml, _TYPES).describe
    assert '  p: Path = Path("/C/x")' in describe
