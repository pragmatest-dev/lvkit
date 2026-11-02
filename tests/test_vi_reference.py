"""VI references: a VI is a Python function, so the VI a reference holds is found
when the code is generated and the generated code imports and calls it.

Each case is a tiny diagram of just the construct (``vi_reference_diagrams.py``):
what ``describe`` and ``lvnet`` print for its nodes, the Python ``build_module``
writes, and the result of running that Python next to a hand-written callee.
"""

from __future__ import annotations

import builtins
import dataclasses
import importlib
import keyword
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from lvkit.codegen.ast_utils import module_part, to_function_name
from lvkit.codegen.builder import build_module
from lvkit.codegen.errors_module import ERRORS_MODULE_SOURCE
from lvkit.codegen.gaps import Gap
from lvkit.codegen.nodes import vi_reference
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.models import LVType, LVTypeKind
from tests import vi_reference_diagrams as d

_X = d.Constant(200, "5", "NumInt32", 41)
_PATH = d.Constant(300, 'Path("Callee_BDHb.xml")', "Path", 23)
_NAME = d.Constant(301, "'Callee.vi'", "String", 23)
_FORGET = d.Constant(302, "0x80", "NumInt32", 25)

AWKWARD_NAMES = (
    "Run",
    "1D Array",
    "a b-c",
    "__x",
    "Ünï.cödé",
    "Foo.llb",
    "-",
    "9",
    "",
    " ",
    "Get Settings Path",
    "TestCase.lvclass",
    "Class",
    "import",
    "None",
)

_NO_PANE = (
    "Call By Reference: which VI the reference holds is only known when the "
    "diagram runs, and its type records no connector pane to name the "
    "parameters by."
)
_STATIC = d.static_vi_reference(10, "Callee.vi")
_OPEN = d.open_vi_reference(20)
_CALL = d.call_through_reference(30)
_START = d.call_through_reference(30, "Start Asynchronous Call", save_style="01")
_NAME_OF_STATIC = d.vi_name_property(60, 500, "Name")
_PATH_OF_STATIC = d.vi_name_property(60, 501, "Path")

# static VI reference -> Call By Reference
_STATIC_CALL = ([_STATIC, _CALL], [d.wire(100, 11, 31)])
# Open VI Reference -> Call By Reference
_OPEN_CALL = ([_OPEN, _CALL], [d.wire(100, 21, 31)])
# static VI reference -> Name -> Open VI Reference -> Start Asynchronous Call
_STATIC_NAME_OPEN_START = (
    [_STATIC, _NAME_OF_STATIC, _OPEN, _START],
    [d.wire(100, 11, 61), d.wire(101, 69, 23), d.wire(102, 21, 31)],
)


def _source(loaded: d.Loaded) -> str:
    return build_module(
        loaded.graph.get_vi_context(loaded.caller), loaded.caller, graph=loaded.graph
    )


def _lvnet(loaded: d.Loaded) -> str:
    text = render_lvnet(build_netlist_from_graph(loaded.graph, loaded.caller))
    return text.split("\n", 1)[1]


def _failure(loaded: d.Loaded) -> str:
    """The reason of the first gap the caller has; its source raises there."""
    gaps: list[Gap] = []
    source = build_module(
        loaded.graph.get_vi_context(loaded.caller),
        loaded.caller,
        graph=loaded.graph,
        gaps=gaps,
    )
    assert "raise NotImplementedError(" in source
    return gaps[0].reason


def test_call_by_reference_frame_terminals_have_their_own_indices(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, *_STATIC_CALL)
    call = next(
        n
        for n in loaded.graph.iter_nodes(loaded.caller)
        if n.node_type == "callByRefNode"
    )
    assert [(t.index, t.direction) for t in call.terminals] == [
        (-4, "output"),
        (-3, "input"),
        (-2, "output"),
        (-1, "input"),
        (0, "input"),
        (1, "output"),
    ]
    assert call.call_save_style == 0


def test_a_saved_call_style_reaches_the_node(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, *_STATIC_NAME_OPEN_START)
    start = next(
        n
        for n in loaded.graph.iter_nodes(loaded.caller)
        if n.node_type == "callByRefNode"
    )
    assert start.call_save_style == 1


def test_static_reference_then_call_views_and_source(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, *_STATIC_CALL, [_X])
    assert describe_vi(loaded.graph, loaded.caller).endswith(
        "## Operations\nCall By Reference"
    )
    assert _lvnet(loaded).endswith(
        '    constant Callee_10 : VIRefnum = "Callee.vi"\n'
        "    subVI Call_By_Reference_30 : Call By Reference\n"
        '      in   0  : ?        = 5\n'
        "      in   -3 : ?        default\n"
        "      in   -1 : VIRefnum = Callee_10\n"
        "      out  1  : ?\n"
        "      out  -4 : ?\n"
        "      out  -2 : ?"
    )
    assert _source(loaded).endswith(
        "from .callee import callee\n\ndef refs():\n    callee(x=5)"
    )


