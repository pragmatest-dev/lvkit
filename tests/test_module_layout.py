"""The generated package mirrors the project's folders: a VI's module path is its
path below the project folder, each part put through one transform."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.cache_paths import project_root, store_root
from lvkit.codegen.ast_utils import module_part
from lvkit.graph import InMemoryVIGraph
from lvkit.module_layout import (
    ModuleCollisionError,
    ModuleLayout,
    ModulePlace,
    UnplacedVIError,
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
    userlib_root: Path | None = None,
    shared: frozenset[str] = frozenset(),
    recorded: dict[str, str] | None = None,
) -> ModuleLayout:
    graph = _graph(*keys)
    if userlib_root is not None:
        graph.set_library_roots(userlib_root=userlib_root)
    return ModuleLayout(graph, list(keys), root, shared, recorded or {})


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
    with_others = _layout(vi, other, root=tree.proj, userlib_root=tree.base / "other")
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


def test_a_library_root_file_with_no_recorded_path_is_placed_as_recorded_there(
    tree: _Tree,
) -> None:
    """A file under the configured user.lib with no recorded path is placed as if
    recorded as ``<userlib>/...`` — the same package its recorded path gives."""
    key = tree.key("user.lib", "Sub", "b.vi", under=tree.base)
    layout = _layout(
        tree.key("a.vi"), key, root=tree.proj, userlib_root=tree.base / "user.lib"
    )
    assert layout.places[key] == ModulePlace(("userlib", "sub"), "b")


def test_any_other_file_outside_the_project_is_under_external_by_its_own_project(
    tree: _Tree,
) -> None:
    """Below ``external``: the name of the file's own project folder and its path
    below it, never a folder above that tree."""
    libs = tree.base / "Users" / "someone" / "Documents" / "libs"
    (libs / "Sub").mkdir(parents=True)
    (libs / "Libs.lvproj").write_text("")
    key = tree.key("Sub", "Helper.vi", under=libs)
    layout = _layout(tree.key("a.vi"), key, root=tree.proj)
    assert layout.places[key] == ModulePlace(("external", "libs", "sub"), "helper")


def test_an_outside_file_with_no_project_marker_is_under_its_own_folder(
    tree: _Tree,
) -> None:
    key = tree.key("other", "Deep", "c.vi", under=tree.base)
    layout = _layout(tree.key("a.vi"), key, root=tree.proj)
    assert layout.places[key] == ModulePlace(("external", "deep"), "c")


def test_a_git_repo_at_the_home_folder_is_not_an_outside_files_project(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A home folder kept in git (dotfiles) never puts the user's name in a
    module path."""
    home = tree.base / "home" / "someone"
    (home / ".git").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    key = tree.key("Documents", "libs", "Helper.vi", under=home)
    layout = _layout(tree.key("a.vi"), key, root=tree.proj)
    assert layout.places[key] == ModulePlace(("external", "libs"), "helper")


def test_outside_projects_in_one_git_repo_are_placed_by_the_repo(
    tree: _Tree,
) -> None:
    """Two ``.lvproj`` folders of one name inside one git repository (the
    measurement-plugin generated APIs, each ``.../v1``) give unique modules: an
    outside file's project is its repository, not the nearer ``.lvproj``."""
    repo = tree.base / "plugin"
    (repo / ".git").mkdir(parents=True)
    keys = []
    for service in ("sessionmanagement", "discovery"):
        v1 = repo / "gRPC" / service / "v1"
        (v1 / "RPC Messages").mkdir(parents=True)
        (v1 / f"{service}.lvproj").write_text("")
        keys.append(tree.key("RPC Messages", "Register gRPC Messages.vi", under=v1))
    layout = _layout(tree.key("a.vi"), *keys, root=tree.proj)
    assert [layout.places[k] for k in keys] == [
        ModulePlace(
            ("external", "plugin", "grpc", service, "v1", "rpc_messages"),
            "register_grpc_messages",
        )
        for service in ("sessionmanagement", "discovery")
    ]


def test_two_outside_trees_that_give_one_module_stop_generation(tree: _Tree) -> None:
    first = tree.key("a", "libs", "Helper.vi", under=tree.base)
    second = tree.key("b", "libs", "Helper.vi", under=tree.base)
    with pytest.raises(ModuleCollisionError, match="external.libs.helper"):
        _layout(tree.key("x.vi"), first, second, root=tree.proj)


