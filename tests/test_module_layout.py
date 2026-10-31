"""The generated package mirrors the project's folders: a VI's module path is its
path below the project folder, each part put through one transform."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.ast_utils import module_part
from lvkit.graph import InMemoryVIGraph
from lvkit.module_layout import (
    ModuleCollisionError,
    ModuleLayout,
    ModulePlace,
    UnplacedVIError,
    project_root,
)


def _graph(*keys: str) -> InMemoryVIGraph:
    graph = InMemoryVIGraph()
    for key in keys:
        graph._vi_nodes[key] = set()  # noqa: SLF001
    return graph


class _Tree:
    """Absolute paths under a temporary folder, valid on any platform."""

    def __init__(self, base: Path) -> None:
        self.base = base.resolve()
        self.proj = self.base / "proj"

    def key(self, *parts: str, under: Path | None = None) -> str:
        return str((under or self.proj).joinpath(*parts))


@pytest.fixture
def tree(tmp_path: Path) -> _Tree:
    return _Tree(tmp_path)


def _layout(
    *keys: str,
    root: Path,
    search_roots: tuple[Path, ...] = (),
    shared: frozenset[str] = frozenset(),
    recorded: dict[str, str] | None = None,
) -> ModuleLayout:
    return ModuleLayout(
        _graph(*keys),
        list(keys),
        root,
        list(search_roots),
        shared,
        recorded or {},
    )


def test_modules_mirror_the_folders_below_the_project_folder(tree: _Tree) -> None:
    case = tree.key("Classes", "TestCase", "run.vi")
    suite = tree.key("Classes", "TestSuite", "run.vi")
    helper = tree.key("Support Files", "1D Helper-X.vi")
    layout = _layout(case, suite, helper, root=tree.proj)
    assert layout.places[case] == ModulePlace(("classes", "testcase"), "run")
    assert layout.places[suite] == ModulePlace(("classes", "testsuite"), "run")
    assert layout.places[helper] == ModulePlace(("support_files",), "var_1d_helper_x")


def test_what_else_was_loaded_does_not_move_a_module(tree: _Tree) -> None:
    vi = tree.key("a", "x.vi")
    other = tree.key("other", "deep", "y.vi", under=tree.base)
    alone = _layout(vi, root=tree.proj)
    with_others = _layout(
        vi, other, root=tree.proj, search_roots=(tree.base / "other",)
    )
    assert alone.places[vi] == with_others.places[vi] == ModulePlace(("a",), "x")


def test_files_that_are_not_vis_keep_their_extension_in_the_module_name(
    tree: _Tree,
) -> None:
    vi, ctl = tree.key("a", "Data.vi"), tree.key("a", "Data.ctl")
    layout = _layout(vi, ctl, root=tree.proj)
    assert layout.places[vi].module == "data"
    assert layout.places[ctl].module == "datactl"


def test_a_folder_named_like_a_keyword_is_a_valid_package(tree: _Tree) -> None:
    key = tree.key("Class", "ants", "Import.vi")
    layout = _layout(key, root=tree.proj)
    assert layout.places[key] == ModulePlace(("class_", "ants"), "import_")


def test_a_file_outside_the_project_is_placed_by_the_path_its_caller_recorded(
    tree: _Tree,
) -> None:
    key = tree.key(
        "lv", "vi.lib", "Utility", "error.llb", "Clear Errors.vi", under=tree.base
    )
    layout = _layout(
        tree.key("a.vi"),
        key,
        root=tree.proj,
        recorded={key: "<vilib>/Utility/error.llb/Clear Errors.vi"},
    )
    assert layout.places[key] == ModulePlace(
        ("vilib", "utility", "errorllb"), "clear_errors"
    )


def test_a_search_path_file_with_no_recorded_path_is_under_its_search_path(
    tree: _Tree,
) -> None:
    key = tree.key("lib", "Sub", "b.vi", under=tree.base)
    layout = _layout(
        tree.key("a.vi"), key, root=tree.proj, search_roots=(tree.base / "lib",)
    )
    assert layout.places[key] == ModulePlace(("lib", "sub"), "b")


def test_any_other_file_outside_the_project_is_under_external(tree: _Tree) -> None:
    key = tree.key("other", "Deep", "c.vi", under=tree.base)
    layout = _layout(tree.key("a.vi"), key, root=tree.proj)
    assert layout.places[key] == ModulePlace(("external", "other", "deep"), "c")


def test_a_vi_with_a_recorded_path_and_no_file_is_placed_by_it(tree: _Tree) -> None:
    layout = _layout(
        tree.key("a.vi"),
        "Clear Errors.vi",
        root=tree.proj,
        recorded={"Clear Errors.vi": "<vilib>/Utility/error.llb/Clear Errors.vi"},
    )
    assert layout.places["Clear Errors.vi"] == ModulePlace(
        ("vilib", "utility", "errorllb"), "clear_errors"
    )


def test_a_vi_with_no_file_and_no_recorded_path_is_unplaced_and_named(
    tree: _Tree,
) -> None:
    layout = _layout(tree.key("a.vi"), "Build Path__ogtk.vi", root=tree.proj)
    assert "Build Path__ogtk.vi" not in layout.places
    assert "Build Path__ogtk.vi" in str(layout.unplaced["Build Path__ogtk.vi"])


def test_polymorphic_variants_take_no_module_and_the_wrapper_goes_beside_one(
    tree: _Tree,
) -> None:
    wrap, variant = tree.key("a", "Wrap.vi"), tree.key("a", "sub", "Variant One.vi")
    layout = _layout(wrap, variant, root=tree.proj, shared=frozenset({variant}))
    assert variant not in layout.places
    place = layout.place_beside("Family.vi", [variant])
    assert place == ModulePlace(("a", "sub"), "family")


def test_two_files_that_transform_to_one_module_stop_generation_naming_both(
    tree: _Tree,
) -> None:
    first, second = tree.key("Foo Bar", "x.vi"), tree.key("foo_bar", "x.vi")
    with pytest.raises(ModuleCollisionError) as caught:
        _layout(first, second, root=tree.proj)
    assert repr(first) in str(caught.value)
    assert repr(second) in str(caught.value)


def test_a_module_and_a_package_of_one_name_stop_generation(tree: _Tree) -> None:
    with pytest.raises(ModuleCollisionError, match="package of"):
        _layout(tree.key("a.vi"), tree.key("a", "b.vi"), root=tree.proj)


def test_the_project_is_the_nearest_folder_with_a_project_marker(
    tmp_path: Path,
) -> None:
    (tmp_path / "A.lvproj").write_text("")
    vi = tmp_path / "src" / "deep" / "x.vi"
    vi.parent.mkdir(parents=True)
    vi.write_text("")
    assert project_root(vi) == tmp_path.resolve()
    inner = tmp_path / "src" / ".git"
    inner.mkdir()
    assert project_root(vi) == (tmp_path / "src").resolve()


def test_with_no_marker_the_project_is_the_vis_own_folder(tmp_path: Path) -> None:
    vi = tmp_path / "x.vi"
    vi.write_text("")
    assert project_root(vi) == tmp_path.resolve()


@pytest.mark.parametrize(
    "name", ["Run", "1D Array", "a b-c", "__x", "Ünï.cödé", "Foo.llb", "-", "9"]
)
def test_the_transform_is_valid_python_for_awkward_names(name: str) -> None:
    assert module_part(name).isidentifier()


def test_an_absent_vi_is_placed_by_the_path_its_stub_records(tree: _Tree) -> None:
    key = "Flex-t'DAQmx AO.lvlib-Action.ctl'.lvlib:Read Event.vi"
    graph = _graph(tree.key("a.vi"), key)
    graph._dep_graph.add_node(key)  # noqa: SLF001
    graph._recorded.record(  # noqa: SLF001
        key,
        None,
        ["<extravilib>", "ChannelInstances", "Action.ctl", "Read Event.vi"],
    )
    layout = ModuleLayout(graph, [tree.key("a.vi"), key], tree.proj)
    assert layout.places[key] == ModulePlace(
        ("extravilib", "channelinstances", "actionctl"), "read_event"
    )


def test_an_unplaceable_vi_fails_only_what_imports_it(tree: _Tree) -> None:
    layout = _layout(tree.key("a.vi"), "Missing.vi", root=tree.proj)
    assert tree.key("a.vi") in layout.places
    assert list(layout.unplaced) == ["Missing.vi"]
    with pytest.raises(UnplacedVIError, match="Missing.vi"):
        layout.place_of_name("Missing.vi")