def test_open_vi_reference_of_a_constant_path_calls_that_vi(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, *_OPEN_CALL, [_PATH, _X])
    assert describe_vi(loaded.graph, loaded.caller).endswith(
        "## Operations\nOpen VI Reference [prim 8010]\nCall By Reference"
    )
    assert _lvnet(loaded).endswith(
        "    function Open_VI_Reference_20 : Open VI Reference\n"
        "      in   options      : ? default\n"
        '      in   vi_path      : ? = Path("Callee_BDHb.xml")\n'
        "      out  vi_reference : ?\n"
        "    subVI Call_By_Reference_30 : Call By Reference\n"
        '      in   0  : ? = 5\n'
        "      in   -3 : ? default\n"
        "      in   -1 : ? = Open_VI_Reference_20::vi_reference\n"
        "      out  1  : ?\n"
        "      out  -4 : ?\n"
        "      out  -2 : ?"
    )
    assert _source(loaded).endswith(
        "from .callee import callee\n\ndef refs():\n    callee(x=5)"
    )


def test_open_vi_reference_of_a_path_built_from_constants_calls_that_vi(
    tmp_path: Path,
) -> None:
    constants = [
        d.Constant(300, 'Path("Callee_BDHb.xml")', "Path", 83),
        d.Constant(304, 'Path("sub/..")', "Path", 85),
        _X,
    ]
    loaded = d.load(
        tmp_path,
        [*_OPEN_CALL[0], d.build_path(80)],
        [*_OPEN_CALL[1], d.wire(101, 81, 23)],
        constants,
    )
    source = _source(loaded)
    assert "from .callee import callee\n" in source
    assert source.endswith("    callee(x=5)")


def test_open_vi_reference_of_a_constant_name_calls_the_vi_of_that_name(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, *_OPEN_CALL, [_NAME, _X])
    assert _source(loaded).endswith(
        "from .callee import callee\n\ndef refs():\n    callee(x=5)"
    )


def test_open_vi_reference_of_a_name_no_loaded_vi_is_a_gap(
    tmp_path: Path,
) -> None:
    other = d.Constant(300, "'Other.vi'", "String", 23)
    loaded = d.load(tmp_path, *_OPEN_CALL, [other, _X])
    assert _failure(loaded) == (
        "Open VI Reference: The VI named 'Other.vi' is not loaded with this VI."
    )


def test_static_reference_name_open_start_submits_the_call(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, *_STATIC_NAME_OPEN_START, [_X])
    assert describe_vi(loaded.graph, loaded.caller).endswith(
        "## Operations\nProperty Node [Property Node]\n"
        "Open VI Reference [prim 8010]\nStart Asynchronous Call"
    )
    assert _lvnet(loaded).endswith(
        '    constant Callee_10 : VIRefnum = "Callee.vi"\n'
        "    property-node Property_Node_60 : VI\n"
        "      in   0    : VIRefnum = Callee_10\n"
        "      in   2    : ?        default\n"
        "      out  1    : ?\n"
        "      out  3    : ?\n"
        "      out  Name : ?\n"
        "    function Open_VI_Reference_20 : Open VI Reference\n"
        "      in   options      : ? default\n"
        "      in   vi_path      : ? = Property_Node_60::Name\n"
        "      out  vi_reference : ?\n"
        "    subVI Start_Asynchronous_Call_30 : Start Asynchronous Call\n"
        '      in   0  : ? = 5\n'
        "      in   -3 : ? default\n"
        "      in   -1 : ? = Open_VI_Reference_20::vi_reference\n"
        "      out  1  : ?\n"
        "      out  -4 : ?\n"
        "      out  -2 : ?"
    )
    source = _source(loaded)
    assert source.endswith(
        "from .callee import callee\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "_async_call_pool = ThreadPoolExecutor()\n\n"
        "def refs():\n"
        "    _async_call_pool.submit(callee, x=5)"
    )
    assert "_vi_registry" not in source and "lvkit.runtime" not in source


def test_call_and_forget_start_submits_the_call_the_same_way(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, *_STATIC_NAME_OPEN_START, [_X, _FORGET])
    assert _source(loaded).endswith("    _async_call_pool.submit(callee, x=5)")


