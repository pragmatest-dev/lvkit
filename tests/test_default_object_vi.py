"""An Application Property Node with its reference unwired, built into a whole
VI: it generates the plain Python value and runs."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, VIContext, VIMetadata, VINode, Wire
from lvkit.models import (
    FPTerminal,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
)
from tests.helpers import build_graph, exec_generated


def _vi(
    class_name: str, property_name: str, code: int, extra_nodes=(), owners=()
):
    """A VI ``User.vi`` whose only output is ``property_name`` (property code
    ``code``) read from the
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
            PropertyDef(segments=[PropertySegment(name=property_name, code=code)])
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
    graph = InMemoryVIGraph()
    if owners:
        graph._vi_metadata["User.vi"] = VIMetadata(owning_libraries=list(owners))
    graph = build_graph(context, context.name, [prop, *extra_nodes], graph)
    return build_module(context, context.name, graph=graph)


def _run(source: str, tmp_path: Path):
    path = tmp_path / "user.py"
    path.write_text(source, encoding="utf-8")
    namespace: dict = {"__file__": str(path)}
    exec_generated(source, namespace, str(path))  # noqa: S102
    return str(namespace["user"]().user)


def test_unwired_user_name_is_written_in_and_runs(tmp_path: Path) -> None:
    source = _vi("App", "App.UserName", 1)
    assert "lvkit" not in source
    assert "import getpass" in source
    assert "appusername = getpass.getuser()" in source
    assert _run(source, tmp_path) == getpass.getuser()


def test_this_vis_name_is_its_qualified_name(tmp_path: Path) -> None:
    source = _vi("VI", "Name", 500)
    assert "name = user.vi_name\n" in source
    assert _run(source, tmp_path) == "User.vi"


def test_this_vis_path_is_its_file(tmp_path: Path) -> None:
    source = _vi("VI", "Path", 501)
    assert "path = Path(__file__)\n" in source
    assert _run(source, tmp_path) == str(tmp_path / "user.py")


def test_the_application_directory_is_the_running_programs_folder(
    tmp_path: Path,
) -> None:
    for name in ("App.Dir", "Application:Directory Path"):
        source = _vi("App", name, 25)
        assert "Path(sys.argv[0]).resolve().parent" in source
        assert "import sys" in source
        assert _run(source, tmp_path) == str(Path(sys.argv[0]).resolve().parent)


def test_callees_names_list_the_called_functions(tmp_path: Path) -> None:
    none = _vi("VI", "Callees' Names", 545)
    assert "callees_names = [callee.vi_name for callee in ()]\n" in none
    assert _run(none, tmp_path) == "[]"
    helper = VINode(
        id="helper",
        vi_path="User.vi",
        name="Helper.vi",
        callee_key="/lib/Helper.vi",
        terminals=[],
    )
    source = _vi("VI", "Callees' Names", 545, [helper])
    assert "from .helper import helper\n" in source
    assert "[callee.vi_name for callee in (helper,)]" in source


def test_a_vi_outside_a_packed_library_is_not_in_one(tmp_path: Path) -> None:
    for owners in ((), ("Lib.lvlib",)):
        source = _vi("VI", "IsInPackedLibrary", 681, owners=owners)
        assert "isinpackedlibrary = False\n" in source
        assert _run(source, tmp_path) == "False"


def test_a_vi_recorded_inside_a_lvlibp_is_in_a_packed_library(
    tmp_path: Path,
) -> None:
    source = _vi("VI", "IsInPackedLibrary", 681, owners=("Support.lvlibp",))
    assert "isinpackedlibrary = True\n" in source
    assert "lvkit" not in source
    assert _run(source, tmp_path) == "True"


def test_properties_with_no_python_meaning_raise_where_they_run() -> None:
    # Each property's code as the corpus stores it.
    for class_name, name, code in (
        ("App", "App.Version", 2),
        ("App", "App.Kind", 3),
        ("App", "App.LVTarget", 131),
    ):
        source = _vi(class_name, name, code)
        assert (
            f"raise NotImplementedError(\"Property '{name}' of the default "
            f"{class_name} object has no Python equivalent\\ngap kind: "
            "unsupported_node\\nVI: User.vi\\nnode: Property Node\")"
        ) in source