def test_a_library_folder_inside_the_project_places_its_files_as_library_files(
    tree: _Tree,
) -> None:
    """A user.lib kept inside the project (a vendored copy in a git repo) is still
    user.lib: its files are ``userlib.*``, the same as anywhere else."""
    userlib = tree.proj / "vendor" / "user.lib"
    key = tree.key("_OpenG.lib", "file", "file.llb", "Delete.vi", under=userlib)
    layout = _layout(tree.key("a.vi"), key, root=tree.proj, userlib_root=userlib)
    assert layout.places[key] == ModulePlace(
        ("userlib", "_openglib", "file", "filellb"), "delete"
    )
    assert layout.places[tree.key("a.vi")] == ModulePlace((), "a")
    assert layout.display_path(key) == "<userlib>/_OpenG.lib/file/file.llb/Delete.vi"
    assert layout.placed_folder(userlib / "_OpenG.lib") == ("userlib", "_openglib")
    assert layout.placed_folder(tree.proj / "sub") == ("sub",)


def test_a_recorded_installation_path_places_a_file_inside_the_project(
    tree: _Tree,
) -> None:
    key = tree.key("copies", "Build Path__ogtk.vi")
    layout = _layout(
        tree.key("a.vi"),
        key,
        root=tree.proj,
        recorded={key: "<userlib>/_OpenG.lib/file/file.llb/Build Path__ogtk.vi"},
    )
    assert layout.places[key] == ModulePlace(
        ("userlib", "_openglib", "file", "filellb"), "build_path__ogtk"
    )


def test_a_user_lib_vi_generated_alone_and_its_dependencies_share_one_tree(
    tree: _Tree,
) -> None:
    """A user.lib VI given as the input, with no project marker above it: its
    project is its own folder, yet it and the user.lib files it calls are all
    placed under ``userlib``, never half at the top and half below."""
    userlib = tree.base / "user.lib"
    top = tree.key("_OpenG.lib", "file", "file.llb", "Top.vi", under=userlib)
    dep = tree.key("_OpenG.lib", "array", "array.llb", "Dep.vi", under=userlib)
    layout = _layout(top, dep, root=Path(top).parent, userlib_root=userlib)
    assert layout.places[top] == ModulePlace(
        ("userlib", "_openglib", "file", "filellb"), "top"
    )
    assert layout.places[dep] == ModulePlace(
        ("userlib", "_openglib", "array", "arrayllb"), "dep"
    )


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
    # named by their project-relative paths, never absolute ones
    assert repr("Foo Bar/x.vi") in str(caught.value)
    assert repr("foo_bar/x.vi") in str(caught.value)
    assert str(tree.proj) not in str(caught.value)


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


def test_a_lvproj_folder_inside_a_git_repo_is_the_project_but_the_repo_is_the_store(
    tmp_path: Path,
) -> None:
    """A git repo whose ``.lvproj`` is in a subfolder: generated modules and every
    shown name are relative to the ``.lvproj`` folder (the project); the cache and
    the index are keyed by the repo (the store)."""
    (tmp_path / ".git").mkdir()
    labview = tmp_path / "LabVIEW"
    (labview / "sub").mkdir(parents=True)
    (labview / "My.lvproj").write_text("<Project/>")
    vi = labview / "sub" / "A.vi"
    vi.write_text("")
    assert project_root(vi) == labview.resolve()
    assert store_root(vi) == tmp_path.resolve()


def test_a_git_repo_inside_a_store_is_its_own_project_but_shares_the_store(
    tmp_path: Path,
) -> None:
    (tmp_path / ".lvkit").mkdir()
    vendored = tmp_path / "vendor" / "lib"
    (vendored / ".git").mkdir(parents=True)
    vi = vendored / "x.vi"
    vi.write_text("")
    assert project_root(vi) == vendored.resolve()
    assert store_root(vi) == tmp_path.resolve()


def test_a_git_repo_at_or_above_the_home_folder_is_not_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path.resolve() / "home" / "someone"
    (home / ".git").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    vi = home / "Documents" / "proj" / "x.vi"
    vi.parent.mkdir(parents=True)
    vi.write_text("")
    assert project_root(vi) == vi.parent
    (vi.parent / ".git").mkdir()
    assert project_root(vi) == vi.parent


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