def test_start_whose_open_options_are_computed_keeps_the_call(
    tmp_path: Path,
) -> None:
    nodes = [_STATIC, _NAME_OF_STATIC, _OPEN, _START, d.open_vi_reference(80)]
    wires = [
        d.wire(100, 11, 61),
        d.wire(101, 69, 23),
        d.wire(102, 21, 31),
        d.wire(103, 81, 25),
    ]
    loaded = d.load(
        tmp_path, nodes, wires, [_X, d.Constant(303, "'Callee.vi'", "String", 83)]
    )
    assert _source(loaded).endswith("    _async_call_pool.submit(callee, x=5)")


def test_the_options_of_an_open_vi_reference_are_folded_from_constants(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, *_STATIC_NAME_OPEN_START, [_X, _FORGET])
    (open_node,) = [
        n
        for n in loaded.graph.iter_nodes(loaded.caller)
        if isinstance(n, PrimitiveNode) and n.prim_id == 8010
    ]
    assert loaded.graph.opened_vi(open_node).options == 0x80


def test_a_call_style_no_corpus_node_is_a_gap(tmp_path: Path) -> None:
    wait = d.call_through_reference(30, "Wait On Asynchronous Call", save_style="02")
    loaded = d.load(tmp_path, [_STATIC, wait], [d.wire(100, 11, 31)], [_X])
    assert _failure(loaded) == (
        "Wait On Asynchronous Call: this kind of call by reference (saved style "
        "0x2) is not supported yet; Wait On Asynchronous Call has no code "
        "generation yet."
    )


def test_the_style_comes_from_the_saved_field_not_the_title(tmp_path: Path) -> None:
    titled = d.call_through_reference(30, "Wait On Asynchronous Call", save_style="00")
    loaded = d.load(tmp_path, [_STATIC, titled], [d.wire(100, 11, 31)], [_X])
    assert _source(loaded).endswith("    callee(x=5)")


_PATH_TYPE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
_STRING_TYPE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


def _computed(
    tmp_path: Path,
    value_type: LVType | None,
    nodes: tuple[str, ...] = (),
    wires: tuple[str, ...] = (),
    constants: tuple[d.Constant, ...] = (),
) -> d.Loaded:
    """An Open VI Reference whose path or name is a control of the VI, so it is
    only known when the code runs."""
    loaded = d.load(tmp_path, [_OPEN, *nodes], [*wires], constants)
    d.add_control(loaded, "vi_path", value_type, 23, uid="vi_path")
    return loaded


def test_open_vi_reference_of_a_computed_path_imports_the_module_at_run_time(
    tmp_path: Path,
) -> None:
    loaded = _computed(tmp_path, _PATH_TYPE)
    source = _source(loaded)
    assert "import importlib\n" in source
    assert "lvkit" not in source and "_vi_registry" not in source
    assert "os.path.relpath" in source and "importlib.import_module(" in source
    assert "getattr(vi_reference_module, _function_name(" in source


def test_a_computed_path_opens_and_calls_the_vi_at_that_path(tmp_path: Path) -> None:
    loaded = _computed(tmp_path, _PATH_TYPE, (_CALL,), (d.wire(102, 21, 31),), (_X,))
    d.type_terminal(loaded, 21, d.typed_vi_reference())
    d.type_terminal(loaded, 31, d.typed_vi_reference())
    for refs in _package(tmp_path, _source(loaded), _CALLEE):
        callee = importlib.import_module("generated_refs.callee")
        refs.refs(Path("Callee.vi"))  # type: ignore[attr-defined]
        refs.refs(Path("./Callee.vi"))  # type: ignore[attr-defined]
        assert len(callee.calls) == 2


def test_a_computed_path_may_climb_to_a_parent_folder(tmp_path: Path) -> None:
    loaded = _computed(tmp_path, _PATH_TYPE, (_CALL,), (d.wire(102, 21, 31),), (_X,))
    d.type_terminal(loaded, 21, d.typed_vi_reference())
    d.type_terminal(loaded, 31, d.typed_vi_reference())
    package = tmp_path / "generated_refs"
    (package / "sub").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sub" / "__init__.py").write_text("")
    (package / "callee.py").write_text(_CALLEE)
    (package / "sub" / "refs.py").write_text(_source(loaded))
    sys.path.insert(0, str(tmp_path))
    try:
        refs = importlib.import_module("generated_refs.sub.refs")
        refs.refs(Path("../Callee.vi"))
        callee = importlib.import_module("generated_refs.callee")
        assert [x for x, _ in callee.calls] == [5]
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m.startswith("generated_refs")]:
            del sys.modules[name]


