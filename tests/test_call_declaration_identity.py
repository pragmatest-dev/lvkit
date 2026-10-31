"""A call is declared by the file it names: a VI's name is its file name, and a
VI is never listed as its own callee because a class beside it has a member of
the same name as a library VI it calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import file_qualified_name
from lvkit.graph.netlist_build import build_netlist_from_graph
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


def _class_graph(tmp_path: Path) -> tuple[InMemoryVIGraph, str, str, Path, Path]:
    """A child class and its parent, each with a ``Run.vi`` member."""
    child_vi = tmp_path / "Child" / "Run.vi"
    parent_vi = tmp_path / "Parent" / "Run.vi"
    graph = InMemoryVIGraph()
    parent_key = str(tmp_path / "Parent" / "Parent.lvclass")
    child_key = str(tmp_path / "Child" / "Child.lvclass")
    graph._dep_graph.add_node(parent_key, method_paths={"Run.vi": str(parent_vi)})
    graph._dep_graph.add_node(
        child_key, method_paths={"Run.vi": str(child_vi)}, parent_key=parent_key
    )
    return graph, child_key, parent_key, child_vi, parent_vi


def test_class_member_lookup_passes_over_the_caller_itself(tmp_path: Path) -> None:
    graph, child_key, parent_key, child_vi, parent_vi = _class_graph(tmp_path)
    assert graph.class_method(child_key, "Run.vi") == (child_key, str(child_vi))
    # The child's own Run.vi is the caller: the nearest other file is the parent's.
    assert graph.class_method(
        child_key, "Run.vi", excluding=str(child_vi.resolve())
    ) == (parent_key, str(parent_vi))
    # With no other owner, nothing resolves (the call is not the caller's file).
    assert graph.class_method(
        parent_key, "Run.vi", excluding=str(parent_vi.resolve())
    ) is None


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
