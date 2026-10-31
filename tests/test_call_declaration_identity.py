"""A call is declared by the file it names: a VI's name is its file name, and a
VI is never listed as its own callee because a class beside it has a member of
the same name as a library VI it calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.loading import file_qualified_name
from lvkit.graph.models import VINode
from lvkit.graph.netlist import render_lvnet
from lvkit.graph.netlist_build import build_netlist_from_graph
from lvkit.graph.node_kinds import NodeType
from tests.corpus_graphs import load_corpus_vi

_SAMPLES = Path(".lvkit/cache/samples").resolve()


def test_file_name_is_the_last_component() -> None:
    assert file_qualified_name("TrackDroppedFrames_2P.vi", "Whisker_2P.vi") == (
        "Whisker_2P.vi"
    )
    assert file_qualified_name("Lib.lvlib:Cls.lvclass:Old.vi", "New.vi") == (
        "Lib.lvlib:Cls.lvclass:New.vi"
    )
    assert file_qualified_name(None, "New.vi") == "New.vi"
    assert file_qualified_name("", "New.vi") == "New.vi"


def _declarations(vi: Path) -> dict[str, str | None]:
    graph, key = load_corpus_vi(vi)
    return {
        d.qualified: d.path
        for d in build_netlist_from_graph(graph, key).dependencies
        if d.kind.value == "subVI"
    }


@pytest.mark.needs_samples
def test_renamed_file_is_declared_by_its_file_name() -> None:
    root = _SAMPLES / "LabVIEW-DAQ" / "Two Photon"
    decls = _declarations(root / "MasterAquisitionFile_2P.vi")
    # Paths are shown relative to the VI's project (the sample's git root).
    assert decls["TrackWhiskerDroppedFrames_2P.vi"] == (
        "./Two Photon/TrackWhiskerDroppedFrames_2P.vi"
    )
    assert "TrackDroppedFrames_2P.vi" not in decls


@pytest.mark.needs_samples
def test_library_call_named_like_the_caller_is_not_the_caller() -> None:
    template = (
        _SAMPLES
        / "measurement-plugin-labview/Source/Generator"
        / "Measurement Plug-In Template/Measurement"
    )
    for rel, name in (
        ("Build Assets/Post-Build Action.vi", "Post-Build Action.vi"),
        ("Run Service.vi", "Run Service.vi"),
    ):
        decls = _declarations(template / rel)
        # The SDK VI the body calls is absent from the project: no path.
        assert decls[f"NI Measurement Plug-In SDK.lvlib:{name}"] is None
        assert f"Measurement Plugin Template.lvclass:{name}" not in decls
        assert name not in decls


@pytest.mark.needs_samples
def test_text_views_declare_the_library_call_not_the_caller() -> None:
    template = (
        _SAMPLES
        / "measurement-plugin-labview/Source/Generator"
        / "Measurement Plug-In Template/Measurement"
    )
    vi = template / "Build Assets" / "Post-Build Action.vi"
    graph, key = load_corpus_vi(vi)
    lvnet = render_lvnet(build_netlist_from_graph(graph, key), display_name="X")
    uses = [
        ln.strip()
        for ln in lvnet.splitlines()
        if ln.startswith("    subVI") and " : " not in ln
    ]
    assert uses == ["subVI NI Measurement Plug-In SDK.lvlib:Post-Build Action.vi"]
    described = describe_vi(graph, key)
    assert "NI Measurement Plug-In SDK.lvlib:Post-Build Action.vi" in described
    assert "Measurement Plugin Template.lvclass:Post-Build Action.vi" not in described


def test_absent_dependency_with_an_absolute_recorded_path_shows_no_path(
    tmp_path: Path,
) -> None:
    """An absolute path (type 0) is a machine path outside the project: it is
    never joined onto the caller's file, and no path is shown for the stub."""
    tokens = ["C", "Program Files", "NI", "vi.lib", "Lib", "Thing.lvclass"]
    graph = InMemoryVIGraph()
    caller = tmp_path / "Caller.vi"
    graph._source_paths[str(caller)] = caller
    graph._dep_graph.add_node(str(caller))
    key = "Lib.lvlib:Thing.lvclass"
    graph._add_stub(
        key,
        NodeType.CLASS,
        "Thing.lvclass",
        key,
        path_tokens=tokens,
        caller=str(caller),
        path_type=0,
    )
    assert graph._dependency_file_path(key) is None
    # The same tokens recorded relative to the calling file do resolve.
    relative = "Rel.lvlib:Thing.lvclass"
    graph._add_stub(
        relative,
        NodeType.CLASS,
        "Thing.lvclass",
        relative,
        path_tokens=["", "Lib", "Thing.lvclass"],
        caller=str(caller),
        path_type=1,
    )
    assert graph._dependency_file_path(relative) == tmp_path / "Lib" / "Thing.lvclass"


@pytest.mark.needs_samples
def test_absent_class_declared_without_a_machine_path() -> None:
    template = (
        _SAMPLES
        / "measurement-plugin-labview/Source/Generator"
        / "Measurement Plug-In Template/Measurement"
    )
    graph, key = load_corpus_vi(
        template / "Build Assets" / "Post-Build Action.vi")
    lvnet = render_lvnet(build_netlist_from_graph(graph, key), display_name="X")
    sdk_class = "NI Measurement Plug-In SDK.lvlib:Measurement Plugin Service.lvclass"
    assert f"    class {sdk_class}\n" in lvnet
    assert "Program Files" not in lvnet


@pytest.mark.needs_samples
def test_method_kept_in_a_class_subfolder_is_declared_once_by_its_file() -> None:
    root = _SAMPLES / "measurement-plugin-labview" / "Source"
    vi = root / "Runtime/Measurements/Measurement Service V1/Destroy.vi"
    graph, key = load_corpus_vi(vi)
    netlist = build_netlist_from_graph(graph, key)
    # Shown relative to the VI's project (Source/Runtime holds its .lvproj).
    real = (
        "./../gRPC/Generated APIs/ni/measurementlink/measurement/v1/"
        "RPC Service/MeasurementService/Server API/Destroy.vi"
    )
    assert [
        (d.qualified, d.path) for d in netlist.dependencies if d.kind.value == "subVI"
    ] == [("Destroy.vi", real)]
    (call,) = [
        n
        for n in graph.iter_nodes(key)
        if isinstance(n, VINode) and n.id != n.vi_path
    ]
    assert call.callee_key is not None
    assert Path(call.callee_key).exists()
    assert call.callee_key.endswith("/MeasurementService/Server API/Destroy.vi")
