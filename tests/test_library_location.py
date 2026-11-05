"""A library file is placed and shown by its library path in every view, inside or
outside the project: module paths, documentation pages, messages and lvnet."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.docs.page_names import PageName, page_names
from lvkit.graph import InMemoryVIGraph, NodeType
from lvkit.graph.lvnet_typedef import render_lvnet_typedef
from lvkit.graph.netlist_build import _build_dependency_manifest
from lvkit.graph.render_lvnet import render_lvnet_named_types
from lvkit.graph.typedef import TypedefInfo, TypedefRef
from lvkit.library_folders import library_location
from lvkit.models import LVType, LVTypeKind
from lvkit.run_report import RunSettings


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repository that keeps a user.lib copy inside it."""
    root = tmp_path.resolve() / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "vendor" / "user.lib" / "_OpenG.lib" / "file").mkdir(parents=True)
    return root


def _userlib(project: Path) -> Path:
    return project / "vendor" / "user.lib"


def _graph(project: Path, *keys: str) -> InMemoryVIGraph:
    graph = InMemoryVIGraph()
    graph.set_library_roots(userlib_root=_userlib(project))
    for key in keys:
        graph._vi_nodes[key] = set()  # noqa: SLF001
    return graph


def test_the_recorded_installation_path_comes_first(tmp_path: Path) -> None:
    roots = [("<userlib>", tmp_path / "user.lib")]
    file = tmp_path / "user.lib" / "a" / "X.vi"
    assert library_location(file, roots, "<vilib>/b/X.vi") == ("<vilib>", "b", "X.vi")
    assert library_location(file, roots, "../a/X.vi") == ("<userlib>", "a", "X.vi")
    assert library_location(None, roots, "<vilib>/b/X.vi") == ("<vilib>", "b", "X.vi")
    assert library_location(tmp_path / "proj" / "X.vi", roots) is None
    assert library_location(None, roots) is None


def test_a_message_shows_a_library_file_inside_the_project_by_its_library_path(
    project: Path,
) -> None:
    vi = _userlib(project) / "_OpenG.lib" / "file" / "Delete.vi"
    roots = {"<userlib>": (_userlib(project),)}
    settings = RunSettings(root=project, library_roots=roots)
    assert settings.label(vi) == "<userlib>/_OpenG.lib/file/Delete.vi"
    assert settings.label(project / "sub" / "A.vi") == "sub/A.vi"


def test_a_library_vi_inside_the_project_has_a_user_lib_page(project: Path) -> None:
    lib_vi = _userlib(project) / "_OpenG.lib" / "file" / "Delete.vi"
    own_vi = project / "sub" / "A.vi"
    graph = _graph(project, str(lib_vi), str(own_vi))
    sources = {str(lib_vi): lib_vi, str(own_vi): own_vi}
    graph._source_paths.update(sources)  # noqa: SLF001
    names = page_names(graph, [str(lib_vi), str(own_vi)], project)
    assert names[str(lib_vi)] == PageName("user.lib", "OpenG.lib_file_Delete")
    assert names[str(own_vi)] == PageName("Project", "sub_A")


def test_an_outside_vi_page_is_under_its_own_project_folder(
    project: Path, tmp_path: Path
) -> None:
    libs = tmp_path.resolve() / "elsewhere" / "libs"
    libs.mkdir(parents=True)
    (libs / "Libs.lvproj").write_text("")
    helper = libs / "Helper.vi"
    graph = _graph(project, str(helper))
    graph._source_paths[str(helper)] = helper  # noqa: SLF001
    assert page_names(graph, [str(helper)], project)[str(helper)] == PageName(
        "libs", "Helper"
    )


def test_an_lvnet_uses_line_shows_a_library_file_by_its_library_path(
    project: Path,
) -> None:
    caller = str(project / "Caller.vi")
    lib_dep = str(_userlib(project) / "_OpenG.lib" / "file" / "Delete.vi")
    own_dep = str(project / "sub" / "Own.vi")
    graph = _graph(project, caller)
    for dep in (lib_dep, own_dep):
        graph._dep_graph.add_edge(caller, dep)  # noqa: SLF001
    paths = {d.qualified: d.path for d in _build_dependency_manifest(graph, caller)}
    assert paths == {
        "Delete.vi": "<userlib>/_OpenG.lib/file/Delete.vi",
        "Own.vi": "./sub/Own.vi",
    }


def test_a_ctl_uses_line_and_types_footnote_show_library_paths() -> None:
    info = TypedefInfo(
        key="/p/Cfg.ctl",
        name="Cfg.ctl",
        root_type=LVType(LVTypeKind.PRIMITIVE, underlying_type="DBL"),
        default=None,
        fields=(),
        uses=(
            TypedefRef(
                NodeType.TYPEDEF,
                "Dir.ctl",
                "/lv/vi.lib/Utility/Dir.ctl",
                ("<vilib>", "Utility", "Dir.ctl"),
            ),
        ),
        owned_by=(),
        used_by=(),
    )
    lines = render_lvnet_typedef(info).splitlines()
    assert lines[2].split() == ["typedef", "Dir.ctl", ";", "<vilib>/Utility/Dir.ctl"]
    footnote: list[str] = []
    render_lvnet_named_types(
        {
            "Dir": LVType(
                LVTypeKind.ENUM, typedef_name="Dir", typedef_path="<vilib>/U/Dir.ctl"
            ),
            "Own": LVType(LVTypeKind.ENUM, typedef_name="Own", typedef_path="Own.ctl"),
        },
        footnote,
    )
    assert footnote[1].endswith("; <vilib>/U/Dir.ctl")
    assert footnote[2].endswith("; ./Own.ctl")


def test_an_ambiguous_dependency_names_a_library_candidate_by_its_library_path(
    project: Path,
) -> None:
    graph = _graph(project)
    lib_vi = _userlib(project) / "_OpenG.lib" / "file" / "Delete.vi"
    caller = project / "Caller.vi"
    shown = graph._display_path  # noqa: SLF001
    assert shown(lib_vi, caller) == "<userlib>/_OpenG.lib/file/Delete.vi"
    assert shown(project / "sub" / "Own.vi", caller) == "./sub/Own.vi"
