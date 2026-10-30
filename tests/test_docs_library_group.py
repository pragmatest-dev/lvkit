"""Docs: a VI's group comes from its library or recorded path, and a dependency
with no description shows none."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.docs.html_generator.naming import library_group
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


def test_group_comes_from_the_registration_only(tmp_path: Path) -> None:
    gen = HTMLDocGenerator(tmp_path, "t", "vi")
    gen.vi_groups = {"a/Build Path__ogtk.vi": "src", "b/Do.vi": "vi.lib"}
    assert gen._extract_library_group("a/Build Path__ogtk.vi") == "src"
    assert gen._extract_library_group("b/Do.vi") == "vi.lib"
    with pytest.raises(KeyError):
        gen._extract_library_group("Get System Directory.vi")
    gen.vi_groups["Lib.lvlib:Do.vi"] = "Lib.lvlib"
    assert gen._vi_name_to_filename("Lib.lvlib:Do.vi").startswith("Lib_lvlib/")


def test_label_names_the_page_not_the_key(tmp_path: Path) -> None:
    gen = HTMLDocGenerator(tmp_path, "t", "vi")
    key = "/home/someone/proj/src/Do.vi"
    gen.vi_groups = {key: "src"}
    gen.vi_labels = {key: "Do.vi"}
    assert gen._vi_name_to_filename(key) == "src/Do.html"
    assert gen._extract_display_name(key) == "Do"
    # The VI's own name is shown as it is.
    gen.vi_labels = {key: "Build Path__ogtk.vi"}
    assert gen._extract_display_name(key) == "Build Path__ogtk"


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
