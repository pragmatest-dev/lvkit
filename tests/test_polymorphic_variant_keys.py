"""A polymorphic VI's variants are VIs known by their path keys, in the graph, the
docs and the generator, never by the names the wrapper's link table records."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.docs.generate import _prepare_vi_documentation_data
from lvkit.docs.html_generator import HTMLDocGenerator
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.load_mode import LoadMode
from lvkit.module_layout import ModuleLayout
from lvkit.pipeline import generate_python
from tests.test_call_identity import _record, _static, _Tree


@pytest.fixture
def poly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    variants = [("One", "v1.vi"), ("Two", "v2.vi")]
    for folder, name in variants:
        built.vi(f"{folder}/{name}", name, [])
    calls = [_static([name], ["", folder, name]) for folder, name in variants]
    wrapper = built.vi("wrap.vi", "wrap.vi", [], calls)
    slot = built.extract(wrapper)[2]
    records = "".join(
        _record("VIVI", [name], ["", folder, name], 0, 10 + i)
        for i, (folder, name) in enumerate(variants)
    )
    text = slot.read_text()
    text = text.replace("</Section></LIvi>", f"{records}</Section></LIvi>")
    text = text.replace(
        "</VI>",
        '<LVSR><Section><Execution2 AllowPolyTypeAdapt="1"/></Section></LVSR></VI>',
    )
    slot.write_text(text)
    return built


def _key(tree: _Tree, rel: str) -> str:
    return str((tree.root / rel).resolve())


def test_the_variants_are_the_keys_of_the_wrappers_dependencies(poly: _Tree) -> None:
    graph = InMemoryVIGraph()
    wrapper = graph.load_vi(poly.root / "wrap.vi", LoadMode.MINIMAL, [poly.root])
    expected = [_key(poly, "One/v1.vi"), _key(poly, "Two/v2.vi")]
    assert graph.get_polymorphic_groups() == {wrapper: expected}
    assert graph.get_poly_variants(wrapper) == expected
    assert graph.get_poly_variant_wrappers() == dict.fromkeys(expected, wrapper)


def test_the_docs_variants_section_links_each_variant_page(
    poly: _Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "lvkit.docs.generate.render_vi_with_subvis", lambda graph, key: (None, {})
    )
    graph = InMemoryVIGraph()
    wrapper = graph.load_vi(poly.root / "wrap.vi", LoadMode.MINIMAL, [poly.root])
    groups = graph.get_polymorphic_groups()
    problems: list[str] = []
    data = _prepare_vi_documentation_data(wrapper, graph, groups, problems, str)
    one, two = groups[wrapper]
    assert [v["name"] for v in data["variant_params"]] == [one, two]
    gen = HTMLDocGenerator(tmp_path / "out", "Docs", "directory")
    names = [wrapper, one, two]
    gen.register_vi_names(
        dict.fromkeys(names, "src"), {wrapper: "wrap.vi", one: "v1.vi", two: "v2.vi"}
    )
    gen.all_vis = set(names)
    section = gen._render_polymorphic_section(
        data["variant_params"], gen._vi_name_to_filename
    )
    assert f'href="{gen._vi_name_to_filename(one)}"' in section
    assert f'href="{gen._vi_name_to_filename(two)}"' in section
    assert gen._vi_name_to_filename(one) != gen._vi_name_to_filename(two)


def test_the_generator_leaves_the_variants_out_of_their_own_modules(
    poly: _Tree, tmp_path: Path
) -> None:
    graph = InMemoryVIGraph()
    wrapper = graph.load_vi(poly.root / "wrap.vi", LoadMode.FULL, [poly.root])
    groups = graph.get_polymorphic_groups()
    variants = {v for vs in groups.values() for v in vs}
    layout = ModuleLayout(
        graph, [wrapper, *sorted(variants)], poly.root, shared=frozenset(variants)
    )
    assert set(layout.places) == {wrapper}


def test_generate_writes_the_wrapper_module_and_none_for_its_variants(
    poly: _Tree, tmp_path: Path
) -> None:
    out = tmp_path / "gen"
    result = generate_python(poly.root / "wrap.vi", out, search_paths=[poly.root])
    written = sorted(
        p.relative_to(result["output_dir"]).as_posix() for p in out.rglob("*.py")
    )
    assert "wrap.py" in written
    assert not [w for w in written if w.endswith(("v1.py", "v2.py"))]
    wrap = next(out.rglob("wrap.py")).read_text()
    assert "def v1(" in wrap and "def v2(" in wrap