def test_a_computed_path_with_backslashes_is_read_like_one_with_slashes(
    tmp_path: Path,
) -> None:
    loaded = _computed(tmp_path, _PATH_TYPE, (_CALL,), (d.wire(102, 21, 31),), (_X,))
    d.type_terminal(loaded, 21, d.typed_vi_reference())
    d.type_terminal(loaded, 31, d.typed_vi_reference())
    package = tmp_path / "generated_refs"
    (package / "sub").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sub" / "__init__.py").write_text("")
    (package / "sub" / "callee.py").write_text(_CALLEE)
    (package / "refs.py").write_text(_source(loaded))
    sys.path.insert(0, str(tmp_path))
    try:
        refs = importlib.import_module("generated_refs.refs")
        refs.refs("sub\\Callee.vi")
        callee = importlib.import_module("generated_refs.sub.callee")
        assert [x for x, _ in callee.calls] == [5]
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m.startswith("generated_refs")]:
            del sys.modules[name]


_CLASS_CALLEE = (
    "from typing import NamedTuple\n"
    "calls = []\n"
    "class CalleeResult(NamedTuple):\n"
    "    y: int\n"
    "class Owner:\n"
    "    @staticmethod\n"
    "    def callee_bdhbxml(x=None):\n"
    "        calls.append((x, None))\n"
    "        return CalleeResult(y=0)\n"
)


def test_a_computed_name_finds_the_method_of_its_class(tmp_path: Path) -> None:
    loaded = _computed(tmp_path, _STRING_TYPE)
    d.own_by_class(loaded, "Owner.lvclass")
    source = _source(loaded)
    assert "_vi_owners = {}\n" in source
    assert "'Owner.lvclass:Callee.vi': ('.owner', 'Owner', 'callee_bdhbxml')" in source
    for refs in _package(tmp_path, source, _CLASS_CALLEE, "owner"):
        refs.refs("Owner.lvclass:Callee.vi")  # type: ignore[attr-defined]
        with pytest.raises(KeyError):
            refs.refs("Other.lvclass:Callee.vi")  # type: ignore[attr-defined]


def test_a_call_by_reference_of_a_static_method_found_when_the_code_runs_calls_it(
    tmp_path: Path,
) -> None:
    loaded = _computed(tmp_path, _STRING_TYPE, (_CALL,), (d.wire(102, 21, 31),), (_X,))
    d.type_terminal(loaded, 21, d.typed_vi_reference())
    d.type_terminal(loaded, 31, d.typed_vi_reference())
    d.own_by_class(loaded, "Owner.lvclass")
    source = _source(loaded)
    assert source.endswith("    vi_reference(x=5)")
    for refs in _package(tmp_path, source, _CLASS_CALLEE, "owner"):
        refs.refs("Owner.lvclass:Callee.vi")  # type: ignore[attr-defined]
        assert sys.modules["generated_refs.owner"].calls == [(5, None)]


def test_a_computed_name_with_no_owner_in_the_project_finds_nothing(
    tmp_path: Path,
) -> None:
    loaded = _computed(tmp_path, _STRING_TYPE)
    assert "_vi_owners = {}\n" in _source(loaded)


def test_open_vi_reference_of_an_untyped_computed_value_is_a_gap(
    tmp_path: Path,
) -> None:
    loaded = _computed(tmp_path, None)
    assert _failure(loaded) == (
        "Open VI Reference: the computed VI path is neither a path nor a string."
    )


def test_a_reference_opened_by_a_computed_path_is_called_with_the_panes_names(
    tmp_path: Path,
) -> None:
    reference = d.typed_vi_reference()
    loaded = _computed(tmp_path, _PATH_TYPE, (_CALL,), (d.wire(102, 21, 31),), (_X,))
    d.type_terminal(loaded, 21, reference)
    d.type_terminal(loaded, 31, reference)
    source = _source(loaded)
    assert source.endswith("    vi_reference(x=5)")
    for refs in _package(tmp_path, source, _CALLEE):
        callee = importlib.import_module("generated_refs.callee")
        refs.refs(Path("Callee.vi"))  # type: ignore[attr-defined]
        assert [x for x, _ in callee.calls] == [5]


def test_the_inline_name_transform_is_the_generators() -> None:
    namespace: dict[str, object] = {"keyword": keyword}
    exec(vi_reference.MODULE_PART_SOURCE, namespace)  # noqa: S102
    for name in AWKWARD_NAMES:
        assert namespace["_module_part"](name) == module_part(name)  # type: ignore[operator]


def test_the_inline_function_name_is_the_generators() -> None:
    namespace: dict[str, object] = {"keyword": keyword, "builtins": builtins}
    exec(vi_reference.MODULE_PART_SOURCE, namespace)  # noqa: S102
    exec(vi_reference.FUNCTION_NAME_SOURCE, namespace)  # noqa: S102
    for stem in ("id", "str", "Run Test", "1D Test", "class", "List", "a-b", "Foo.llb"):
        assert namespace["_function_name"](stem) == to_function_name(f"{stem}.vi")  # type: ignore[operator]


