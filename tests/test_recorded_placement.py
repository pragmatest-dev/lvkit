"""A loaded file is placed by the installation-folder path its caller recorded,
wherever it was found; a path relative to the caller never places a loaded file.
Load order does not matter."""

from __future__ import annotations

import shutil
from pathlib import Path

from lvkit.docs.generate import _collect_library_groups
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.recorded_paths import resolve_against
from lvkit.module_layout import ModuleLayout
from lvkit.parser.models import ParsedDependencyRef

ISSUES = Path(__file__).parent / "corpus" / "issues"
MAIN = ISSUES / "39" / "zorder-not-respected.vi"
ONE = ISSUES / "30" / "visible-frame-case-sequence.vi"
TWO = ISSUES / "33" / "coercion-dot-consistency.vi"
THREE = ISSUES / "31" / "disable-structure-frame-names.vi"
CLASS_DIR = ISSUES / "29" / "Test LVKit" / "Lib1" / "Class"


def _ref(name: str, *tokens: str) -> ParsedDependencyRef:
    return ParsedDependencyRef(name=name, path_tokens=list(tokens))


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "proj"
    (project / ".lvkit").mkdir(parents=True)
    return project, Path(shutil.copy(MAIN, project / "Main.vi"))


def _shared_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    shared = tmp_path / "Shared"
    shared.mkdir()
    return (
        shared,
        Path(shutil.copy(ONE, shared / "Y.vi")),
        Path(shutil.copy(TWO, shared / "X.vi")),
    )


def _load_pair(tmp_path: Path, sibling_first: bool) -> dict[str, object]:
    """Y (beside X in Shared) records X relative to itself; Main records Y and X
    relative to Main. The two callers record X in either order."""
    project, main = _project(tmp_path)
    shared, y, x = _shared_pair(tmp_path)
    graph = InMemoryVIGraph()
    main_key = graph.load_vi(main, mode=LoadMode.MINIMAL)

    def load(name: str, ref: ParsedDependencyRef, caller: Path, caller_key: str) -> str:
        key = graph._load_dependency(
            name,
            ref,
            caller,
            [shared],
            caller_qname=caller_key,
            mode=LoadMode.MINIMAL,
        )
        assert key is not None
        return key

    y_key = load("Y.vi", _ref("Y.vi", "", "", "Shared", "Y.vi"), main, main_key)

    def beside() -> None:
        load("X.vi", _ref("X.vi", "", "X.vi"), y, y_key)

    def from_main() -> None:
        load("X.vi", _ref("X.vi", "", "", "Shared", "X.vi"), main, main_key)

    for step in (beside, from_main) if sibling_first else (from_main, beside):
        step()
    x_key = str(x.resolve())
    layout = ModuleLayout(graph, [main_key, x_key, y_key], project, [shared])
    groups, _ = _collect_library_groups(graph, [main_key], project, [shared])
    return {
        "recorded": graph.recorded_path(x_key),
        "place": layout.places[x_key],
        "folders": layout.folders_of(x_key),
        "group": groups[x_key],
    }


def test_a_path_relative_to_the_caller_never_places_a_loaded_file(
    tmp_path: Path,
) -> None:
    first = _load_pair(tmp_path / "a", sibling_first=True)
    second = _load_pair(tmp_path / "b", sibling_first=False)
    assert first == second
    assert first["recorded"] is None
    assert first["group"] == "Shared"
    assert first["folders"] == ("Shared",)
    assert first["place"].packages == ("shared",)  # type: ignore[attr-defined]


def test_a_file_found_through_a_search_path_is_placed_by_its_installation_path(
    tmp_path: Path,
) -> None:
    project, main = _project(tmp_path)
    search = tmp_path / "extracted"
    search.mkdir()
    shutil.copy(THREE, search / "Lib.vi")
    graph = InMemoryVIGraph()
    main_key = graph.load_vi(main, mode=LoadMode.MINIMAL)
    key = graph._load_dependency(
        "Lib.vi",
        _ref("Lib.vi", "<userlib>", "_OpenG.lib", "lib.llb", "Lib.vi"),
        main,
        [search],
        caller_qname=main_key,
        mode=LoadMode.MINIMAL,
    )
    assert key is not None and not graph.is_stub(key)
    layout = ModuleLayout(graph, [main_key, key], project, [search])
    assert layout.places[key].parts == ("userlib", "_openglib", "libllb", "lib")
    assert layout.folders_of(key) == ("<userlib>", "_OpenG.lib", "lib.llb")
    groups, _ = _collect_library_groups(graph, [main_key], project, [search])
    assert groups[key] == "user.lib"


def test_a_class_and_its_members_are_placed_in_one_package(tmp_path: Path) -> None:
    project, main = _project(tmp_path)
    vilib = tmp_path / "vilib"
    shutil.copytree(CLASS_DIR, vilib / "Foo")
    graph = InMemoryVIGraph()
    graph.set_library_roots(vilib_root=vilib)
    main_key = graph.load_vi(main, mode=LoadMode.FULL)
    ref = _ref("Class.lvclass", "<vilib>", "Foo", "Class.lvclass")
    class_keys = {
        graph._load_dependency(
            "Class.lvclass",
            ref,
            main,
            [],
            caller_qname=main_key,
            mode=LoadMode.FULL,
        )
        for _ in range(2)
    }
    (class_key,) = class_keys
    assert class_key is not None
    assert graph.recorded_path(class_key) == "<vilib>/Foo/Class.lvclass"
    members = graph._class_member_keys(class_key)
    assert members
    for member in members:
        recorded = graph.recorded_path(member)
        assert recorded == f"<vilib>/Foo/{Path(member).name}"
    layout = ModuleLayout(graph, [main_key, class_key, *members], project)
    packages = layout.packages_of(class_key)
    assert packages == ("vilib", "foo")
    assert {layout.places[m].packages for m in members} == {packages}


