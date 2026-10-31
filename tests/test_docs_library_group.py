"""Docs: the index lists a VI under its library, installation folder or project
folder, never naming its page by it; a dependency with no description shows none."""

from __future__ import annotations

from pathlib import Path

from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.docs.html_generator.naming import library_group
from lvkit.docs.page_names import PageName
from lvkit.docs.utils import generate_dependency_description


def test_library_group_sources() -> None:
    assert library_group("Lib.lvlib", ()) == "Lib.lvlib"
    assert library_group("Lib.lvlib:Cls.lvclass", ("src",)) == "Lib.lvlib:Cls.lvclass"
    assert library_group(None, ("<vilib>", "Utility", "error.llb")) == "vi.lib"
    assert library_group(None, ("<userlib>", "_OpenG.lib", "x.llb")) == "user.lib"
    assert library_group(None, ("<instrlib>", "dev")) == "instr.lib"
    assert library_group(None, ("<resource>", "dev")) == "resource"
    assert library_group(None, ("src", "paths")) == "src/paths"
    assert library_group(None, ()) == "Project"


def test_the_index_section_comes_from_the_registration(tmp_path: Path) -> None:
    gen = HTMLDocGenerator(tmp_path, "t", "vi")
    key = "/home/someone/proj/src/Do.vi"
    gen.register_vis({key: PageName("Project", "src_Do")})
    gen.register_index_sections({key: "src"})
    assert gen._index_section(key) == "src"
    # The section never names the page: the page is where ``page_names`` puts it.
    assert gen._vi_name_to_filename(key) == "Project/src_Do.html"
    # A VI with no registered section is listed under its page's folder.
    gen.register_index_sections({})
    assert gen._index_section(key) == "Project"


def test_the_index_lists_pages_under_their_sections(tmp_path: Path) -> None:
    gen = HTMLDocGenerator(tmp_path, "t", "vi")
    owned, util = "/proj/Lib/Do.vi", "/vilib/Utility/Util.vi"
    gen.register_vis(
        {owned: PageName("Project", "Lib_Do"), util: PageName("vi.lib", "Utility_Util")}
    )
    gen.register_index_sections({owned: "Lib.lvlib", util: "vi.lib"})
    index = gen._render_index_page([owned, util])
    assert '<span class="library-name">Lib.lvlib</span>' in index
    assert '<span class="library-name">vi.lib</span>' in index
    assert 'href="Project/Lib_Do.html"' in index
    assert 'href="vi_lib/Utility_Util.html"' in index


def test_empty_description_renders_nothing(tmp_path: Path) -> None:
    gen = HTMLDocGenerator(tmp_path, "t", "vi")
    html = gen._render_dependencies_section(
        {"Mystery.vi": "", "Known.vi": "Does a thing"}, lambda n: n
    )
    assert "<code>Mystery.vi</code>\n" in html
    assert "None" not in html and "Mystery.vi</code> -" not in html
    assert "Known.vi</code> - Does a thing" in html


class _StubGraph:
    def is_stub(self, name: str) -> bool:
        return True

    def recorded_path(self, name: str) -> str | None:
        return None


def test_unknown_stub_has_no_name_guessed_description() -> None:
    graph = _StubGraph()
    assert generate_dependency_description("Read File Contents.vi", graph) == ""  # type: ignore[arg-type]
