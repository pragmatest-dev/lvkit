"""Docs: a VI's group comes from its library or recorded path, and a dependency
with no description shows none."""

from __future__ import annotations

from pathlib import Path

from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.docs.html_generator.naming import library_group
from lvkit.docs.utils import generate_dependency_description


def test_library_group_sources() -> None:
    assert library_group("Lib.lvlib:Do.vi", ()) == "Lib.lvlib"
    assert library_group("Cls.lvclass:Do.vi", ("src",)) == "Cls.lvclass"
    assert library_group("Do.vi", ("<vilib>", "Utility", "error.llb")) == "vi.lib"
    assert library_group("Build Path__ogtk.vi", ("src", "paths")) == "src/paths"
    assert library_group("Do.vi", ()) == "Project"


def test_group_not_guessed_from_name(tmp_path: Path) -> None:
    gen = HTMLDocGenerator(tmp_path, "t", "vi")
    gen.vi_groups = {"a/Build Path__ogtk.vi": "src", "b/Do.vi": "vi.lib"}
    assert gen._extract_library_group("a/Build Path__ogtk.vi") == "src"
    assert gen._extract_library_group("b/Do.vi") == "vi.lib"
    # No registration and no qualified name: the project's own folder.
    assert gen._extract_library_group("Get System Directory.vi") == "Project"
    assert gen._vi_name_to_filename("Lib.lvlib:Do.vi").startswith("Lib_lvlib/")


def test_label_names_the_page_not_the_key(tmp_path: Path) -> None:
    gen = HTMLDocGenerator(tmp_path, "t", "vi")
    key = "/home/someone/proj/src/Do.vi"
    gen.vi_groups = {key: "src"}
    gen.vi_labels = {key: "Do.vi"}
    assert gen._vi_name_to_filename(key) == "src/Do.html"
    assert gen._extract_display_name(key) == "Do"


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


def test_unknown_stub_has_no_name_guessed_description() -> None:
    graph = _StubGraph()
    assert generate_dependency_description("Read File Contents.vi", graph) == ""  # type: ignore[arg-type]
