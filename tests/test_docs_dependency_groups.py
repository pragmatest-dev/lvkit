"""Docs: a dependency that loaded from vi.lib, user.lib or the VI search path is
listed on the index under the section its caller recorded (else its own project
folder), and its page is where ``docs/page_names.py`` puts it, never under a
folder of this machine."""

from __future__ import annotations

import shutil
from pathlib import Path

from lvkit.docs.generate import _index_sections
from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.docs.page_names import page_names
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
    (project / ".lvkit").mkdir(parents=True)
    main = _copy(MAIN, project, "Main.vi")
    vilib = tmp_path / "vilib"
    userlib = tmp_path / "userlib"
    search = tmp_path / "found here"
    (search / ".lvkit").mkdir(parents=True)
    _copy(VILIB_VI, vilib / "Utility" / "util.llb", "Util.vi")
    _copy(USERLIB_VI, userlib / "_Lib.lib" / "lib.llb", "Lib.vi")
    _copy(SEARCH_VI, search, "Other.vi")

    graph = InMemoryVIGraph()
    graph.set_library_roots(
        vilib_root=vilib, userlib_root=userlib, search_path=[str(search)]
    )
    main_key = graph.load_vi(main, mode=LoadMode.MINIMAL)
    assert main_key is not None
    refs = {
        "Util.vi": ["<vilib>", "Utility", "util.llb", "Util.vi"],
        "Lib.vi": ["<userlib>", "_Lib.lib", "lib.llb", "Lib.vi"],
        "Other.vi": ["", "", "Shared", "Other.vi"],
    }
    keys = {}
    for name, tokens in refs.items():
        key = graph._load_dependency(  # noqa: SLF001
            name,
            ParsedDependencyRef(name=name, path_tokens=tokens),
            main,
            caller_qname=main_key,
            mode=LoadMode.MINIMAL,
        )
        assert key is not None and not graph.is_stub(key)
        keys[name] = key
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


def test_index_sections_and_pages_name_no_folder_of_this_machine(
    tmp_path: Path,
) -> None:
    graph, main_key, keys = _loaded(tmp_path)
    project = tmp_path / "proj"
    names = [main_key, *keys.values()]
    sections = _index_sections(graph, names, project)
    assert sections[keys["Util.vi"]] == "vi.lib"
    assert sections[keys["Lib.vi"]] == "user.lib"
    # Found on the VI search path: grouped like any other file outside the
    # project, by its own project folder.
    assert sections[keys["Other.vi"]] == "found here"
    assert sections[main_key] == "Project"

    gen = HTMLDocGenerator(tmp_path / "out", "<Docs> & more", "vi")
    gen.register_vis(page_names(graph, names, project))
    gen.register_index_sections(sections)
    gen.all_vis = set(names)
    assert gen._vi_name_to_filename(main_key) == "Project/Main.html"
    assert gen._vi_name_to_filename(keys["Util.vi"]).startswith("vi_lib/")
    assert gen._vi_name_to_filename(keys["Lib.vi"]).startswith("user_lib/")
    assert gen._vi_name_to_filename(keys["Other.vi"]) == "found here/Other.html"

    index = gen._render_index_page(sorted(names))
    assert "<title>&lt;Docs&gt; &amp; more - Documentation</title>" in index
    assert "<h1>&lt;Docs&gt; &amp; more</h1>" in index
    for section in ("vi.lib", "user.lib", "found here", "Project"):
        assert f'<span class="library-name">{section}</span>' in index
    for piece in (str(tmp_path), "external", "<userlib>"):
        assert piece not in index
        assert piece not in "".join(gen._vi_name_to_filename(k) for k in names)


def test_only_an_installation_folder_path_places_a_loaded_module(
    tmp_path: Path,
) -> None:
    """A path relative to the caller names different files for different callers,
    so a loaded file found on the VI search path is placed beside the project."""
    graph, main_key, keys = _loaded(tmp_path)
    layout = ModuleLayout(graph, [main_key, *keys.values()], tmp_path / "proj")
    assert layout.places[keys["Util.vi"]].packages == ("vilib", "utility", "utilllb")
    assert layout.places[keys["Lib.vi"]].packages[:1] == ("userlib",)
    assert layout.places[keys["Other.vi"]].packages == ("external", "found_here")
