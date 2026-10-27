"""A Shared Variable node and its project library (issue #149).

``tests/corpus/issues/149/SV Sample.vi`` has three Shared Variable nodes in a flat
sequence -- write 1.5, read, read with the timestamp -- on the single-process
variable ``Value`` of ``SV Sample.lvlib``. The VI's block-diagram link table
records the library in an ``HpVL`` record that lists the three nodes' uids.
``tests/corpus/issues/149/network/SV Sample.vi`` is the same project with ``Value``
published to the network; its record differs after the second GUID.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.package_modules import PackageModules
from lvkit.conversion_gap import ConversionGap
from lvkit.extractor import extract_vi_xml
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.lvnet_parse import (
    LvnetParseError,
    SharedVariableComponent,
    netlist_signature,
    parse_lvnet,
    split_shared_variable_component,
)
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import SharedVariableLibrary, SharedVariableNode
from lvkit.graph.netlist import NetlistModule, build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.render_lvnet import lvnet_literal_token, lvnet_shared_variable_target
from lvkit.index.build import project_vi_facts
from lvkit.index.model import NodeKind
from lvkit.load_mode import LoadMode
from lvkit.models import SharedVariableKind
from lvkit.parser import parse_vi
from lvkit.parser import vi as vi_parser
from lvkit.parser.link_table import (
    LinkTableVersion,
    SharedVariableRecordFailure,
    SharedVariableTable,
    decode_shared_variable_links,
)
from lvkit.parser.node_types import SharedVariableTerminal
from lvkit.pipeline import create_import_resolver

from .test_link_table import build_hpvl_record
from .test_lvnet_identity import _assert_graph_identity
from .test_lvnet_roundtrip import _first_mismatch

SV_SAMPLE = Path(__file__).parent / "corpus" / "issues" / "149" / "SV Sample.vi"
SV_NETWORK_SAMPLE = SV_SAMPLE.parent / "network" / "SV Sample.vi"
PERSISTENT_ID = "{F7E00465-D283-4032-9E36-1D82BDC3CC46}"
NETWORK_PATH = r"\\.\SV Sample\Value"
BOTH_FIXTURES = pytest.mark.parametrize(
    "vi", [SV_SAMPLE, SV_NETWORK_SAMPLE], ids=["single-process", "network"]
)


def _load(vi: Path = SV_SAMPLE) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(vi, LoadMode.MINIMAL, search_paths=[vi.parent])
    return graph, graph.list_vis()[0]


def _generated_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, graph: InMemoryVIGraph, vi: str
):
    """Generate ``vi`` into a package with its ``shared_variables`` module and
    import both: ``(sv_sample module, shared_variables module)``."""
    root = tmp_path / "svpkg"
    root.mkdir()
    (root / "__init__.py").write_text("")
    modules = PackageModules.from_graph(graph, [vi])
    resolver = create_import_resolver(
        "svpkg",
        root,
        {vi: root / "sv_sample.py"},
        root / "sv_sample.py",
        package_modules=modules,
    )
    source = build_module(
        graph.get_vi_context(vi), vi, graph=graph, import_resolver=resolver
    )
    assert "lvkit" not in source
    (root / "sv_sample.py").write_text(source)
    modules.write(root)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        yield (
            importlib.import_module("svpkg.sv_sample"),
            importlib.import_module("svpkg.shared_variables"),
        )
    finally:
        for name in [m for m in sys.modules if m.split(".")[0] == "svpkg"]:
            del sys.modules[name]


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
    published = "SV Sample.lvlib, Single Process"
    assert f"Shared Variable `Value` ({published}) write" in text
    assert text.count(f"Shared Variable `Value` ({published}) read") == 2


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


def _assert_strict_round_trip(text: str, module: NetlistModule) -> None:
    parsed = parse_lvnet(text)
    assert _first_mismatch(netlist_signature(module), netlist_signature(parsed)) is None
    rebuilt = reconstruct_module(parsed)
    _assert_graph_identity(module, rebuilt, "SV Sample.vi")
    assert render_lvnet(rebuilt, display_name="SV Sample.vi", verbose=True) == text


@BOTH_FIXTURES
def test_lvnet_round_trips_strictly(vi: Path) -> None:
    graph, vi_name = _load(vi)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name="SV Sample.vi", verbose=True)
    _assert_strict_round_trip(text, module)


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
        assert split_shared_variable_component(
            f"{target} : {access}"
        ) == SharedVariableComponent(library, variable, access)


def test_lvnet_target_without_access_is_an_error() -> None:
    with pytest.raises(LvnetParseError):
        split_shared_variable_component("SV Sample.lvlib::Value")


def test_generated_python_writes_then_reads_the_shared_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, vi = _load()
    for sv_sample, shared in _generated_package(tmp_path, monkeypatch, graph, vi):
        assert shared.sv_samplelvlib_value == 0.0
        assert shared.sv_samplelvlib_value_timestamp == 0.0
        result = sv_sample.sv_sample()
        assert result.value == 1.5
        assert isinstance(result.timestamp, float) and result.timestamp > 0
        assert shared.sv_samplelvlib_value == 1.5
        assert result.timestamp == shared.sv_samplelvlib_value_timestamp


@pytest.mark.parametrize(
    "attribute",
    [
        "",
        f" (network-published {lvnet_literal_token(NETWORK_PATH)})",
        ' (library unreadable "HpVL record at offset 12: a : b \\"q\\"")',
    ],
)
def test_lvnet_attribute_reads_back_exactly(attribute: str) -> None:
    component = f"Lib.lvlib::Value : write{attribute}"
    parsed = split_shared_variable_component(component)
    assert (parsed.library, parsed.variable, parsed.access) == (
        "Lib.lvlib",
        "Value",
        "write",
    )
    assert (parsed.network_path is not None) == ("network-published" in attribute)
    assert (parsed.library_unreadable is not None) == ("unreadable" in attribute)


def test_lvnet_attribute_after_a_quoted_name_with_a_separator() -> None:
    parsed = split_shared_variable_component(
        'Lib.lvlib::"Two Words : x" : read (network-published "p : q")'
    )
    assert parsed.variable == "Two Words : x"
    assert parsed.network_path == "p : q"


def test_a_malformed_lvnet_attribute_is_an_error() -> None:
    for bad in (
        'read (network-published "p"',
        "read (somewhere)",
        "read (library unreadable p)",
    ):
        with pytest.raises(LvnetParseError):
            split_shared_variable_component(f"Lib.lvlib::Value : {bad}")


def test_network_fixture_loads_with_its_library_and_path() -> None:
    graph, vi = _load(SV_NETWORK_SAMPLE)
    nodes = _nodes(graph, vi)
    assert sorted(nodes) == ["1033", "1112", "977"]
    for node in nodes.values():
        assert node.library == SharedVariableLibrary(
            qualified_name="SV Sample.lvlib",
            kind=SharedVariableKind.NETWORK,
            persistent_id=PERSISTENT_ID,
            network_path=NETWORK_PATH,
        )
        assert node.library_unreadable is None
    assert nodes["1033"].is_write


def test_network_fixture_describe_names_library_kind_and_path() -> None:
    graph, vi = _load(SV_NETWORK_SAMPLE)
    text = describe_vi(graph, vi)
    published = f"SV Sample.lvlib, Network-Published at {NETWORK_PATH}"
    assert f"Shared Variable `Value` ({published}) write" in text
    assert text.count(f"Shared Variable `Value` ({published}) read") == 2


def test_network_fixture_lvnet_json_and_index_carry_the_path() -> None:
    graph, vi = _load(SV_NETWORK_SAMPLE)
    module = build_netlist_from_graph(graph, vi)
    text = render_lvnet(module, display_name="SV Sample.vi", verbose=True)
    escaped = NETWORK_PATH.replace("\\", "\\\\")
    expected = (
        "shared-variable Value_1033 : SV Sample.lvlib::Value : write "
        f'(network-published "{escaped}")'
    )
    assert expected in text
    body = netlist_to_dict(module)["body"]
    shared = [
        inner
        for item in body
        for frame in item.get("frames", [])
        for inner in frame["body"]
        if inner.get("kind") == "shared_variable"
    ]
    assert {s["network_path"] for s in shared} == {NETWORK_PATH}
    facts = project_vi_facts(graph, vi, SV_NETWORK_SAMPLE, SV_NETWORK_SAMPLE.parent)
    nodes = [n for n in facts.nodes if n.kind is NodeKind.SHARED_VARIABLE]
    assert {n.network_path for n in nodes} == {NETWORK_PATH}


def test_single_process_fixture_has_no_network_path() -> None:
    graph, vi = _load()
    for node in _nodes(graph, vi).values():
        assert node.library is not None and node.library.network_path is None


def test_generated_python_for_a_network_published_variable_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, vi = _load(SV_NETWORK_SAMPLE)
    for sv_sample, shared in _generated_package(tmp_path, monkeypatch, graph, vi):
        result = sv_sample.sv_sample()
        assert result.value == 1.5
        assert isinstance(result.timestamp, float) and result.timestamp > 0
        assert shared.sv_samplelvlib_value == 1.5
        assert result.timestamp == shared.sv_samplelvlib_value_timestamp


def _fail_shared_variable_records(
    monkeypatch: pytest.MonkeyPatch, node_uids: tuple[int, ...] | None
) -> None:
    """Make the link table's ``HpVL`` record undecodable, as the loader sees it."""
    failure = SharedVariableRecordFailure(
        offset=12,
        reason="HpVL record at offset 12: unknown layout flag 0x07",
        node_uids=node_uids,
    )
    monkeypatch.setattr(
        vi_parser,
        "read_shared_variable_links",
        lambda path, version: SharedVariableTable(links=[], failures=[failure]),
    )