def test_the_name_helpers_are_defined_once_for_the_module(tmp_path: Path) -> None:
    loaded = _computed(tmp_path, _PATH_TYPE, nodes=(d.open_vi_reference(80),))
    d.add_control(loaded, "second", _PATH_TYPE, 83, uid="second")
    source = _source(loaded)
    assert source.count("def _module_part(") == 1
    assert source.count("def _function_name(") == 1
    assert source.count("importlib.import_module(") == 2


def test_open_vi_reference_with_no_path_is_not_converted(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, [_OPEN], [])
    source = _source(loaded)
    assert (
        "raise NotImplementedError('Open VI Reference is not converted yet\\n"
        "gap kind: placeholder_primitive\\n"
    ) in source
    assert "primitive 8010 to" not in source
    assert "give primitive 8010 a python_code" in source


def test_call_with_no_reference_wired_says_so(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, [_CALL], [], [_X])
    assert _failure(loaded) == (
        "Call By Reference: the VI reference input is not wired and the node "
        "records no VI of its own."
    )


def test_call_with_a_reference_passed_on_by_another_call_is_a_gap(
    tmp_path: Path,
) -> None:
    second = d.call_through_reference(50)
    loaded = d.load(
        tmp_path,
        [_STATIC, _CALL, second],
        [d.wire(100, 11, 31), d.wire(101, 33, 51)],
        [_X],
    )
    assert _failure(loaded) == _NO_PANE


def test_name_and_path_of_a_vi_reference_are_read_from_the_function(
    tmp_path: Path,
) -> None:
    name = d.vi_name_property(60, 500, "Name")
    path = d.vi_name_property(80, 501, "Path")
    loaded = d.load(
        tmp_path,
        [_STATIC, name, path, _CALL],
        [
            d.wire(100, 11, 61),
            d.wire(101, 11, 81),
            d.wire(102, 11, 31),
            d.wire(103, 69, 41),
        ],
    )
    source = _source(loaded)
    assert "callee_Name" not in source
    assert "callee.__name__" in source
    assert "inspect.getfile(callee)" in source
    assert "import inspect" in source


def test_close_reference_of_a_vi_reference_generates_nothing(tmp_path: Path) -> None:
    close = d.close_reference(70)
    loaded = d.load(
        tmp_path,
        [_OPEN, _CALL, close],
        [d.wire(100, 21, 31), d.wire(101, 21, 71)],
        [_PATH, _X],
    )
    assert describe_vi(loaded.graph, loaded.caller).endswith(
        "## Operations\nOpen VI Reference [prim 8010]\n"
        "Call By Reference\nClose Reference [prim 8011]"
    )
    assert _source(loaded).endswith(
        "from .callee import callee\n\ndef refs():\n    callee(x=5)"
    )


def test_close_reference_of_an_unwired_reference_is_not_a_vi_reference(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, [d.close_reference(70)], [])
    assert not loaded.graph.holds_vi_reference(f"{loaded.caller}::71")


# --- the generated package runs ----------------------------------------------


def _package(
    tmp_path: Path, source: str, callee: str, module: str = "callee"
) -> Iterator[object]:
    package = tmp_path / "generated_refs"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "errors.py").write_text(ERRORS_MODULE_SOURCE)
    (package / f"{module}.py").write_text(callee)
    (package / "refs.py").write_text(source)
    sys.path.insert(0, str(tmp_path))
    try:
        yield importlib.import_module("generated_refs.refs")
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m.startswith("generated_refs")]:
            del sys.modules[name]


_CALLEE = (
    "from typing import NamedTuple\n"
    "calls = []\n"
    "class CalleeResult(NamedTuple):\n"
    "    y: int\n"
    "def callee(x=None):\n"
    "    calls.append((x, threading_name()))\n"
    "    return CalleeResult(y=0)\n"
    "def threading_name():\n"
    "    import threading\n"
    "    return threading.current_thread().name\n"
)


@pytest.mark.parametrize(
    ("diagram", "constants"),
    [
        (_STATIC_CALL, [_X]),
        (_OPEN_CALL, [_PATH, _X]),
        (_OPEN_CALL, [_NAME, _X]),
    ],
)
def test_a_call_through_a_reference_runs(
    tmp_path: Path, diagram: tuple[list[str], list[str]], constants: list[d.Constant]
) -> None:
    loaded = d.load(tmp_path, *diagram, constants)
    for refs in _package(tmp_path, _source(loaded), _CALLEE):
        refs.refs()  # type: ignore[attr-defined]
        callee = sys.modules["generated_refs.callee"]
        assert [x for x, _ in callee.calls] == [5]


