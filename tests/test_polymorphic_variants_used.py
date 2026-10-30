"""A variant of a polymorphic VI gets generated code when a VI that is itself
generated uses it: a call of the wrapper that resolves to it, a call of it by
name from another variant, or a reference to it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.pipeline import generate_python
from lvkit.polymorphic_variants import variants_to_generate
from tests.test_call_identity import _record, _static, _Tree


class _Graph:
    """A graph of the keys named in ``dependents`` (key to the VIs that use it)."""

    def __init__(
        self,
        calls: dict[str, dict[str, set[str | None]]],
        dependents: dict[str, list[str]],
    ) -> None:
        self._calls = calls
        self._dependents = dependents

    def poly_call_choices(self) -> dict[str, dict[str, set[str | None]]]:
        return self._calls

    def get_vi_dependents(self, key: str) -> list[str]:
        return self._dependents.get(key, [])

    def recorded_path(self, key: str) -> str:
        return key


class _Resolver:
    def has_python(self, path: str | None) -> bool:
        return path == "catalog"


def _chosen(
    graph: _Graph, written: set[str], variants: list[str] | None = None
) -> list[str]:
    groups = {"wrap": variants or ["catalog", "array", "scalar"]}
    return variants_to_generate(
        graph,  # type: ignore[arg-type]
        _Resolver(),  # type: ignore[arg-type]
        groups,
        written,
    )["wrap"]


def test_a_variant_a_chosen_variant_calls_is_generated() -> None:
    graph = _Graph({"wrap": {"main": {"array"}}}, {"scalar": ["array", "wrap"]})
    assert _chosen(graph, {"main"}) == ["array", "scalar"]


def test_a_variant_only_a_variant_nothing_uses_calls_is_left_out() -> None:
    graph = _Graph({"wrap": {"main": {"scalar"}}}, {"scalar": ["wrap"], "array": []})
    assert _chosen(graph, {"main"}) == ["scalar"]


def test_a_variant_a_generated_vi_references_is_generated() -> None:
    graph = _Graph({"wrap": {"main": {"array"}}}, {"scalar": ["main"]})
    assert _chosen(graph, {"main"}) == ["array", "scalar"]


def test_a_variant_used_by_a_vi_that_is_not_generated_is_left_out() -> None:
    graph = _Graph({"wrap": {"main": {"array"}}}, {"scalar": ["elsewhere"]})
    assert _chosen(graph, {"main"}) == ["array"]


def test_a_call_that_leaves_the_choice_to_the_wrapper_needs_every_variant() -> None:
    graph = _Graph({"wrap": {"main": {None}}}, {})
    assert _chosen(graph, {"main"}) == ["array", "scalar"]


def test_a_wrapper_nothing_calls_keeps_every_variant() -> None:
    assert _chosen(_Graph({}, {}), set()) == ["array", "scalar"]


def test_a_catalog_variant_is_never_generated() -> None:
    graph = _Graph({"wrap": {"main": {"catalog"}}}, {})
    assert _chosen(graph, {"main"}) == []


@pytest.fixture
def poly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    """``main.vi`` calls the wrapper ``wrap.vi``; the variant ``array`` calls the
    variant ``scalar`` by name."""
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    built.vi("Two/scalar.vi", "scalar.vi", [])
    built.vi(
        "One/array.vi",
        "array.vi",
        [],
        [_static(["scalar.vi"], ["", "..", "Two", "scalar.vi"])],
    )
    variants = [("One", "array.vi"), ("Two", "scalar.vi")]
    wrapper = built.vi(
        "wrap.vi",
        "wrap.vi",
        [],
        [_static([name], ["", folder, name]) for folder, name in variants],
    )
    slot = built.extract(wrapper)[2]
    records = "".join(
        _record("VIVI", [name], ["", folder, name], 0, 10 + i)
        for i, (folder, name) in enumerate(variants)
    )
    text = slot.read_text().replace("</Section></LIvi>", f"{records}</Section></LIvi>")
    text = text.replace(
        "</VI>",
        '<LVSR><Section><Execution2 AllowPolyTypeAdapt="1"/></Section></LVSR></VI>',
    )
    slot.write_text(text)
    built.vi("main.vi", "main.vi", [], [_static(["wrap.vi"], ["", "wrap.vi"])])
    return built


def test_a_variant_called_by_a_chosen_variant_is_written_and_importable(
    poly: _Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def choices(self: InMemoryVIGraph) -> dict[str, dict[str, set[str | None]]]:
        key = lambda rel: str((poly.root / rel).resolve())  # noqa: E731
        return {key("wrap.vi"): {key("main.vi"): {key("One/array.vi")}}}

    monkeypatch.setattr(InMemoryVIGraph, "poly_call_choices", choices)
    out = tmp_path / "gen"
    result = generate_python(poly.root / "main.vi", out, search_paths=[poly.root])
    assert not result["gaps"]
    wrap = next(out.rglob("wrap.py"))
    source = wrap.read_text()
    assert "def array(" in source and "def scalar(" in source
    spec = importlib.util.spec_from_file_location("wrap_module", wrap)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.array()  # runs the variant it calls
    assert "scalar(" in source.split("def array(")[1].split("def ")[0]
