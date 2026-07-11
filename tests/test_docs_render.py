"""Golden tests for SVG-in-docs integration.

Verifies the docs pipeline embeds the faithful ``render_vi`` SVG at the Block
Diagram section (mermaid is fully retired), shows a note when the render can't
produce geometry, and injects click-to-navigate wiring onto documented subVI
nodes. The sample VI is local-only (gitignored) — skip gracefully when it's
unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit import docs as docs_pkg  # noqa: F401  (ensures package import works)
from lvkit.docs.generate import _prepare_vi_documentation_data
from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.graph.core import InMemoryVIGraph

SAMPLE_VI = Path(".tmp/array average 1.vi")


def _load_sample() -> tuple[InMemoryVIGraph, str] | None:
    if not SAMPLE_VI.exists():
        return None
    graph = InMemoryVIGraph()
    try:
        graph.load_vi(SAMPLE_VI, expand_subvis=False)
    except Exception:
        return None
    return graph, graph.resolve_vi_name(SAMPLE_VI.name)


def _require_sample() -> tuple[InMemoryVIGraph, str]:
    loaded = _load_sample()
    if loaded is None:
        pytest.skip(f"sample VI not available: {SAMPLE_VI}")
    return loaded


def _generate_page(tmp_path: Path, graph: InMemoryVIGraph, vi_name: str) -> str:
    generator = HTMLDocGenerator(tmp_path, "test-doc", "vi")
    generator.all_vis = {vi_name}
    vi_data = _prepare_vi_documentation_data(vi_name, graph, poly_groups={})
    generator.generate_vi_page(vi_data)
    html_path = tmp_path / generator._vi_name_to_filename(vi_name)
    return html_path.read_text(encoding="utf-8")


def _dataflow_section(html: str) -> str:
    start = html.index('<section id="dataflow">')
    end = html.index("</section>", start)
    return html[start:end]


def test_docs_embed_faithful_svg_when_render_succeeds(tmp_path):
    graph, vi_name = _require_sample()
    from lvkit.render import render_vi

    assert render_vi(graph, vi_name) is not None, (
        "expected render_vi to succeed for this sample; if geometry "
        "regressed, this test's premise is invalid"
    )

    html = _generate_page(tmp_path, graph, vi_name)
    section = _dataflow_section(html)

    assert "<svg" in section
    assert "<pre class='mermaid'>" not in section


def test_docs_show_note_when_render_fails(tmp_path, monkeypatch):
    graph, vi_name = _require_sample()

    monkeypatch.setattr(
        "lvkit.docs.generate.render_vi_with_subvis",
        lambda graph, vi_name: (None, {}),
    )

    html = _generate_page(tmp_path, graph, vi_name)
    section = _dataflow_section(html)

    # No SVG, no mermaid — just an explanatory note.
    assert "<svg" not in section
    assert "mermaid" not in section
    assert "Block diagram unavailable" in section


def test_diagram_injects_subvi_navigation(tmp_path):
    """A documented subVI node gets click-to-navigate wiring, added by the doc
    layer and keyed on the renderer's ``data-node`` id."""
    gen = HTMLDocGenerator(tmp_path, "test-doc", "vi")
    gen.all_vis = {"Child.vi"}
    svg = '<svg><g class="lv-node" data-node="Parent.vi::42"></g></svg>'
    html = gen._render_diagram(
        svg, {"Parent.vi::42": "Child.vi"}, lambda n: n.replace(".vi", ".html")
    )
    assert 'data-node="Parent.vi::42"' in html  # SVG embedded verbatim
    assert '"Parent.vi::42"' in html and "Child.html" in html  # nav map
    assert "location.href" in html  # click behavior


def test_diagram_no_navigation_for_undocumented_subvi(tmp_path):
    """A subVI without its own page is not turned into a link."""
    gen = HTMLDocGenerator(tmp_path, "test-doc", "vi")
    gen.all_vis = set()
    svg = '<svg><g data-node="Parent.vi::42"></g></svg>'
    html = gen._render_diagram(svg, {"Parent.vi::42": "Child.vi"}, lambda n: n)
    assert "location.href" not in html
