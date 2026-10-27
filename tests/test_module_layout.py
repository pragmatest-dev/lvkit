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


def _layout(
    *keys: str,
    root: str = "/home/me/proj",
    search_roots: tuple[str, ...] = (),
    shared: frozenset[str] = frozenset(),
    recorded: dict[str, str] | None = None,
) -> ModuleLayout:
    return ModuleLayout(
        _graph(*keys),
        list(keys),
        Path(root),
        [Path(r) for r in search_roots],
        shared,
        recorded or {},
    )


def test_modules_mirror_the_folders_below_the_project_folder() -> None:
    layout = _layout(
        "/home/me/proj/Classes/TestCase/run.vi",
        "/home/me/proj/Classes/TestSuite/run.vi",
        "/home/me/proj/Support Files/1D Helper-X.vi",
    )
    assert layout.places["/home/me/proj/Classes/TestCase/run.vi"] == ModulePlace(
        ("classes", "testcase"), "run"
    )
    assert layout.places["/home/me/proj/Classes/TestSuite/run.vi"] == ModulePlace(
        ("classes", "testsuite"), "run"
    )
    assert layout.places["/home/me/proj/Support Files/1D Helper-X.vi"] == ModulePlace(
        ("support_files",), "var_1d_helper_x"
    )


def test_what_else_was_loaded_does_not_move_a_module() -> None:
    vi = "/home/me/proj/a/x.vi"
    alone = _layout(vi)
    with_others = _layout(
        vi,
        "/home/me/other/deep/y.vi",
        "/somewhere/else/z.vi",
        search_roots=("/home/me/other",),
    )
    assert alone.places[vi] == with_others.places[vi] == ModulePlace(("a",), "x")


def test_a_folder_named_like_a_keyword_is_a_valid_package() -> None:
    layout = _layout("/home/me/proj/Class/ants/Import.vi")
    assert layout.places["/home/me/proj/Class/ants/Import.vi"] == ModulePlace(
        ("class_", "ants"), "import_"
    )


def test_files_that_are_not_vis_keep_their_extension_in_the_module_name() -> None:
    layout = _layout("/home/me/proj/a/Data.vi", "/home/me/proj/a/Data.ctl")
    assert layout.places["/home/me/proj/a/Data.vi"].module == "data"
    assert layout.places["/home/me/proj/a/Data.ctl"].module == "datactl"


def test_a_file_outside_the_project_is_placed_by_the_path_its_caller_recorded() -> None:
    key = "/lv/vi.lib/Utility/error.llb/Clear Errors.vi"
    layout = _layout(
        "/home/me/proj/a.vi",
        key,
        recorded={key: "<vilib>/Utility/error.llb/Clear Errors.vi"},
    )
    assert layout.places[key] == ModulePlace(
        ("vilib", "utility", "errorllb"), "clear_errors"
    )


def test_a_search_path_file_with_no_recorded_path_is_under_its_search_path() -> None:
    layout = _layout(
        "/home/me/proj/a.vi",
        "/home/me/lib/Sub/b.vi",
        search_roots=("/home/me/lib",),
    )
    assert layout.places["/home/me/lib/Sub/b.vi"] == ModulePlace(("lib", "sub"), "b")


def test_any_other_file_outside_the_project_is_under_external() -> None:
    layout = _layout("/home/me/proj/a.vi", "/home/me/other/Deep/c.vi")
    assert layout.places["/home/me/other/Deep/c.vi"] == ModulePlace(
        ("external", "other", "deep"), "c"
    )


def test_a_vi_with_a_recorded_path_and_no_file_is_placed_by_it() -> None:
    layout = _layout(
        "/home/me/proj/a.vi",
        "Clear Errors.vi",
        recorded={"Clear Errors.vi": "<vilib>/Utility/error.llb/Clear Errors.vi"},
    )
    assert layout.places["Clear Errors.vi"] == ModulePlace(
        ("vilib", "utility", "errorllb"), "clear_errors"
    )


def test_a_vi_with_no_file_and_no_recorded_path_is_unplaced_and_named() -> None:
    layout = _layout("/home/me/proj/a.vi", "Build Path__ogtk.vi")
    assert "Build Path__ogtk.vi" not in layout.places
    assert "Build Path__ogtk.vi" in str(layout.unplaced["Build Path__ogtk.vi"])


def test_polymorphic_variants_take_no_module_and_the_wrapper_goes_beside_one() -> None:
    wrap, variant = "/home/me/proj/a/Wrap.vi", "/home/me/proj/a/sub/Variant One.vi"
    layout = _layout(wrap, variant, shared=frozenset({variant}))
    assert variant not in layout.places
    place = layout.place_beside("Family.vi", [variant])
    assert place == ModulePlace(("a", "sub"), "family")


def test_two_files_that_transform_to_one_module_stop_generation_naming_both() -> None:
    with pytest.raises(ModuleCollisionError) as caught:
        _layout("/home/me/proj/Foo Bar/x.vi", "/home/me/proj/foo_bar/x.vi")
    assert "'/home/me/proj/Foo Bar/x.vi'" in str(caught.value)
    assert "'/home/me/proj/foo_bar/x.vi'" in str(caught.value)


def test_a_module_and_a_package_of_one_name_stop_generation() -> None:
    with pytest.raises(ModuleCollisionError, match="package of"):
        _layout("/home/me/proj/a.vi", "/home/me/proj/a/b.vi")


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


def test_an_absent_vi_is_placed_by_the_path_its_stub_records() -> None:
    key = "Flex-t'DAQmx AO.lvlib-Action.ctl'.lvlib:Read Event.vi"
    graph = _graph("/home/me/proj/a.vi", key)
    graph._dep_graph.add_node(  # noqa: SLF001
        key,
        path_tokens=["<extravilib>", "ChannelInstances", "Action.ctl", "Read Event.vi"],
    )
    layout = ModuleLayout(graph, ["/home/me/proj/a.vi", key], Path("/home/me/proj"))
    assert layout.places[key] == ModulePlace(
        ("extravilib", "channelinstances", "actionctl"), "read_event"
    )


def test_an_unplaceable_vi_fails_only_what_imports_it() -> None:
    layout = _layout("/home/me/proj/a.vi", "Missing.vi")
    assert "/home/me/proj/a.vi" in layout.places
    assert list(layout.unplaced) == ["Missing.vi"]
    with pytest.raises(UnplacedVIError, match="Missing.vi"):
        layout.place_of_name("Missing.vi")