def test_absent_members_of_one_class_are_not_placed_at_the_class_file(
    tmp_path: Path,
) -> None:
    project, main = _project(tmp_path)
    graph = InMemoryVIGraph()
    main_key = graph.load_vi(main, mode=LoadMode.MINIMAL)
    tokens = ["<vilib>", "X", "Foo.lvclass"]
    for member in ("m.vi", "n.vi"):
        graph._load_dependency(
            f"Foo.lvclass:{member}",
            _ref(member, *tokens),
            main,
            [],
            caller_qname=main_key,
            mode=LoadMode.MINIMAL,
        )
    stubs = sorted(graph._stubs)
    assert [graph.recorded_path(s) for s in stubs] == [
        "<vilib>/X/m.vi",
        "<vilib>/X/n.vi",
    ]
    layout = ModuleLayout(graph, [main_key, *stubs], project)
    assert [layout.places[s].parts for s in stubs] == [
        ("vilib", "x", "m"),
        ("vilib", "x", "n"),
    ]


_LLB = ["<userlib>", "_OpenG.lib", "array", "array.llb"]


def _sibling_graph(tmp_path: Path, order: int) -> tuple[InMemoryVIGraph, str, str, str]:
    """A and B sit in one <userlib> llb found through a search path. Main records A
    by its <userlib> path; A records B beside itself; Main may record B by its
    <userlib> path too. The three records arrive in the order ``order`` picks."""
    project, main = _project(tmp_path)
    search = tmp_path / "extracted"
    search.mkdir()
    a = Path(shutil.copy(ONE, search / "A.vi"))
    Path(shutil.copy(TWO, search / "B.vi"))
    graph = InMemoryVIGraph()
    main_key = graph.load_vi(main, mode=LoadMode.MINIMAL)

    def load(name: str, tokens: list[str], caller: Path, caller_key: str) -> str:
        key = graph._load_dependency(
            name,
            _ref(name, *tokens),
            caller,
            [search],
            caller_qname=caller_key,
            mode=LoadMode.MINIMAL,
        )
        assert key is not None
        return key

    a_key = str(a.resolve())
    if order == 1:
        graph.load_vi(a, mode=LoadMode.MINIMAL)
        b_key = load("B.vi", ["", "B.vi"], a, a_key)
        load("A.vi", [*_LLB, "A.vi"], main, main_key)
    elif order == 2:
        load("A.vi", [*_LLB, "A.vi"], main, main_key)
        b_key = load("B.vi", ["", "B.vi"], a, a_key)
    else:
        load("A.vi", [*_LLB, "A.vi"], main, main_key)
        b_key = load("B.vi", [*_LLB, "B.vi"], main, main_key)
        load("B.vi", ["", "B.vi"], a, a_key)
    return graph, main_key, a_key, b_key


def test_a_sibling_call_in_an_installation_folder_is_placed_there_in_any_order(
    tmp_path: Path,
) -> None:
    for order in (1, 2, 3):
        graph, main_key, a_key, b_key = _sibling_graph(tmp_path / str(order), order)
        project = tmp_path / str(order) / "proj"
        assert graph.recorded_path(b_key) == "/".join([*_LLB, "B.vi"])
        layout = ModuleLayout(
            graph,
            [main_key, a_key, b_key],
            project,
            [tmp_path / str(order) / "extracted"],
        )
        assert layout.places[a_key].packages == layout.places[b_key].packages
        assert layout.places[b_key].parts == (
            "userlib",
            "_openglib",
            "array",
            "arrayllb",
            "b",
        )
        groups, _ = _collect_library_groups(
            graph,
            [main_key, a_key, b_key],
            project,
            [tmp_path / str(order) / "extracted"],
        )
        assert groups[a_key] == groups[b_key] == "user.lib"


def test_a_relative_path_resolves_against_the_callers_installation_path() -> None:
    caller = [*_LLB, "A.vi"]

    def resolve(tokens: list[str], caller_path: list[str] | None) -> list[str]:
        return resolve_against(tokens, caller_path)

    assert resolve(["", "B.vi"], caller) == [*_LLB, "B.vi"]
    assert resolve(["", "", "x.llb", "C.vi"], caller) == [
        "<userlib>",
        "_OpenG.lib",
        "array",
        "x.llb",
        "C.vi",
    ]
    assert resolve(["", "..", "D.vi"], caller) == [
        "<userlib>",
        "_OpenG.lib",
        "array",
        "D.vi",
    ]
    # More levels up than the caller's path has, or a caller with no installation
    # path: the tokens stay as recorded.
    assert resolve(["", "", "", "", "", "E.vi"], caller) == [
        "",
        "",
        "",
        "",
        "",
        "E.vi",
    ]
    assert resolve(["", "F.vi"], None) == ["", "F.vi"]
    assert resolve(["<vilib>", "G.vi"], caller) == ["<vilib>", "G.vi"]
