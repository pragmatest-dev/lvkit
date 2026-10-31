"""A call is declared by the file it names: a VI's name is its file name, and a
VI is never listed as its own callee because a class beside it has a member of
the same name as a library VI it calls."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.loading import file_qualified_name
from lvkit.graph.netlist import render_lvnet
from lvkit.graph.netlist_build import build_netlist_from_graph
from lvkit.graph.node_kinds import NodeType
from lvkit.load_mode import LoadMode
from lvkit.parser.models import ParsedDependencyRef
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


def _method_graph(tmp_path: Path) -> tuple[InMemoryVIGraph, str, list[str]]:
    """A caller VI that depends on class ``X``, whose members are ``A.vi`` (the
    caller itself) and a DIFFERENT ``Foo.vi``; ``loaded`` collects every file
    the method pass leaf-loads."""
    caller = tmp_path / "X" / "A.vi"
    foo = tmp_path / "X" / "Foo.vi"
    graph = InMemoryVIGraph()
    cls_key = str(tmp_path / "X" / "X.lvclass")
    graph._dep_graph.add_node(str(caller), name="A.vi", qname="X.lvclass:A.vi")
    graph._dep_graph.add_node(
        cls_key,
        node_type=NodeType.CLASS,
        name="X.lvclass",
        method_paths={"A.vi": str(caller), "Foo.vi": str(foo)},
    )
    graph._dep_graph.add_edge(str(caller), cls_key)
    loaded: list[str] = []

    def record(path: Path, *args: object) -> str:
        loaded.append(path.name)
        return str(path)

    graph._leaf_load_vi = record  # type: ignore[method-assign]
    return graph, str(caller), loaded


def _metadata(
    calls: list[str], dispatch: list[str] | None = None, vipi: list[str] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        iuse_to_qualified_name=dict(enumerate(calls)),
        dyn_refs={
            i: ParsedDependencyRef(
                name=q.rsplit(":", 1)[-1], path_tokens=[], qualified_name=q
            )
            for i, q in enumerate(dispatch or [])
        },
        subvi_method_names=vipi or [],
    )


def test_library_call_is_not_resolved_to_a_class_member_of_that_name(
    tmp_path: Path,
) -> None:
    graph, caller, loaded = _method_graph(tmp_path)
    graph._load_subvi_method_deps(
        caller, _metadata(["Lib.lvlib:Foo.vi"]), [], LoadMode.MINIMAL, set()
    )
    assert loaded == []


def test_call_owned_by_the_class_resolves_to_its_member(tmp_path: Path) -> None:
    graph, caller, loaded = _method_graph(tmp_path)
    graph._load_subvi_method_deps(
        caller, _metadata(["X.lvclass:Foo.vi"]), [], LoadMode.MINIMAL, set()
    )
    assert loaded == ["Foo.vi"]


def test_call_already_resolved_by_its_recorded_path_is_left_alone(
    tmp_path: Path,
) -> None:
    graph, caller, loaded = _method_graph(tmp_path)
    graph._load_subvi_method_deps(
        caller,
        _metadata(["X.lvclass:Foo.vi"]),
        [],
        LoadMode.MINIMAL,
        {"X.lvclass:Foo.vi"},
    )
    assert loaded == []


def test_ownerless_dispatch_name_resolves_through_the_callers_class(
    tmp_path: Path,
) -> None:
    graph, caller, loaded = _method_graph(tmp_path)
    graph._load_subvi_method_deps(
        caller, _metadata([], vipi=["Foo.vi"]), [], LoadMode.MINIMAL, set()
    )
    assert loaded == ["Foo.vi"]


def test_dispatch_name_a_recorded_call_accounts_for_is_not_a_second_call(
    tmp_path: Path,
) -> None:
    """``Message.lvclass:A.vi`` (a parent's method, not a dependency) and the
    bare ``A.vi`` the same call leaves in the VIPI table are ONE call: the bare
    name must not find the caller's own ``A.vi``."""
    graph, caller, loaded = _method_graph(tmp_path)
    graph._load_subvi_method_deps(
        caller,
        _metadata([], dispatch=["Message.lvclass:A.vi"], vipi=["A.vi"]),
        [],
        LoadMode.MINIMAL,
        set(),
    )
    assert loaded == []


def _declarations(vi: Path, root: Path) -> dict[str, str | None]:
    graph, key = load_corpus_vi(vi, root)
    return {
        d.qualified: d.path
        for d in build_netlist_from_graph(graph, key).dependencies
        if d.kind.value == "subVI"
    }


@pytest.mark.needs_samples
def test_renamed_file_is_declared_by_its_file_name() -> None:
    root = _SAMPLES / "LabVIEW-DAQ" / "Two Photon"
    decls = _declarations(root / "MasterAquisitionFile_2P.vi", root)
    assert decls["TrackWhiskerDroppedFrames_2P.vi"] == (
        "./TrackWhiskerDroppedFrames_2P.vi"
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
        decls = _declarations(template / rel, template)
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
    graph, key = load_corpus_vi(vi, template)
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
        template / "Build Assets" / "Post-Build Action.vi", template
    )
    lvnet = render_lvnet(build_netlist_from_graph(graph, key), display_name="X")
    sdk_class = "NI Measurement Plug-In SDK.lvlib:Measurement Plugin Service.lvclass"
    assert f"    class {sdk_class}\n" in lvnet
    assert "Program Files" not in lvnet
