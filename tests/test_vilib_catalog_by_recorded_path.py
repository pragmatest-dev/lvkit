"""The vi.lib catalog describes a call only when the call records a path in a
LabVIEW installation folder; a project VI that shares a catalog VI's file name
keeps its own terminals and is not treated as an error VI."""

from __future__ import annotations

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
    assert len(project.statements) == 1


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
