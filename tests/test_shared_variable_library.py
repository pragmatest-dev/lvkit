"""A Shared Variable node and its project library (issue #149).

``tests/corpus/issues/149/SV Sample.vi`` has three Shared Variable nodes in a flat
sequence -- write 1.5, read, read with the timestamp -- on the single-process
variable ``Value`` of ``SV Sample.lvlib``. The VI's block-diagram link table
records the library in an ``HpVL`` record that lists the three nodes' uids.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.nodes.base import CodeGenError
from lvkit.extractor import extract_vi_xml
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.lvnet_parse import (
    LvnetParseError,
    netlist_signature,
    parse_lvnet,
    split_shared_variable_component,
)
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import SharedVariableLibrary, SharedVariableNode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.render_lvnet import lvnet_shared_variable_target
from lvkit.index.build import project_vi_facts
from lvkit.index.model import NodeKind
from lvkit.load_mode import LoadMode
from lvkit.models import SharedVariableKind
from lvkit.parser import parse_vi
from lvkit.parser.node_types import SharedVariableTerminal
from lvkit.runtime import shared_variable

from .test_lvnet_identity import _assert_graph_identity
from .test_lvnet_roundtrip import _first_mismatch

SV_SAMPLE = Path(__file__).parent / "corpus" / "issues" / "149" / "SV Sample.vi"
PERSISTENT_ID = "{F7E00465-D283-4032-9E36-1D82BDC3CC46}"


def _load(vi: Path = SV_SAMPLE) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(vi, LoadMode.MINIMAL, search_paths=[vi.parent])
    return graph, graph.list_vis()[0]


def _nodes(graph: InMemoryVIGraph, vi: str) -> dict[str, SharedVariableNode]:
    return {
        n.id.rsplit("::", 1)[-1]: n
        for n in graph.iter_nodes(vi)
        if isinstance(n, SharedVariableNode)
    }


def test_parser_links_each_node_to_its_library_by_uid() -> None:
    bd, fp, main_xml = extract_vi_xml(SV_SAMPLE)
    metadata = parse_vi(bd_xml=bd, fp_xml=fp, main_xml=main_xml).metadata
    assert sorted(metadata.shared_variable_links) == ["1033", "1112", "977"]
    link = metadata.shared_variable_links["977"]
    assert link.library_qualified_name == "SV Sample.lvlib"
    assert link.persistent_id == PERSISTENT_ID
    assert [ref.qualified_name for ref in metadata.dependency_refs] == [
        "SV Sample.lvlib"
    ]


def test_graph_node_carries_library_kind_and_persistent_id() -> None:
    graph, vi = _load()
    nodes = _nodes(graph, vi)
    assert sorted(nodes) == ["1033", "1112", "977"]
    for node in nodes.values():
        assert node.variable_name == "Value"
        assert node.library == SharedVariableLibrary(
            qualified_name="SV Sample.lvlib",
            kind=SharedVariableKind.SINGLE_PROCESS,
            persistent_id=PERSISTENT_ID,
        )
    assert nodes["1033"].is_write
    assert not nodes["1112"].is_write and not nodes["977"].is_write


def test_terminal_three_is_the_timestamp_of_the_node_that_shows_it() -> None:
    graph, vi = _load()
    nodes = _nodes(graph, vi)
    timestamp = next(
        t for t in nodes["977"].terminals if t.index == SharedVariableTerminal.TIMESTAMP
    )
    assert (timestamp.name, timestamp.direction) == ("timestamp", "output")
    assert not any(
        t.index == SharedVariableTerminal.TIMESTAMP for t in nodes["1112"].terminals
    )


def test_library_is_a_dependency_of_the_vi() -> None:
    graph, vi = _load()
    (library_key,) = graph.get_vi_dependencies(vi)
    assert library_key.endswith("SV Sample.lvlib")
    assert graph.shared_variable_library_key(_nodes(graph, vi)["977"]) == library_key


def test_describe_lists_the_library_and_each_access() -> None:
    graph, vi = _load()
    text = describe_vi(graph, vi)
    assert "## Dependencies\n  SV Sample.lvlib (Shared Variable library)" in text
    assert "Shared Variable `Value` (SV Sample.lvlib) write" in text
    assert text.count("Shared Variable `Value` (SV Sample.lvlib) read") == 2


def test_library_is_listed_without_its_file(tmp_path: Path) -> None:
    lone = tmp_path / SV_SAMPLE.name
    shutil.copy(SV_SAMPLE, lone)
    graph, vi = _load(lone)
    assert "SV Sample.lvlib (Shared Variable library)" in describe_vi(graph, vi)
    text = render_lvnet(build_netlist_from_graph(graph, vi), verbose=True)
    assert "    library SV Sample.lvlib\n" in text


def test_lvnet_names_the_library_and_lists_it_under_uses() -> None:
    graph, vi = _load()
    text = render_lvnet(build_netlist_from_graph(graph, vi), verbose=True)
    assert "    library SV Sample.lvlib ; " in text
    assert "shared-variable Value_1033 : SV Sample.lvlib::Value : write" in text
    assert "shared-variable Value_1112 : SV Sample.lvlib::Value : read" in text
    assert "shared-variable Value_977 : SV Sample.lvlib::Value : read" in text
    assert "out  timestamp           : MeasureData" in text


def test_lvnet_round_trips_strictly() -> None:
    graph, vi = _load()
    module = build_netlist_from_graph(graph, vi)
    text = render_lvnet(module, display_name="SV Sample.vi", verbose=True)
    parsed = parse_lvnet(text)
    assert _first_mismatch(netlist_signature(module), netlist_signature(parsed)) is None
    rebuilt = reconstruct_module(parsed)
    _assert_graph_identity(module, rebuilt, "SV Sample.vi")
    assert render_lvnet(rebuilt, display_name="SV Sample.vi", verbose=True) == text


def test_json_names_the_library() -> None:
    graph, vi = _load()
    body = netlist_to_dict(build_netlist_from_graph(graph, vi))["body"]
    shared = [
        inner
        for item in body
        for frame in item.get("frames", [])
        for inner in frame["body"]
        if inner.get("kind") == "shared_variable"
    ]
    assert {(s["library"], s["variable"]) for s in shared} == {
        ("SV Sample.lvlib", "Value")
    }


def test_index_projects_the_library_as_the_node_s_qualified_name() -> None:
    graph, vi = _load()
    facts = project_vi_facts(graph, vi, SV_SAMPLE, SV_SAMPLE.parent)
    shared = [n for n in facts.nodes if n.kind is NodeKind.SHARED_VARIABLE]
    assert len(shared) == 3
    assert {(n.name, n.qualified_name) for n in shared} == {
        ("Value", "SV Sample.lvlib")
    }


@pytest.mark.parametrize(
    ("library", "variable"),
    [
        ("SV Sample.lvlib", "Value"),
        ("Lib::Odd.lvlib", "Value"),
        ("Lib.lvlib", "A::B"),
        ("Lib.lvlib", "Two Words : x"),
        ("Lib.lvlib", '"quoted"'),
        ('"Lib".lvlib', "Value"),
        (None, "Value"),
        (None, "A::B"),
        (None, '"quoted"'),
        (None, "Two Words : x"),
    ],
)
def test_lvnet_target_names_read_back_exactly(
    library: str | None, variable: str
) -> None:
    target = lvnet_shared_variable_target(library, variable)
    for access in ("read", "write"):
        assert split_shared_variable_component(f"{target} : {access}") == (
            library,
            variable,
            access,
        )


def test_lvnet_target_without_access_is_an_error() -> None:
    with pytest.raises(LvnetParseError):
        split_shared_variable_component("SV Sample.lvlib::Value")


def test_generated_python_writes_then_reads_the_shared_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shared_variable, "_cells", {})
    graph, vi = _load()
    source = build_module(graph.get_vi_context(vi), vi, graph=graph)
    namespace: dict[str, object] = {}
    exec(compile(source, "sv_sample.py", "exec"), namespace)
    sv_sample = namespace["sv_sample"]
    assert callable(sv_sample)

    assert shared_variable.timestamp("SV Sample.lvlib", "Value") == 0.0
    result = sv_sample()
    assert result.value == 1.5
    assert isinstance(result.timestamp, float) and result.timestamp > 0
    assert shared_variable.read("SV Sample.lvlib", "Value", 0.0) == 1.5
    assert result.timestamp == shared_variable.timestamp("SV Sample.lvlib", "Value")


def test_a_variable_nothing_wrote_reads_its_type_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shared_variable, "_cells", {})
    assert shared_variable.read("Unwritten.lvlib", "X", 0.0) == 0.0
    assert shared_variable.timestamp("Unwritten.lvlib", "X") == 0.0
    shared_variable.write("Unwritten.lvlib", "X", 2.0)
    assert shared_variable.read("Unwritten.lvlib", "X", 0.0) == 2.0


def test_a_network_published_variable_has_no_python_equivalent() -> None:
    graph, vi = _load()
    for node in _nodes(graph, vi).values():
        assert node.library is not None
        node.library = node.library.model_copy(
            update={"kind": SharedVariableKind.NETWORK}
        )
    with pytest.raises(CodeGenError, match="network-published and has no Python"):
        build_module(graph.get_vi_context(vi), vi, graph=graph)
