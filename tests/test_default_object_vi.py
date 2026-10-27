"""An Application Property Node with its reference unwired, built into a whole
VI: it generates the plain Python value and runs."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors import CodeGenError
from lvkit.graph.models import PrimitiveNode, VIContext, VINode, Wire
from lvkit.models import (
    FPTerminal,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
)
from tests.helpers import build_graph


def _vi(class_name: str, property_name: str, extra_nodes=()):
    """A VI ``User.vi`` whose only output is ``property_name`` read from the
    unwired default object of ``class_name``; returns the generated source."""
    text = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    ref = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
    user = FPTerminal(
        id="fp:user",
        index=0,
        direction="output",
        name="user",
        is_public=True,
        is_indicator=True,
        lv_type=text,
    )
    prop = PrimitiveNode(
        id="prop",
        vi_path="User.vi",
        name="Property Node",
        node_type="propNode",
        object_name=class_name,
        terminals=[
            Terminal(id="prop:ref", index=0, direction="input", lv_type=ref),
            Terminal(id="prop:value", index=4, direction="output", lv_type=text),
        ],
        properties=[
            PropertyDef(segments=[PropertySegment(name=property_name, code=1)])
        ],
        property_value_terminal_ids=["prop:value"],
    )
    context = VIContext(
        name="User.vi",
        inputs=[],
        outputs=[user],
        data_flow=[
            Wire.from_terminals(from_terminal_id="prop:value", to_terminal_id=user.id)
        ],
    )
    graph = build_graph(context, context.name, [prop, *extra_nodes])
    return build_module(context, context.name, graph=graph)


def _run(source: str, tmp_path: Path):
    path = tmp_path / "user.py"
    path.write_text(source)
    namespace: dict = {"__file__": str(path)}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102
    return str(namespace["user"]().user)


def test_unwired_user_name_is_written_in_and_runs(tmp_path: Path) -> None:
    source = _vi("App", "App.UserName")
    assert "lvkit" not in source
    assert "import getpass" in source
    assert "appusername = getpass.getuser()" in source
    assert _run(source, tmp_path) == getpass.getuser()


def test_this_vis_name_is_its_function_name(tmp_path: Path) -> None:
    source = _vi("VI", "Name")
    assert "name = user.__name__\n" in source
    assert _run(source, tmp_path) == "user"


def test_this_vis_path_is_its_file(tmp_path: Path) -> None:
    source = _vi("VI", "Path")
    assert "path = Path(__file__)\n" in source
    assert _run(source, tmp_path) == str(tmp_path / "user.py")


def test_the_application_directory_is_the_running_programs_folder(
    tmp_path: Path,
) -> None:
    for name in ("App.Dir", "Application:Directory Path"):
        source = _vi("App", name)
        assert "Path(sys.argv[0]).resolve().parent" in source
        assert "import sys" in source
        assert _run(source, tmp_path) == str(Path(sys.argv[0]).resolve().parent)


def test_callees_names_list_the_called_functions(tmp_path: Path) -> None:
    none = _vi("VI", "Callees' Names")
    assert "callees_names = [callee.__name__ for callee in ()]\n" in none
    assert _run(none, tmp_path) == "[]"
    helper = VINode(
        id="helper",
        vi_path="User.vi",
        name="Helper.vi",
        callee_key="/lib/Helper.vi",
        terminals=[],
    )
    source = _vi("VI", "Callees' Names", [helper])
    assert "from .helper import helper\n" in source
    assert "[callee.__name__ for callee in (helper,)]" in source


def test_properties_with_no_python_meaning_stop_generation() -> None:
    for class_name, name in (
        ("App", "App.Version"),
        ("App", "App.Kind"),
        ("App", "App.LVTarget"),
        ("App", "Disp.AllMonitors"),
        ("VI", "IsInPackedLibrary"),
    ):
        with pytest.raises(CodeGenError, match=f"'{name}' of the default"):
            _vi(class_name, name)
