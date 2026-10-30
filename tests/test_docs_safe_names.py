"""Docs page and icon names, HTML text and SVG ids stay safe and unique."""

from __future__ import annotations

import re
from pathlib import Path

from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.render import _root_id
from lvkit.render.backend import SvgBackend

SAFE = re.compile(r"[A-Za-z0-9_./-]+")


def _generator(tmp_path: Path, labels: dict[str, str]) -> HTMLDocGenerator:
    gen = HTMLDocGenerator(tmp_path / "out", "Docs", "directory")
    gen.register_vi_names(dict.fromkeys(labels, "Group #1"), labels)
    return gen


def test_page_names_hold_only_safe_characters(tmp_path: Path) -> None:
    labels = {
        "/p/a.vi": "50% done #2.vi",
        "/p/b.vi": "Lib.lvlib:Größe (x).vi",
        "/p/c.vi": "Cls.lvclass:Do it?.vi",
    }
    gen = _generator(tmp_path, labels)
    for key in labels:
        assert SAFE.fullmatch(gen._vi_name_to_filename(key))
    assert SAFE.fullmatch(gen._class_name_to_filename("Lib.lvlib:Cls.lvclass"))


def test_names_that_sanitize_alike_get_distinct_pages_and_icons(
    tmp_path: Path,
) -> None:
    labels = {"/p/1.vi": "a b.vi", "/p/2.vi": "a_b.vi", "/p/3.vi": "A_B.vi"}
    gen = _generator(tmp_path, labels)
    pages = {gen._vi_name_to_filename(k) for k in labels}
    assert len(pages) == 3
    icons = {p.removesuffix(".html") + ".png" for p in pages}
    assert len(icons) == 3


def test_a_name_that_sanitizes_alone_keeps_its_plain_page(tmp_path: Path) -> None:
    gen = _generator(tmp_path, {"/p/1.vi": "a b.vi"})
    assert gen._vi_name_to_filename("/p/1.vi") == "Group_1/a_b.html"


def test_control_names_types_and_defaults_are_escaped(tmp_path: Path) -> None:
    gen = _generator(tmp_path, {})
    rows = [{"name": "<b>x</b>", "type": "A<B>", "default_value": "&amp;"}]
    html = gen._render_controls_table(rows)
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert "A&lt;B&gt;" in html and "&amp;amp;" in html
    out = gen._render_indicators_table([{"name": "<i>", "type": "<t>"}])
    assert "<i>" not in out and "&lt;i&gt;" in out and "&lt;t&gt;" in out


def test_variant_parameter_names_are_escaped(tmp_path: Path) -> None:
    gen = _generator(tmp_path, {"/p/v.vi": "v.vi"})
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
