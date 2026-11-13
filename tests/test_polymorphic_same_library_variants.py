"""A polymorphic VI in a library whose variants are in that library records each
variant by its file name alone (a same-library record); the calls of the variants
record the library's name too. The variants are still the polymorphic VI's: the
graph finds them, and the generator writes one module for the polymorphic VI with
a function per variant, with no gap."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.load_mode import LoadMode
from lvkit.parser.metadata import parse_polymorphic_info
from lvkit.pipeline import generate_python
from tests.test_call_identity import _Call, _record, _Tree

_LIBRARY = ["Lib.lvlib", "Sub.lvlib"]
_SAME_LIBRARY = 2


@pytest.fixture
def poly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    variants = ["v1.vi", "v2.vi"]
    for name in variants:
        built.vi(f"Sub/{name}", f"Lib.lvlib:Sub.lvlib:{name}", _LIBRARY)
    calls = [
        _Call(records=[("IUVI", [name], ["", name], _SAME_LIBRARY)])
        for name in variants
    ]
    wrapper = built.vi("Sub/wrap.vi", "Lib.lvlib:Sub.lvlib:wrap.vi", _LIBRARY, calls)
    slot = built.extract(wrapper)[2]
    records = "".join(
        _record("VIVI", [name], ["", name], _SAME_LIBRARY, 10 + i)
        for i, name in enumerate(variants)
    )
    text = slot.read_text()
    text = text.replace("</Section></LIvi>", f"{records}</Section></LIvi>")
    text = text.replace(
        "</VI>",
        '<LVSR><Section><Execution2 AllowPolyTypeAdapt="1"/></Section></LVSR></VI>',
    )
    slot.write_text(text)
    return built


def test_the_variants_are_named_as_their_calls_name_them(poly: _Tree) -> None:
    root = ET.fromstring(poly.extract(poly.root / "Sub/wrap.vi")[2].read_text())
    info = parse_polymorphic_info(root)
    assert info["variants"] == ["Lib.lvlib:v1.vi", "Lib.lvlib:v2.vi"]


def test_the_graph_finds_the_variants(poly: _Tree) -> None:
    graph = InMemoryVIGraph()
    wrapper = graph.load_vi(poly.root / "Sub/wrap.vi", LoadMode.MINIMAL)
    expected = [str((poly.root / f"Sub/{n}").resolve()) for n in ("v1.vi", "v2.vi")]
    assert graph.get_polymorphic_groups() == {wrapper: expected}


def test_generate_writes_a_function_per_variant_with_no_gap(
    poly: _Tree, tmp_path: Path
) -> None:
    out = tmp_path / "gen"
    result = generate_python(poly.root / "Sub/wrap.vi", out)
    assert not result["gaps"]
    wrap = next(out.rglob("wrap.py")).read_text()
    assert "def v1(" in wrap and "def v2(" in wrap
    assert not list(out.rglob("v1.py"))
