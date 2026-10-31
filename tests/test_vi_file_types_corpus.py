"""Real ``.vim`` (malleable VI), ``.vit`` (VI template) and ``.ctl`` files, and
the callers of a malleable VI, from the sample corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph, netlist_to_dict, render_lvnet
from lvkit.graph.node_kinds import EdgeRel
from lvkit.load_mode import LoadMode
from lvkit.pipeline import generate_python
from lvkit.render import render_vi_file
from tests.corpus_graphs import load_corpus_vi

pytestmark = pytest.mark.needs_samples

_SAMPLES = Path(".lvkit/cache/samples")
_RESERVATION = (
    _SAMPLES
    / "measurement-plugin-labview/Source/Runtime/Clients"
    / "Session Management V1/Session Reservation"
)
_VIM = _RESERVATION / "Create Annotations.vim"
_VIT = _SAMPLES / "JKI-VI-Tester/source/Templates/TestCase/testExample.vit"
_CALLER = _RESERVATION / "Create Reservation Annotations.vi"
_MANY_INSTANCES_CALLER = (
    _SAMPLES
    / "measurement-plugin-labview/Source/Tests/Tests.Runtime/Measurement Server"
    / "Get Connections"
    / "test_OneSessTwoPinOneInstrTwoSiteReservation_"
    "CallNiDigitalGetConnectionsNPinsMConnections_ReturnsThreeConnections.vi"
)
_GENERATED_VIM = (
    _SAMPLES
    / "measurement-plugin-labview/Source/Tests/Tests.Runtime/Measurement Server"
    / "Helpers/Assertion Helpers/Assert Equal.vim"
)
_GENERATED_VIT = _SAMPLES / "JKI-VI-Tester/source/Tests/MyFirstTest/testTemplate.vit"
_ENUM_CTL = _SAMPLES / "lv-flex-channel-examples/DAQmx AO/Action.ctl"
_CLUSTER_CTL = _SAMPLES / "lv-flex-channel-examples/DAQmx AO/Params.ctl"

_VIM_SIGNATURE = (
    "  create_annotations(annotation keys: [String], annotation typedef: "
    "cluster{key, value}, error in: Error) -> annotations array: "
    "[cluster{key, value}], error out: Error"
)
_VIT_SIGNATURE = (
    "  testexample(TestCase in: TemplateTestCase.lvclass, error in (no error): "
    "Error) -> TestCase out: TemplateTestCase.lvclass, error out: Error"
)


def _describe(path: Path) -> str:
    graph, key = load_corpus_vi(path.resolve())
    return describe_vi(graph, key)


@pytest.mark.parametrize(
    ("path", "signature", "lvnet_header"),
    [
        (
            _VIM,
            _VIM_SIGNATURE,
            "vi NI Session Management V1 Client.lvlib:Session Reservation.lvclass:"
            "Create Annotations.vim :",
        ),
        (_VIT, _VIT_SIGNATURE, "vi TemplateTestCase.lvclass:testExample.vit :"),
    ],
)
def test_vim_and_vit_read_in_every_view(
    path: Path, signature: str, lvnet_header: str
) -> None:
    graph, key = load_corpus_vi(path.resolve())

    assert describe_vi(graph, key).splitlines()[2] == signature

    module = build_netlist_from_graph(graph, key)
    assert render_lvnet(module, display_name=graph.vi_display_name(key)).startswith(
        lvnet_header
    )
    assert Path(netlist_to_dict(module)["vi"]).name == path.name

    svg = render_vi_file(path)
    assert svg is not None
    assert "<svg" in svg


@pytest.mark.parametrize(
    ("path", "mode", "module_file"),
    [
        (_GENERATED_VIM, LoadMode.FULL, "assert_equal.py"),
        (_GENERATED_VIT, LoadMode.NONE, "testtemplate.py"),
    ],
)
def test_vim_and_vit_generate_python(
    path: Path, mode: LoadMode, module_file: str, tmp_path: Path
) -> None:
    summary = generate_python(path, tmp_path, mode=mode)

    assert summary["error"] == 0
    [written] = tmp_path.rglob(module_file)
    function = module_file.removesuffix(".py")
    assert f"def {function}(" in written.read_text(encoding="utf-8")


def test_caller_of_a_malleable_vi_generates_a_call_to_its_module(
    tmp_path: Path,
) -> None:
    generate_python(_CALLER, tmp_path)

    [caller] = tmp_path.rglob("create_reservation_annotations.py")
    [callee] = tmp_path.rglob("create_annotations.py")
    lines = caller.read_text(encoding="utf-8").splitlines()
    [call_import] = [ln for ln in lines if " import create_annotations" in ln]
    assert call_import.startswith("from .")
    assert call_import.endswith(
        ".clients.session_management_v1.session_reservation.create_annotations"
        " import create_annotations"
    )
    assert (
        "    create_annotations_result = create_annotations("
        "annotation_typedef=SimpleNamespace(key='', value=''), "
        "annotation_keys=session_reservation_keys_result."
        "session_reservation_keys)"
    ) in lines
    assert "def create_annotations(" in callee.read_text(encoding="utf-8")


def test_caller_of_a_malleable_vi_shows_the_vi_with_its_terminal_names() -> None:
    text = _describe(_CALLER)

    # The call is to Create Annotations.vim, named by its own LIBN chain, with
    # the terminal names of that VI.
    assert (
        "  NI Session Management V1 Client.lvlib:Session Reservation.lvclass:"
        "Create Annotations.vim: (annotation keys: [String], annotation "
        "typedef: cluster{key, value}, error in: Error) -> (annotations array: "
        "[cluster{key, value}], error out: Error)"
    ) in text.splitlines()
    assert (
        "    NI Session Management V1 Client.lvlib:Session Reservation.lvclass:"
        "Create Annotations.vim(annotation typedef, error in, annotation keys) "
        "-> error out, annotations array"
    ) in text.splitlines()
    # The embedded GUID-named copy never surfaces.
    assert "Instance:" not in text

    # A neighbouring ordinary subVI call is unchanged.
    assert (
        "  NI Session Management V1 Client.lvlib:Session Reservation.lvclass:"
        "Session Reservation Keys.vi: () -> (session reservation keys: [String])"
    ) in text.splitlines()
    assert (
        "    NI Session Management V1 Client.lvlib:Session Reservation.lvclass:"
        "Session Reservation Keys.vi() -> session reservation keys"
    ) in text.splitlines()


def test_caller_of_a_malleable_vi_lists_the_vi_in_lvnet_and_json() -> None:
    graph, key = load_corpus_vi(_CALLER.resolve())
    module = build_netlist_from_graph(graph, key)

    lvnet = render_lvnet(module, display_name=graph.vi_display_name(key))
    qualified = (
        "NI Session Management V1 Client.lvlib:Session Reservation.lvclass:"
        "Create Annotations.vim"
    )
    assert f"    subVI   {qualified} ; ./" in lvnet
    assert f"subVI Create_Annotations_43 : {qualified}" in lvnet
    assert "Instance" not in lvnet
    assert "Instance" not in str(netlist_to_dict(module))


def test_instances_of_one_malleable_vi_in_a_caller_share_its_file() -> None:
    text = _describe(_MANY_INSTANCES_CALLER)
    dependencies = text.split("## Dependencies\n")[1].split("\n\n")[0].splitlines()

    assert [line.strip().split(": (")[0] for line in dependencies] == [
        "MeasurementServerTests.lvlib:Assert Connections.vim",
        "MeasurementServerTests.lvlib:Create Connection Cluster.vim",
        "MeasurementServerTests.lvlib:"
        "One Session Two Pins One Instrument Two Sites - Digital.vi",
        "NI Session Management niDigital.lvlib:Get Connections.vi",
    ]
    assert "Instance:" not in text


def test_caller_loads_the_malleable_vi_as_one_graph_node() -> None:
    graph, key = load_corpus_vi(_MANY_INSTANCES_CALLER.resolve())
    callee_names = sorted(Path(k).name for k in graph.get_vi_dependencies(key))

    # Three instances of Create Connection Cluster.vim are one dependency.
    assert callee_names.count("Create Connection Cluster.vim") == 1
    assert callee_names.count("Assert Connections.vim") == 1
    assert not [n for n in callee_names if n.endswith(".vi") and "-" in n[:9]]


def test_generate_python_from_an_enum_control(tmp_path: Path) -> None:
    summary = generate_python(_ENUM_CTL, tmp_path)

    assert summary["error"] == 0
    [module] = tmp_path.rglob("actionctl.py")
    assert module.read_text(encoding="utf-8") == (
        "from __future__ import annotations\n"
        "from enum import IntEnum\n"
        "\n"
        "class Action(IntEnum):\n"
        '    """LabVIEW enum Action.ctl."""\n'
        "    Verify = 0\n"
        "    Start = 1\n"
        "    Stop = 2\n"
        "    Close = 3\n"
        "    Error = 4"
    )


def test_generate_python_from_a_cluster_control(tmp_path: Path) -> None:
    summary = generate_python(_CLUSTER_CTL, tmp_path)

    assert summary["error"] == 0
    [module] = tmp_path.rglob("paramsctl.py")
    assert module.read_text(encoding="utf-8") == (
        "from __future__ import annotations\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "from typing import Any\n"
        "\n"
        "class Params(SimpleNamespace):\n"
        '    """LabVIEW typedef Params.ctl."""\n'
        "    channels: Any\n"
        "    sample_rate: float\n"
        "    sample_count: int"
    )


def test_library_loads_its_vim_members_as_real_vis() -> None:
    library = (
        _SAMPLES
        / "measurement-plugin-labview/Source/Runtime/Measurements"
        / "NI Measurement Plug-In SDK.lvlib"
    )
    graph = InMemoryVIGraph()

    key = graph.load_lvlib(library, LoadMode.NONE)

    members = {
        Path(target).name: target in graph._stubs
        for _, target, data in graph._dep_graph.out_edges(key, data=True)
        if data.get("rel") == EdgeRel.OWNS and Path(target).suffix in (".vim", ".vit")
    }
    assert members == {
        "Add JSON Name Value Pair.vim": False,
        "Add Measurement Annotation.vim": False,
        "Encode to JSON String.vim": False,
    }