@pytest.mark.parametrize("node_uids", [(1033, 1112, 977), None])
def test_an_unreadable_record_leaves_the_vi_loadable_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, node_uids: tuple[int, ...] | None
) -> None:
    _fail_shared_variable_records(monkeypatch, node_uids)
    graph, vi = _load(SV_NETWORK_SAMPLE)
    reason = "HpVL record at offset 12: unknown layout flag 0x07"
    for node in _nodes(graph, vi).values():
        assert node.library is None
        assert node.library_unreadable == reason
    text = describe_vi(graph, vi)
    assert f"Shared Variable `Value` (library link unreadable: {reason}) write" in text
    assert "## Dependencies" not in text


def test_a_record_failing_before_its_uids_is_read_names_no_node_wrongly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_shared_variable_records(monkeypatch, (4242,))
    graph, vi = _load(SV_NETWORK_SAMPLE)
    for node in _nodes(graph, vi).values():
        assert node.library is None
        assert node.library_unreadable is None


def test_an_unreadable_record_reads_back_strictly_from_lvnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_shared_variable_records(monkeypatch, (1033, 1112, 977))
    graph, vi = _load(SV_NETWORK_SAMPLE)
    module = build_netlist_from_graph(graph, vi)
    text = render_lvnet(module, display_name="SV Sample.vi", verbose=True)
    assert (
        "shared-variable Value_1033 : Value : write (library unreadable "
        '"HpVL record at offset 12: unknown layout flag 0x07")'
    ) in text
    _assert_strict_round_trip(text, module)
    item = netlist_to_dict(module)["body"][0]["frames"][0]["body"][0]
    assert item["library_unreadable"].startswith("HpVL record at offset 12")


