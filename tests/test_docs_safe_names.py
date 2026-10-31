"""Docs page and icon names come from ``docs/page_names.py``; HTML text is
escaped and SVG ids stay unique."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.docs.page_names import PageName, page_names
from lvkit.render import _root_id
from lvkit.render.backend import SvgBackend

SAFE = re.compile(r"[A-Za-z0-9_./-]+")


def _generator(tmp_path: Path, pages: dict[str, PageName]) -> HTMLDocGenerator:
    gen = HTMLDocGenerator(tmp_path / "out", "Docs", "directory")
    gen.register_vis(pages)
    return gen


def test_a_page_is_the_file_page_names_gives_it(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    keys = [str(project / "sub" / "a b.vi"), str(project / "sub" / "a_b.vi")]
    graph = SimpleNamespace(get_vi_source_path=lambda key: Path(key))
    pages = page_names(graph, keys, project)  # type: ignore[arg-type]
    gen = _generator(tmp_path, pages)
    assert sorted(gen._vi_name_to_filename(k) for k in keys) == [
        "Project/sub_a_b-2.html",
        "Project/sub_a_b.html",
    ]
    # The page title is the bare file name.
    assert gen._extract_display_name(keys[0]) == "a b"


def test_icons_follow_their_pages(tmp_path: Path) -> None:
    pages = {
        "/p/1.vi": PageName("Project", "a_b"),
        "/p/2.vi": PageName("Project", "a_b-2"),
    }
    gen = _generator(tmp_path, pages)
    icons = {
        gen._vi_name_to_filename(k).removesuffix(".html") + ".png" for k in pages
    }
    assert icons == {"Project/a_b.png", "Project/a_b-2.png"}


def test_control_names_types_and_defaults_are_escaped(tmp_path: Path) -> None:
    gen = _generator(tmp_path, {})
    rows = [{"name": "<b>x</b>", "type": "A<B>", "default_value": "&amp;"}]
    html = gen._render_controls_table(rows)
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert "A&lt;B&gt;" in html and "&amp;amp;" in html
    out = gen._render_indicators_table([{"name": "<i>", "type": "<t>"}])
    assert "<i>" not in out and "&lt;i&gt;" in out and "&lt;t&gt;" in out


def test_variant_parameter_names_are_escaped(tmp_path: Path) -> None:
    gen = _generator(tmp_path, {"/p/v.vi": PageName("Project", "v")})
    gen.all_vis = {"/p/v.vi"}
    params = [
        {
            "name": "/p/v.vi",
            "inputs": [{"name": "<script>", "type": "t"}],
            "outputs": [{"name": "a&b", "type": "t"}],
        }
    ]
    html = gen._render_polymorphic_section(params, gen._vi_name_to_filename)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "a&amp;b" in html


def test_two_drawings_with_one_name_have_different_root_ids() -> None:
    first, second = SvgBackend(), SvgBackend()
    first.rect(0, 0, 10, 10, fill="red")
    second.rect(0, 0, 20, 20, fill="red")
    again = SvgBackend()
    again.rect(0, 0, 10, 10, fill="red")
    ids = [_root_id("Main.vi", b.content_digest()) for b in (first, second, again)]
    assert ids[0] != ids[1]
    assert ids[0] == ids[2]
    assert re.fullmatch(r"lv-Main-vi-[0-9a-f]{8}", ids[0])