def test_a_started_call_runs_on_another_thread(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, *_STATIC_NAME_OPEN_START, [_X])
    for refs in _package(tmp_path, _source(loaded), _CALLEE):
        refs.refs()  # type: ignore[attr-defined]
        refs._async_call_pool.shutdown(wait=True)  # type: ignore[attr-defined]
        callee = sys.modules["generated_refs.callee"]
        ((x, thread_name),) = callee.calls
        assert x == 5
        assert thread_name != threading.current_thread().name


def test_name_and_path_run_as_the_function_s_own_properties(tmp_path: Path) -> None:
    name = d.vi_name_property(60, 500, "Name")
    path = d.vi_name_property(80, 501, "Path")
    loaded = d.load(
        tmp_path,
        [_STATIC, name, path, _CALL],
        [
            d.wire(100, 11, 61),
            d.wire(101, 11, 81),
            d.wire(102, 11, 31),
            d.wire(103, 69, 41),
        ],
    )
    for refs in _package(tmp_path, _source(loaded), _CALLEE):
        refs.refs()  # type: ignore[attr-defined]
        callee = sys.modules["generated_refs.callee"]
        assert callee.calls[0][0] == "callee"


def test_a_reference_that_is_a_value_is_called_with_the_panes_names(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, [_CALL], [], [_X])
    reference = d.typed_vi_reference()
    d.type_terminal(loaded, 31, reference)
    d.add_control(loaded, "vi_ref", reference, 31)
    assert _source(loaded).endswith("def refs(vi_ref: Any):\n    vi_ref(x=5)")


def test_a_reference_whose_pane_has_a_dynamic_dispatch_terminal_is_a_gap(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, [_CALL], [], [_X])
    reference = d.typed_vi_reference()
    assert reference.element_type is not None and reference.element_type.pane
    first, *rest = reference.element_type.pane
    reference.element_type.pane = (
        dataclasses.replace(first, dynamic_dispatch=True),
        *rest,
    )
    d.type_terminal(loaded, 31, reference)
    d.add_control(loaded, "vi_ref", reference, 31)
    reason = _failure(loaded)
    assert "dynamic dispatch terminal" in reason
    assert "Call By Reference does not support dynamic member VIs" in reason


def test_a_started_call_of_a_reference_value_is_submitted(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, [_START], [], [_X])
    reference = d.typed_vi_reference()
    d.type_terminal(loaded, 31, reference)
    d.add_control(loaded, "vi_ref", reference, 31)
    assert _source(loaded).endswith(
        "def refs(vi_ref: Any):\n    _async_call_pool.submit(vi_ref, x=5)"
    )


def test_a_reference_value_call_runs(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, [_CALL], [], [_X])
    reference = d.typed_vi_reference()
    d.type_terminal(loaded, 31, reference)
    d.add_control(loaded, "vi_ref", reference, 31)
    for refs in _package(tmp_path, _source(loaded), _CALLEE):
        callee = importlib.import_module("generated_refs.callee")
        refs.refs(callee.callee)  # type: ignore[attr-defined]
        assert [x for x, _ in callee.calls] == [5]


def test_a_call_by_reference_with_its_vi_chosen_in_the_setup_is_a_direct_call(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, [_CALL], [], [_X])
    call = next(
        n
        for n in loaded.graph.iter_nodes(loaded.caller)
        if n.node_type == "callByRefNode"
    )
    call.callee_key = loaded.callee
    call.qualified_name = "Callee.vi"
    assert _source(loaded).endswith(
        "from .callee import callee\n\ndef refs():\n    callee(x=5)"
    )
    for refs in _package(tmp_path, _source(loaded), _CALLEE):
        refs.refs()  # type: ignore[attr-defined]
        assert [x for x, _ in sys.modules["generated_refs.callee"].calls] == [5]


def test_this_vi_is_the_function_being_generated(tmp_path: Path) -> None:
    """ "This VI" wired into a Property Node reading Name: the reference is the
    generated function, so Name is ``refs.__name__``; the value runs into a call."""
    name = d.vi_name_property(60, 500, "Name")
    loaded = d.load(
        tmp_path,
        [d.this_vi(80), _STATIC, name, _CALL],
        [d.wire(100, 81, 61), d.wire(101, 11, 31), d.wire(102, 69, 41)],
    )
    d.type_terminal(loaded, 81, d.this_vi_type())
    source = _source(loaded)
    assert "refs.__name__" in source
    assert "NotImplementedError" not in source
    for refs in _package(tmp_path, source, _CALLEE):
        refs.refs()  # type: ignore[attr-defined]
        callee = sys.modules["generated_refs.callee"]
        assert callee.calls[0][0] == "refs"


