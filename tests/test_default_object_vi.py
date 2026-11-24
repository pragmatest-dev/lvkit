"""An Application Property Node with its reference unwired, built into a whole
VI: it generates the plain Python value and runs."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import (
    PrimitiveNode,
    Reentrancy,
    VIContext,
    VIMetadata,
    VINode,
    VIProperties,
    Wire,
)
from lvkit.models import (
    FPTerminal,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    ServerClass,
    Terminal,
)
from tests.helpers import build_graph, exec_generated, generated_panels


def _vi(
    class_name: str,
    property_name: str,
    code: int,
    extra_nodes=(),
    owners=(),
    lv_version: str | None = None,
    reentrancy: Reentrancy | None = None,
    wired: bool = False,
):
    """A VI ``User.vi`` whose only output is ``property_name`` (property code
    ``code``) read from the unwired default object of ``class_name`` (with
    ``wired``, from an Application reference its input ``app`` carries); returns the
    generated source."""
    text = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    ref = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        server_class=ServerClass.APPLICATION if wired else None,
    )
    app = FPTerminal(
        id="fp:app",
        index=1,
        direction="input",
        name="app",
        is_public=True,
        is_indicator=False,
        lv_type=ref,
    )
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
        inputs=[app] if wired else [],
        outputs=[user],
        data_flow=[
            Wire.from_terminals(from_terminal_id="prop:value", to_terminal_id=user.id),
            *(
                [Wire.from_terminals(app.id, "prop:ref")]
                if wired
                else []
            ),
        ],
    )
    graph = InMemoryVIGraph()
    if owners:
        graph._vi_metadata["User.vi"] = VIMetadata(owning_libraries=list(owners))
    if lv_version is not None or reentrancy is not None:
        properties = VIProperties(lv_version=lv_version)
        if reentrancy is not None:
            properties.execution.reentrancy = reentrancy
        graph._vi_properties["User.vi"] = properties
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
        ("App", "App.VerLVFmt", 55),
        ("App", "App.PrefsFilePth", 57),
        ("App", "App.LVTarget", 131),
    ):
        source = _vi(class_name, name, code)
        assert (
            f"raise NotImplementedError(\"Property '{name}' of the default "
            f"{class_name} object has no Python equivalent\\ngap kind: "
            "unsupported_node\\nVI: User.vi\\nnode: Property Node\")"
        ) in source


def test_the_application_version_is_the_labview_version_the_vi_was_saved_in(
    tmp_path: Path,
) -> None:
    for name in ("App.Version", "Application:Version Number"):
        source = _vi("App", name, 2, lv_version="20.0.1")
        assert "= '20.0.1'\n" in source
        assert "lvkit" not in source
        assert _run(source, tmp_path) == "20.0.1"


def test_the_version_year_is_the_release_year_of_the_saved_labview(
    tmp_path: Path,
) -> None:
    source = _vi("App", "App.VersionYearNumberOnly", 265, lv_version="20.0.1")
    assert "= 2020\n" in source
    assert _run(source, tmp_path) == "2020"
    assert _run(
        _vi("App", "App.VersionYearNumberOnly", 265, lv_version="9.0.0"), tmp_path
    ) == "2009"


def test_a_version_with_no_year_or_no_version_is_a_gap() -> None:
    old = _vi("App", "App.VersionYearNumberOnly", 265, lv_version="8.6.1")
    assert "raise NotImplementedError" in old
    assert "whose version number is not a year" in old
    unknown = _vi("App", "App.Version", 2)
    assert "does not record the LabVIEW version it was saved in" in unknown
    assert "raise NotImplementedError" in unknown


def test_the_application_kind_is_run_time_system(tmp_path: Path) -> None:
    source = _vi("App", "App.Kind", 3)
    assert "= 2\n" in source
    assert _run(source, tmp_path) == "2"


def test_the_application_is_one_object_with_no_context_name(tmp_path: Path) -> None:
    source = _vi("App", "App.ContextName", 122)
    assert "from .panels import application" in source
    assert "lvkit" not in source
    assert _run(source, tmp_path) == ""
    contexts = _vi("App", "App.AllContexts", 104)
    assert "application.all_contexts" in contexts
    assert _run(contexts, tmp_path).startswith("[<")
    default = _vi("App", "App.DefaultApplication", 144)
    assert "= application\n" in default
    name = _vi("App", "Application:Name", 0)
    assert "application.name" in name
    assert _run(name, tmp_path) == Path(sys.argv[0] or sys.executable).name


def test_a_row_on_a_wired_application_reference_reads_the_reference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "user.py"
    for name, code, expected in (
        ("App.ContextName", 122, ""),
        ("App.Version", 2, "20.0.1"),
    ):
        source = _vi("App", name, code, lv_version="20.0.1", wired=True)
        assert "lvkit" not in source
        path.write_text(source, encoding="utf-8")
        namespace: dict = {"__file__": str(path)}
        exec_generated(source, namespace, str(path))  # noqa: S102
        application = generated_panels().application
        assert namespace["user"](app=application).user == expected
    contexts = _vi("App", "App.AllContexts", 104, wired=True)
    assert "app.all_contexts" in contexts
    unknown = _vi("App", "App.LVTarget", 131, wired=True)
    assert "has no Python equivalent" in unknown


def test_owning_application_of_the_default_vi_is_the_application(
    tmp_path: Path,
) -> None:
    source = _vi("VI", "OwningApp", 623)
    assert "= application\n" in source
    assert _run(source, tmp_path).startswith("<")


def test_all_vis_in_memory_lists_the_imported_vi_functions(tmp_path: Path) -> None:
    source = _vi("App", "Application:All VIs In Memory", 28)
    assert "from .panels import loaded_vis" in source
    path = tmp_path / "user.py"
    path.write_text(source, encoding="utf-8")
    namespace: dict = {"__file__": str(path)}
    exec_generated(source, namespace, str(path))  # noqa: S102
    listed = namespace["user"]().user
    assert namespace["user"] in listed
    assert [function.vi_name for function in listed] == sorted(
        function.vi_name for function in listed
    )
    assert len(listed) == len({id(function) for function in listed})


def test_the_reentrancy_type_is_a_file_fact_of_a_reentrant_vi(
    tmp_path: Path,
) -> None:
    for reentrancy, number in (
        (Reentrancy.SHARED_CLONE, "0"),
        (Reentrancy.PREALLOCATED_CLONE, "1"),
    ):
        source = _vi("VI", "Exec.ReentrancyType", 648, reentrancy=reentrancy)
        assert f"user.reentrancy_type = {number}\n" in source
        assert _run(source, tmp_path) == number


def test_the_reentrancy_type_of_a_vi_that_is_not_reentrant_raises(
    tmp_path: Path,
) -> None:
    source = _vi("VI", "Exec.ReentrancyType", 648, reentrancy=Reentrancy.NON_REENTRANT)
    assert "user.reentrancy_type = None\n" in source
    with pytest.raises(NotImplementedError, match="User.vi is not reentrant"):
        _run(source, tmp_path)
