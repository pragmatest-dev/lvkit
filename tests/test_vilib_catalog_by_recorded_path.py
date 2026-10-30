"""The vi.lib catalog describes a call only when the call records a path in a
LabVIEW installation folder; a project VI that shares a catalog VI's file name
keeps its own terminals and is not treated as an error VI."""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.error_path import is_error_wire_vi
from lvkit.codegen.nodes import subvi
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VINode
from lvkit.vilib_resolver import get_resolver
from tests import test_error_cluster_from_error_code as base

VILIB_PATH = "<vilib>/Utility/error.llb/General Error Handler.vi"
PROJECT_PATH = "/Helpers/General Error Handler.vi"


def test_catalog_entry_needs_an_installation_folder_path() -> None:
    resolver = get_resolver()
    entry = resolver.resolve_recorded(VILIB_PATH)
    assert entry is not None and entry.name == "General Error Handler"
    assert resolver.resolve_recorded(PROJECT_PATH) is None
    assert resolver.resolve_recorded("General Error Handler.vi") is None
    assert resolver.resolve_recorded(None) is None
    # An installation folder other than vi.lib (OpenG lives in user.lib).
    ogtk = "<userlib>/_OpenG.lib/file/file.llb/File Exists - Scalar__ogtk.vi"
    assert resolver.resolve_recorded(ogtk) is not None


def test_error_vi_needs_the_vilib_path() -> None:
    resolver = get_resolver()
    merge = "Merge Errors.vi"
    assert is_error_wire_vi(f"<vilib>/Utility/error.llb/{merge}", resolver)
    assert not is_error_wire_vi(f"/Helpers/{merge}", resolver)
    assert not is_error_wire_vi(None, resolver)


def test_project_merge_errors_is_a_call_of_its_own_module() -> None:
    def merge(path: str) -> VINode:
        return VINode(
            id="w",
            vi_path="caller.vi",
            name="Merge Errors.vi",
            node_type="iUse",
            qualified_path=path,
            callee_key="/proj/Helpers/Merge Errors.vi",
        )

    vilib = subvi.generate(
        merge("<vilib>/Utility/error.llb/Merge Errors.vi"), CodeGenContext()
    )
    assert vilib.statements == []
    project = subvi.generate(merge("/Helpers/Merge Errors.vi"), CodeGenContext())
    assert [ast.unparse(ast.fix_missing_locations(s)) for s in project.statements] == [
        "merge_errors_result = merge_errors()"
    ]


def _terminal_names(tmp_path: Path, tokens: list[str]) -> list[str | None]:
    strings = "".join(f"<String>{t}</String>" for t in tokens)
    main = base._MAIN_XML.replace(
        "<String>&lt;vilib&gt;</String>\n            <String>Utility</String>\n"
        "            <String>error.llb</String>\n"
        "            <String>Error Cluster From Error Code.vi</String>",
        strings,
    )
    assert main != base._MAIN_XML
    (tmp_path / "Caller_BDHb.xml").write_text(base._BD_XML)
    (tmp_path / "Caller.xml").write_text(main)
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Caller_BDHb.xml")
    vi = graph.list_vis()[0]
    node = graph._graph.nodes[f"{vi}::20"]["node"]
    return [t.display_name for t in sorted(node.terminals, key=lambda t: t.index)]


def test_call_terminals_come_from_the_catalog_only_for_a_vilib_path(
    tmp_path: Path,
) -> None:
    vilib_dir, project_dir = tmp_path / "vilib", tmp_path / "project"
    vilib_dir.mkdir()
    project_dir.mkdir()
    from_vilib = _terminal_names(
        vilib_dir,
        ["&lt;vilib&gt;", "Utility", "error.llb", "Error Cluster From Error Code.vi"],
    )
    assert "error code (0)" in from_vilib
    from_project = _terminal_names(
        project_dir, ["", "Helpers", "Error Cluster From Error Code.vi"]
    )
    assert from_project == [None] * len(from_project)


def test_resolve_call_picks_the_selected_variant_by_the_recorded_path() -> None:
    resolver = get_resolver()
    path = "<vilib>/DAQmx/read.llb/DAQmx Read.vi"
    selector = "Digital 1D Bool\nNChan 1Samp"
    variant = resolver.resolve_call(path, selector)
    assert variant is not None
    assert variant.name == "DAQmx Read (Digital 1D Bool NChan 1Samp).vi"
    wrapper = resolver.resolve_call(path, None)
    assert wrapper is not None and wrapper.name != variant.name
    # A variant the catalog does not list: the wrapper, unless a variant is needed.
    assert resolver.resolve_call(path, "no such variant") is wrapper
    assert (
        resolver.resolve_call(path, "no such variant", wrapper_fallback=False) is None
    )
    # A project VI of the same file name is not the catalog's.
    assert resolver.resolve_call("/Helpers/DAQmx Read.vi", selector) is None
    assert resolver.resolve_call(None, selector) is None


def test_call_relative_to_a_vilib_caller_gets_the_catalog_terminals(
    tmp_path: Path,
) -> None:
    """The call records ``['', 'Error Cluster From Error Code.vi']``: beside its
    caller, which the project records under ``<vilib>/Utility/error.llb``."""
    main = base._MAIN_XML.replace(
        "<String>&lt;vilib&gt;</String>\n            <String>Utility</String>\n"
        "            <String>error.llb</String>\n"
        "            <String>Error Cluster From Error Code.vi</String>",
        "<String />\n<String>Error Cluster From Error Code.vi</String>",
    )
    assert main != base._MAIN_XML
    (tmp_path / "Caller_BDHb.xml").write_text(base._BD_XML)
    (tmp_path / "Caller.xml").write_text(main)

    def names(caller_path: list[str] | None) -> list[str | None]:
        graph = InMemoryVIGraph()
        if caller_path is not None:
            key = str((tmp_path / "Caller_BDHb.xml").resolve())
            graph._recorded.record(key, None, caller_path)
        graph.load_vi(tmp_path / "Caller_BDHb.xml")
        vi = graph.list_vis()[0]
        node = graph._graph.nodes[f"{vi}::20"]["node"]
        return [t.display_name for t in sorted(node.terminals, key=lambda t: t.index)]

    in_vilib = names(["<vilib>", "Utility", "error.llb", "Caller.vi"])
    assert "error code (0)" in in_vilib
    assert names(None) == [None] * len(in_vilib)
