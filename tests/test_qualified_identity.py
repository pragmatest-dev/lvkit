"""A loaded VI's qualified name is its own ``LIBN`` owner chain plus its file
name; a class or library member is named by the class or library that lists it
(that file's own name); a dependency's connector pane is read by its exact key,
never by name; a type is the caller's dependency only through a link record that
gives its file that qualified name. Tiny handmade diagrams, every view."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.loading import build_link_index, file_qualified_name
from lvkit.graph.models import VINode
from lvkit.graph.netlist import render_lvnet
from lvkit.graph.netlist_build import build_netlist_from_graph
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.node_kinds import NodeType
from lvkit.parser.metadata import (
    name_records_by_path,
    parse_xml_link_path_refs,
    recorded_file_name,
)
from lvkit.parser.models import ParsedDependencyRef
from lvkit.parser.xml_trees import parsed_xml
from tests.structure_diagrams import TYPES, main_xml, root_diagram

_LIBN = (
    '<LIBN><Section Index="0" Format="inline">'
    "<Library>Lib.lvlib</Library><Library>C.lvclass</Library>"
    "</Section></LIBN>\n"
)
# The VI's own class link (``VILB``) names no qualified name; a front-panel
# record (``TDCC``) names a control by the name the caller records.
_LIVI = (
    '<LIvi><Section Index="0" Format="inline"><LVIN>'
    '<VILB LinkSaveFlag="2"><LinkSaveQualName />'
    '<LinkSavePathRef Ident="PTH0" TpVal="1"><String /><String>c.lvclass</String>'
    "</LinkSavePathRef></VILB>"
    '<TDCC LinkSaveFlag="2"><LinkSaveQualName><String>Inner.lvlib</String>'
    "<String>Item.ctl</String></LinkSaveQualName>"
    '<LinkSavePathRef Ident="PTH0" TpVal="1"><String /><String>Item.ctl</String>'
    "</LinkSavePathRef></TDCC>"
    "</LVIN></Section></LIvi>\n"
)


def _main(blocks: str) -> str:
    return main_xml(TYPES).replace("</RSRC>", blocks + "</RSRC>")


def _load(tmp_path: Path, blocks: str) -> tuple[InMemoryVIGraph, str]:
    (tmp_path / "T.xml").write_text(_main(blocks))
    bd = tmp_path / "T_BDHb.xml"
    bd.write_text(root_diagram([], [], []))
    graph = InMemoryVIGraph()
    graph.load_vi(bd)
    return graph, graph.list_vis()[0]


def _views(graph: InMemoryVIGraph, vi: str) -> tuple[str, str, dict]:
    module = build_netlist_from_graph(graph, vi)
    return (
        render_lvnet(module, display_name=graph.vi_display_name(vi), verbose=True),
        describe_vi(graph, vi),
        netlist_to_dict(module, verbose=True),
    )


def test_a_vis_qualified_name_is_its_libn_chain_and_file_name(tmp_path: Path) -> None:
    graph, vi = _load(tmp_path, _LIBN)
    assert graph.vi_display_name(vi) == "Lib.lvlib:C.lvclass:T.vi"
    assert graph._dep_graph.nodes[vi]["qname"] == "Lib.lvlib:C.lvclass:T.vi"
    lvnet, _, _ = _views(graph, vi)
    assert lvnet.startswith("vi Lib.lvlib:C.lvclass:T.vi :")


def test_a_vi_with_no_libn_is_its_file_name(tmp_path: Path) -> None:
    graph, vi = _load(tmp_path, "")
    assert graph.vi_display_name(vi) == "T.vi"
    assert file_qualified_name([], "T.vi") == "T.vi"


def test_a_member_is_named_by_the_class_that_lists_it(tmp_path: Path) -> None:
    """Nested library -> class owners: the VI's LIBN spells its class
    ``C.lvclass``; the class file is ``c.lvclass`` in library ``Lib.lvlib``. Once
    the library lists the class and the class lists the VI, the VI is
    ``Lib.lvlib:c.lvclass:T.vi`` in every view."""
    graph, vi = _load(tmp_path, _LIBN)
    lib, cls = str(tmp_path / "Lib.lvlib"), str(tmp_path / "c.lvclass")
    graph._dep_graph.add_node(lib, node_type=NodeType.LIBRARY)
    graph._register_dep_node(lib, Path(lib), "Lib.lvlib", "Lib.lvlib")
    graph._dep_graph.add_node(cls, node_type=NodeType.CLASS)
    graph._register_dep_node(cls, Path(cls), "c.lvclass", "c.lvclass")
    graph._add_ownership_edge(cls, vi)
    assert graph.vi_display_name(vi) == "c.lvclass:T.vi"
    graph._add_ownership_edge(lib, cls)
    assert graph._dep_graph.nodes[cls]["qname"] == "Lib.lvlib:c.lvclass"
    assert graph.vi_display_name(vi) == "Lib.lvlib:c.lvclass:T.vi"
    lvnet, describe, _ = _views(graph, vi)
    assert lvnet.startswith("vi Lib.lvlib:c.lvclass:T.vi :")


def test_the_own_class_record_is_named_by_the_libn_chain(tmp_path: Path) -> None:
    (tmp_path / "T.xml").write_text(_main(_LIBN + _LIVI))
    owners = ["Lib.lvlib", "C.lvclass"]
    refs = parse_xml_link_path_refs(parsed_xml(tmp_path / "T.xml"), owners)
    assert [(r.name, r.qualified_name) for r in refs] == [
        ("c.lvclass", "Lib.lvlib:C.lvclass"),
        ("Item.ctl", "Inner.lvlib:Item.ctl"),
    ]


def test_a_record_names_only_the_file_its_path_ends_in() -> None:
    assert recorded_file_name("VILB", [], "x.lvclass", []) is None
    assert recorded_file_name("VILB", [], "x.lvclass", ["X.lvclass"]) == "X.lvclass"
    assert recorded_file_name("VIPI", ["A.lvclass", "\x07\x00"], "A.lvclass", []) == (
        "A.lvclass"
    )
    assert recorded_file_name("FPPI", ["\x07\x00"], "A.lvclass", []) is None
    assert recorded_file_name(
        "IUVI", ["A.lvclass", "m.vi"], "M.vi", []
    ) == "A.lvclass:m.vi"


def test_a_type_is_a_dependency_only_through_a_record_of_its_name() -> None:
    """A record in the caller's own library omits that library; names compare
    without regard to case; a named record of another library's file of the
    same file name is not the type's (case-only class renames match); a record
    that names no file is that file's, by its path's file name."""
    own = ParsedDependencyRef("Item.ctl", ["", "Item.ctl"], "Inner.lvlib:Item.ctl")
    other = ParsedDependencyRef("C.lvclass", ["", "C.lvclass"], "Lib.lvlib:C.lvclass")
    unnamed = ParsedDependencyRef("Z.ctl", ["", "Z.ctl"], None)
    index = build_link_index([own, other, unnamed], ["Outer.lvlib"])
    assert index.records_of("Inner.lvlib:Item.ctl") == [own]
    assert index.records_of("Outer.lvlib:Inner.lvlib:ITEM.ctl") == [own]
    assert index.records_of("lib.lvlib:c.lvclass") == [other]
    assert index.records_of("Other.lvlib:C.lvclass") is None
    assert index.records_of("Any.lvlib:Z.ctl") == [unnamed]


def test_a_record_of_a_named_records_path_takes_its_name() -> None:
    """AF Debug ``Ping.vi``: its ``FPPI`` record of its own class names no file
    but has the ``VILB`` record's path, so it is that class (``AF Debug``), not
    Actor Framework's class of the same file name."""
    vilb = ParsedDependencyRef(
        "Handle.lvclass", ["", "Handle.lvclass"], "AF Debug.lvlib:Handle.lvclass"
    )
    fppi = ParsedDependencyRef("Handle.lvclass", ["", "handle.lvclass"], None)
    named = name_records_by_path([vilb, fppi])
    assert [r.qualified_name for r in named] == ["AF Debug.lvlib:Handle.lvclass"] * 2
    index = build_link_index(named, ["AF Debug.lvlib", "Handle.lvclass"])
    assert index.records_of("Actor Framework.lvlib:Handle.lvclass") is None