# --- the node's own error in and error out -----------------------------------
#
# A Call By Reference node has its own error in (-3) and error out (-4) beside the
# called VI's error in and error out (connector pane slots 2 and 3). Each diagram
# wires all four: the caller's control "error in" to the node's error in, its
# control "callee error" to the called VI's error in, and the node's error out,
# the called VI's error out and the output y to indicators.

# The callee returns y = x + 1 and an error of its own, code 7.
_CALLEE_WITH_ERRORS = (
    "from types import SimpleNamespace\n"
    "from .errors import LabVIEWError\n"
    "calls = []\n"
    "def callee(x=None, error_in=None):\n"
    "    calls.append((x, error_in))\n"
    "    return SimpleNamespace(\n"
    "        y=x + 1, error_out=LabVIEWError(code=7, source='callee')\n"
    "    )\n"
)

_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _errors_wired(loaded: d.Loaded, *, vi_outputs: bool = True) -> None:
    """Wire the node's and the called VI's error terminals; ``vi_outputs`` wires
    the called VI's outputs too (Start Asynchronous Call has none to read)."""
    for uid in (35, 37, 45, 47):
        d.type_terminal(loaded, uid, d.ERROR)
    d.type_terminal(loaded, 43, _I32)
    d.add_control(loaded, "error in", d.ERROR, 35, uid="node error")
    d.add_control(loaded, "callee error", d.ERROR, 45, uid="callee error")
    d.add_indicator(loaded, "error out", d.ERROR, 37, 2)
    if vi_outputs:
        d.add_indicator(loaded, "y", _I32, 43, 1)
        d.add_indicator(loaded, "callee error out", d.ERROR, 47, 3)


def _static_with_errors(tmp_path: Path, *, style: str = "00") -> d.Loaded:
    call = d.call_through_reference(
        30, "Call By Reference", save_style=style, callee_errors=True
    )
    loaded = d.load(
        tmp_path,
        [_STATIC, call],
        [d.wire(100, 11, 31)],
        [_X],
        callee_terminals=d.CALLEE_WITH_ERRORS,
    )
    _errors_wired(loaded, vi_outputs=style == "00")
    return loaded


def _value_with_errors(tmp_path: Path) -> d.Loaded:
    call = d.call_through_reference(30, "Call By Reference", callee_errors=True)
    loaded = d.load(tmp_path, [call], [], [_X], callee_terminals=d.CALLEE_WITH_ERRORS)
    reference = d.typed_vi_reference(errors=True)
    d.type_terminal(loaded, 31, reference)
    d.add_control(loaded, "vi_ref", reference, 31)
    _errors_wired(loaded)
    return loaded


def test_an_error_on_the_nodes_error_in_skips_the_call(tmp_path: Path) -> None:
    for refs in _package(
        tmp_path, _source(_static_with_errors(tmp_path)), _CALLEE_WITH_ERRORS
    ):
        callee = sys.modules["generated_refs.callee"]
        errors = sys.modules["generated_refs.errors"]
        incoming = errors.LabVIEWError(code=5, source="before")
        result = refs.refs(error_in=incoming, callee_error=None)  # type: ignore[attr-defined]
        assert callee.calls == []
        assert result.error_out is incoming
        assert result.callee_error_out is None
        assert result.y == 0


def test_with_no_error_in_the_call_is_made_and_reports_the_vis_own_error(
    tmp_path: Path,
) -> None:
    for refs in _package(
        tmp_path, _source(_static_with_errors(tmp_path)), _CALLEE_WITH_ERRORS
    ):
        callee = sys.modules["generated_refs.callee"]
        errors = sys.modules["generated_refs.errors"]
        given = errors.LabVIEWError(code=3, source="given")
        result = refs.refs(error_in=None, callee_error=given)  # type: ignore[attr-defined]
        assert callee.calls == [(5, given)]
        assert result.y == 6
        assert result.callee_error_out.code == 7
        assert result.error_out is None
        # A warning on the node's error in does not skip the call; it passes on.
        warning = errors.LabVIEWError(code=9, source="w", status=False)
        result = refs.refs(error_in=warning, callee_error=None)  # type: ignore[attr-defined]
        assert len(callee.calls) == 2
        assert result.error_out is warning


