"""Docs: a dependency that loaded from vi.lib, user.lib or a search path is filed
under the group its caller recorded, never under a folder of this machine."""

from __future__ import annotations

import shutil
from pathlib import Path

from lvkit.docs.generate import _collect_library_groups
from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.module_layout import ModuleLayout
from lvkit.parser.models import ParsedDependencyRef

CORPUS = Path(__file__).parent / "corpus" / "issues"
MAIN = CORPUS / "39" / "zorder-not-respected.vi"
VILIB_VI = CORPUS / "30" / "visible-frame-case-sequence.vi"
USERLIB_VI = CORPUS / "31" / "disable-structure-frame-names.vi"
SEARCH_VI = CORPUS / "33" / "coercion-dot-consistency.vi"


def _copy(source: Path, folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy(source, folder / name))


def _loaded(tmp_path: Path) -> tuple[InMemoryVIGraph, str, dict[str, str]]:
    project = tmp_path / "proj"
    main = _copy(MAIN, project, "Main.vi")
    vilib = tmp_path / "vilib"
    userlib = tmp_path / "userlib"
    search = tmp_path / "found here"
    _copy(VILIB_VI, vilib / "Utility" / "util.llb", "Util.vi")
    _copy(USERLIB_VI, userlib / "_Lib.lib" / "lib.llb", "Lib.vi")
    _copy(SEARCH_VI, search, "Other.vi")

    graph = InMemoryVIGraph()
    graph.set_library_roots(vilib_root=vilib, userlib_root=userlib)
    main_key = graph.load_vi(main, mode=LoadMode.MINIMAL)
    refs = {
        "Util.vi": ["<vilib>", "Utility", "util.llb", "Util.vi"],
        "Lib.vi": ["<userlib>", "_Lib.lib", "lib.llb", "Lib.vi"],
        "Other.vi": ["", "", "Shared", "Other.vi"],
    }
    keys = {}
    for name, tokens in refs.items():
        keys[name] = graph._load_dependency(
            name,
            ParsedDependencyRef(name=name, path_tokens=tokens),
            main,
            [search],
            caller_qname=main_key,
            mode=LoadMode.MINIMAL,
        )
    return graph, main_key, keys


def test_loaded_dependencies_keep_the_path_their_caller_recorded(
    tmp_path: Path,
) -> None:
    graph, _, keys = _loaded(tmp_path)
    assert graph.recorded_path(keys["Util.vi"]) == "<vilib>/Utility/util.llb/Util.vi"
    assert graph.recorded_path(keys["Lib.vi"]) == "<userlib>/_Lib.lib/lib.llb/Lib.vi"
    # A path relative to its caller names different files for different callers:
    # a loaded file does not keep it.
    assert graph.recorded_path(keys["Other.vi"]) is None


def test_groups_pages_and_titles_name_no_folder_of_this_machine(
    tmp_path: Path,
) -> None:
    graph, main_key, keys = _loaded(tmp_path)
    groups, labels = _collect_library_groups(
        graph, [main_key], tmp_path / "proj", [tmp_path / "found here"]
    )
    assert groups[keys["Util.vi"]] == "vi.lib"
    assert groups[keys["Lib.vi"]] == "user.lib"
    assert groups[keys["Other.vi"]] == "found here"
    assert groups[main_key] == "Project"

    gen = HTMLDocGenerator(tmp_path / "out", "<Docs> & more", "vi")
    gen.register_vi_names(groups, labels)
    gen.all_vis = set(groups)
    for name, folder in (("Util.vi", "vi_lib"), ("Lib.vi", "user_lib")) + (
        ("Other.vi", "found_here"),
    ):
        filename = gen._vi_name_to_filename(keys[name])
        assert filename.startswith(f"{folder}/") and filename.endswith(".html")
        assert filename.count("/") == 1

    index = gen._render_index_page(sorted(groups))
    assert "<title>&lt;Docs&gt; &amp; more - Documentation</title>" in index
    assert "<h1>&lt;Docs&gt; &amp; more</h1>" in index
    for group in ("vi.lib", "user.lib", "found here", "Project"):
        assert f'<span class="library-name">{group}</span>' in index
    for piece in (str(tmp_path), "tmp", "external", "<userlib>"):
        assert piece not in index
        assert piece not in "".join(gen._vi_name_to_filename(k) for k in groups)


def test_only_an_installation_folder_path_places_a_loaded_module(
    tmp_path: Path,
) -> None:
    """A path relative to the caller names different files for different callers,
    so a loaded file found through a search path is placed by that search path."""
    graph, main_key, keys = _loaded(tmp_path)
    layout = ModuleLayout(
        graph, [main_key, *keys.values()], tmp_path / "proj", [tmp_path / "found here"]
    )
    assert layout.places[keys["Util.vi"]].packages == ("vilib", "utility", "utilllb")
    assert layout.places[keys["Lib.vi"]].packages[:1] == ("userlib",)
    assert layout.places[keys["Other.vi"]].packages == ("found_here",)