def test_a_dependency_pane_is_read_by_its_exact_key(tmp_path: Path) -> None:
    """A class method no class lists is a stub keyed by its owning class; its
    leaf name is the caller's own. Every view reads the stub's pane by its key
    (none), never the caller's by name."""
    graph, vi = _load(tmp_path, "")
    owner = tmp_path / "Owner.lvclass"
    stub = f"Owner.lvclass@{owner}:T.vi"
    graph._add_stub(stub, NodeType.VI, "T.vi", "Owner.lvclass:T.vi", caller=vi)
    assert graph.pane_terminals(stub, "input") == []
    assert graph.vi_interface(stub) == ([], [], [], [])
    assert graph.key_label(stub) == "Owner.lvclass:T.vi"
    lvnet, describe, as_json = _views(graph, vi)
    assert "    subVI Owner.lvclass:T.vi\n" in lvnet
    assert {d["qualified"] for d in as_json["dependencies"]} == {
        "Owner.lvclass:T.vi"
    }
    assert describe.startswith("#")


def test_a_call_shows_the_name_the_graph_gives_its_callee(tmp_path: Path) -> None:
    """The caller records ``DAQmx C.lvclass:M.vi``; the callee's own file names
    its class ``Daqmx C.lvclass``: the call line shows the callee's name, a call
    with no callee in the graph the caller's record."""
    graph, vi = _load(tmp_path, "")
    callee = str(tmp_path / "M.vi")
    graph._add_stub(callee, NodeType.VI, "M.vi", "Daqmx C.lvclass:M.vi", caller=vi)
    call = VINode(
        id="c", vi_path=vi, name="M.vi", qualified_name="DAQmx C.lvclass:M.vi",
        callee_key=callee,
    )
    assert graph.call_qualified_name(call) == "Daqmx C.lvclass:M.vi"
    call.callee_key = None
    assert graph.call_qualified_name(call) == "DAQmx C.lvclass:M.vi"


def test_an_unloaded_own_class_is_named_by_its_file_on_disk(tmp_path: Path) -> None:
    """The VI's LIBN spells its class ``C.lvclass``; the class file beside it,
    which its ``VILB`` record names, is ``c.lvclass``: the VI is named by the
    file without the class being loaded."""
    (tmp_path / "c.lvclass").write_text("<LVClass />")
    graph, vi = _load(tmp_path, _LIBN + _LIVI)
    assert graph.vi_display_name(vi) == "Lib.lvlib:c.lvclass:T.vi"
    assert not any(graph.is_class(k) for k in graph._dep_graph if "c.lvclass" in k)
    describe = describe_vi(graph, vi)
    assert describe.startswith("# T_BDHb.xml\n")