def test_a_reference_value_that_is_not_a_refnum_is_error_1026(tmp_path: Path) -> None:
    for refs in _package(
        tmp_path, _source(_value_with_errors(tmp_path)), _CALLEE_WITH_ERRORS
    ):
        callee = importlib.import_module("generated_refs.callee")
        errors = sys.modules["generated_refs.errors"]
        result = refs.refs(vi_ref=None, error_in=None, callee_error=None)  # type: ignore[attr-defined]
        assert callee.calls == []
        assert result.error_out.status is True
        assert result.error_out.code == 1026
        assert result.error_out.source == "Call By Reference"
        assert result.error_out.message == "VI Reference is invalid."
        assert result.callee_error_out is None
        assert result.y == 0
        # An error that came in wins over the invalid reference.
        incoming = errors.LabVIEWError(code=5, source="before")
        result = refs.refs(vi_ref=None, error_in=incoming, callee_error=None)  # type: ignore[attr-defined]
        assert result.error_out is incoming
        # A reference that holds the VI is called.
        result = refs.refs(vi_ref=callee.callee, error_in=None, callee_error=None)  # type: ignore[attr-defined]
        assert callee.calls == [(5, None)]
        assert result.error_out is None
        assert result.callee_error_out.code == 7
        assert result.y == 6


def test_a_reference_value_with_an_incoming_error_is_not_called(
    tmp_path: Path,
) -> None:
    for refs in _package(
        tmp_path, _source(_value_with_errors(tmp_path)), _CALLEE_WITH_ERRORS
    ):
        callee = importlib.import_module("generated_refs.callee")
        errors = sys.modules["generated_refs.errors"]
        incoming = errors.LabVIEWError(code=5, source="before")
        result = refs.refs(  # type: ignore[attr-defined]
            vi_ref=callee.callee, error_in=incoming, callee_error=None
        )
        assert callee.calls == []
        assert result.error_out is incoming
        assert result.callee_error_out is None


def test_start_asynchronous_call_with_an_incoming_error_starts_nothing(
    tmp_path: Path,
) -> None:
    loaded = _static_with_errors(tmp_path, style="01")
    for refs in _package(tmp_path, _source(loaded), _CALLEE_WITH_ERRORS):
        callee = sys.modules["generated_refs.callee"]
        errors = sys.modules["generated_refs.errors"]
        incoming = errors.LabVIEWError(code=5, source="before")
        result = refs.refs(error_in=incoming, callee_error=None)  # type: ignore[attr-defined]
        refs._async_call_pool.shutdown(wait=True)  # type: ignore[attr-defined]
        assert callee.calls == []
        assert result.error_out is incoming


def test_start_asynchronous_call_with_no_error_in_starts_the_call(
    tmp_path: Path,
) -> None:
    loaded = _static_with_errors(tmp_path, style="01")
    for refs in _package(tmp_path, _source(loaded), _CALLEE_WITH_ERRORS):
        callee = sys.modules["generated_refs.callee"]
        result = refs.refs(error_in=None, callee_error=None)  # type: ignore[attr-defined]
        refs._async_call_pool.shutdown(wait=True)  # type: ignore[attr-defined]
        assert callee.calls == [(5, None)]
        assert result.error_out is None


# The callee's connector pane has no error terminals: y = x + 1.
_CALLEE_NO_ERRORS = (
    "from types import SimpleNamespace\n"
    "calls = []\n"
    "def callee(x=None):\n"
    "    calls.append(x)\n"
    "    return SimpleNamespace(y=x + 1)\n"
)


def test_a_called_vi_with_no_error_terminals_is_skipped_on_the_nodes_error_in(
    tmp_path: Path,
) -> None:
    """The called VI's connector pane has no error in or error out; the node's
    own error in still skips the call and its error out passes the error on.
    The call names only the pane's terminals."""
    call = d.call_through_reference(30, "Call By Reference")
    loaded = d.load(tmp_path, [_STATIC, call], [d.wire(100, 11, 31)], [_X])
    for uid in (35, 37):
        d.type_terminal(loaded, uid, d.ERROR)
    d.type_terminal(loaded, 43, _I32)
    d.add_control(loaded, "error in", d.ERROR, 35, uid="node error")
    d.add_indicator(loaded, "error out", d.ERROR, 37, 2)
    d.add_indicator(loaded, "y", _I32, 43, 1)
    source = _source(loaded)
    assert "callee(x=5)" in source
    for refs in _package(tmp_path, source, _CALLEE_NO_ERRORS):
        callee = sys.modules["generated_refs.callee"]
        errors = importlib.import_module("generated_refs.errors")
        result = refs.refs(error_in=None)  # type: ignore[attr-defined]
        assert callee.calls == [5]
        assert (result.y, result.error_out) == (6, None)
        incoming = errors.LabVIEWError(code=5, source="before")
        result = refs.refs(error_in=incoming)  # type: ignore[attr-defined]
        assert callee.calls == [5]
        assert (result.y, result.error_out) == (0, incoming)