def test_an_unreadable_record_raises_a_clear_error_for_generated_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_shared_variable_records(monkeypatch, (1033, 1112, 977))
    graph, vi = _load(SV_NETWORK_SAMPLE)
    with pytest.raises(ConversionGap, match="library link is unreadable.*layout flag"):
        build_module(graph.get_vi_context(vi), vi, graph=graph)


def _use_hpvr_record(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make the link table hold one ``HpVr`` record (three-part name, network
    path with a host address) listing the fixture's three nodes."""
    path = r"\\host\SV Sample\Value"
    data = build_hpvl_record(
        offset=0,
        library=b"",
        variable=b"",
        name_parts=[b"My Computer", b"SV Sample.lvlib", b"Value"],
        persistent_id=PERSISTENT_ID,
        node_uids=[1033, 1112, 977],
        variable_type="Network",
        network_path=path,
        ident=b"HpVr",
    )
    table = decode_shared_variable_links(data, LinkTableVersion(25, 0, 0, 4))
    assert table.failures == []
    monkeypatch.setattr(vi_parser, "read_shared_variable_links", lambda p, v: table)
    return path


def test_an_hpvr_record_links_its_nodes_and_describe_shows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _use_hpvr_record(monkeypatch)
    graph, vi = _load(SV_NETWORK_SAMPLE)
    for node in _nodes(graph, vi).values():
        assert node.library == SharedVariableLibrary(
            qualified_name="SV Sample.lvlib",
            kind=SharedVariableKind.NETWORK,
            persistent_id=PERSISTENT_ID,
            network_path=path,
        )
    text = describe_vi(graph, vi)
    published = f"SV Sample.lvlib, Network-Published at {path}"
    assert f"Shared Variable `Value` ({published}) write" in text
    assert "  SV Sample.lvlib (Shared Variable library)" in text


def test_an_hpvr_record_reads_back_strictly_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_hpvr_record(monkeypatch)
    graph, vi = _load(SV_NETWORK_SAMPLE)
    module = build_netlist_from_graph(graph, vi)
    text = render_lvnet(module, display_name="SV Sample.vi", verbose=True)
    assert "(network-published " in text
    _assert_strict_round_trip(text, module)

    for sv_sample, shared in _generated_package(tmp_path, monkeypatch, graph, vi):
        result = sv_sample.sv_sample()
        assert result.value == 1.5
        assert result.timestamp == shared.sv_samplelvlib_value_timestamp


def test_an_hpvr_mismatch_leaves_the_vi_loading_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = build_hpvl_record(
        offset=0,
        library=b"",
        variable=b"",
        name_parts=[b"Elsewhere", b"SV Sample.lvlib", b"Value"],
        persistent_id=PERSISTENT_ID,
        node_uids=[1033, 1112, 977],
        variable_type="Network",
        network_path=r"\\host\SV Sample\Value",
        ident=b"HpVr",
    )
    table = decode_shared_variable_links(data, LinkTableVersion(25, 0, 0, 4))
    monkeypatch.setattr(vi_parser, "read_shared_variable_links", lambda p, v: table)
    graph, vi = _load(SV_NETWORK_SAMPLE)
    for node in _nodes(graph, vi).values():
        assert node.library is None
        assert "does not start with the record's target" in str(node.library_unreadable)
    assert "library link unreadable: HpVr record at offset 2" in describe_vi(graph, vi)
