"""Regression tests: understanding tools never require resolution.

vipy is both a converter (Python codegen) AND an understanding tool
(describe, docs, diff, visualize). The understanding tools must work
even when the resolver has NO mappings for any primitive or vi.lib VI.
This file locks that guarantee in with regression tests.

The strategy: monkey-patch the resolver singletons to return None for
every lookup, then run each understanding tool against a real sample
VI and assert it (a) does not raise and (b) produces non-empty output.

If a future change introduces a hard `resolve_*().something` call into
any of these paths, these tests fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vipy import primitive_resolver, vilib_resolver
from vipy.docs.generate import generate_documents
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import (
    describe_constants,
    describe_dataflow,
    describe_operations,
    describe_vi,
)
from vipy.graph.diff import diff_structured, diff_text
from vipy.graph.flowchart import flowchart

SAMPLE_VI = Path("samples/DAQmx-Digital-IO/In.vi")


def _samples_available() -> bool:
    return SAMPLE_VI.exists()


pytestmark = pytest.mark.skipif(
    not _samples_available(),
    reason="Requires samples/DAQmx-Digital-IO/In.vi",
)


@pytest.fixture
def empty_resolvers(monkeypatch):
    """Patch both resolver singletons to return None for every lookup.

    Forces the understanding tools down their "no resolution available"
    paths so we verify they don't depend on resolution succeeding.
    """
    # Build real resolvers (so type system + dataclass shape are intact),
    # then clear their lookup tables.
    prim = primitive_resolver.PrimitiveResolver()
    prim._by_id.clear()
    prim._by_name.clear()
    prim._by_signature.clear()
    prim._by_node_type.clear()
    monkeypatch.setattr(primitive_resolver, "_resolver", prim)

    vilib = vilib_resolver.VILibResolver()
    vilib._vis.clear()
    vilib._by_name.clear()
    vilib._pdf_entries.clear()
    vilib._types.clear()
    vilib._category_files.clear()
    vilib._variants.clear()
    vilib._by_poly_selector.clear()
    monkeypatch.setattr(vilib_resolver, "_resolver", vilib)

    yield

    # Reset to lazy-init on next get_resolver()
    primitive_resolver.reset_resolver(project_data_dir=None)
    vilib_resolver.reset_resolver(project_data_dir=None)


@pytest.fixture
def loaded_graph(empty_resolvers) -> tuple[InMemoryVIGraph, str]:
    """Load the sample VI through a graph WITHOUT any resolver mappings.

    Verifies that load_vi() itself never raises when resolution fails.
    Graph construction calls non-raising resolver APIs internally — this
    fixture catches any future regression that adds a hard resolver call.
    """
    graph = InMemoryVIGraph()
    graph.load_vi(str(SAMPLE_VI))
    vi_name = graph.resolve_vi_name(SAMPLE_VI.name)
    return graph, vi_name


# ============================================================
# describe
# ============================================================


def test_describe_vi_with_no_resolutions(loaded_graph) -> None:
    """describe_vi runs with empty resolvers and produces output."""
    graph, vi_name = loaded_graph
    text = describe_vi(graph, vi_name)
    assert text
    # Title is always present
    assert vi_name in text
    # Sections are always present
    assert "## Operations" in text


def test_describe_operations_with_no_resolutions(loaded_graph) -> None:
    """describe_operations runs with empty resolvers."""
    graph, vi_name = loaded_graph
    text = describe_operations(graph, vi_name)
    assert text
    assert vi_name in text


def test_describe_dataflow_with_no_resolutions(loaded_graph) -> None:
    """describe_dataflow runs with empty resolvers."""
    graph, vi_name = loaded_graph
    text = describe_dataflow(graph, vi_name)
    assert text
    assert "Dataflow" in text


def test_describe_constants_with_no_resolutions(loaded_graph) -> None:
    """describe_constants runs with empty resolvers."""
    graph, vi_name = loaded_graph
    text = describe_constants(graph, vi_name)
    assert text
    assert "Constants" in text


# ============================================================
# diff
# ============================================================


def test_diff_text_with_no_resolutions(loaded_graph) -> None:
    """diff_text runs with empty resolvers (compares VI to itself)."""
    graph, vi_name = loaded_graph
    # Diffing identical VIs should produce empty output without raising
    result = diff_text(graph, graph, vi_name, vi_name)
    assert result == ""


def test_diff_structured_with_no_resolutions(loaded_graph) -> None:
    """diff_structured runs with empty resolvers."""
    graph, vi_name = loaded_graph
    report = diff_structured(graph, graph, vi_name, vi_name)
    assert report.is_empty()


# ============================================================
# visualize / flowchart
# ============================================================


def test_flowchart_with_no_resolutions(loaded_graph) -> None:
    """flowchart() renders Mermaid output with empty resolvers."""
    graph, vi_name = loaded_graph
    text = flowchart(graph, vi_name)
    assert text
    # Mermaid flowchart syntax marker
    assert "flowchart" in text.lower() or "graph" in text.lower()


# ============================================================
# docs (full HTML pipeline)
# ============================================================


def test_generate_documents_with_no_resolutions(
    empty_resolvers, tmp_path: Path
) -> None:
    """generate_documents produces HTML output with empty resolvers.

    Exercises the full vipy docs pipeline: load → graph → HTML render.
    """
    output_dir = tmp_path / "html"
    summary = generate_documents(
        library_path=str(SAMPLE_VI),
        output_dir=str(output_dir),
        expand_subvis=False,
    )
    assert summary  # Non-empty status string
    assert output_dir.is_dir()
    html_files = list(output_dir.rglob("*.html"))
    assert html_files, "No HTML files generated"


# ============================================================
# Loading is structural-only — never raises on missing resolution
# ============================================================


def test_load_vi_does_not_raise_with_empty_resolvers(empty_resolvers) -> None:
    """InMemoryVIGraph.load_vi() works with empty resolvers.

    Graph construction calls resolve_primitive(), resolve_by_name(), and
    resolve_poly_variant() internally to disambiguate terminal indices.
    All those calls return Optional and the construction code handles
    None by leaving indices unresolved (-1). The loader never raises
    PrimitiveResolutionNeeded or VILibResolutionNeeded — those are only
    thrown from codegen.

    This test exists so any future commit that adds a hard `resolve_*()`
    call to the loading path fails immediately.
    """
    graph = InMemoryVIGraph()
    graph.load_vi(str(SAMPLE_VI))  # Must not raise
    assert SAMPLE_VI.name in {Path(v).name for v in graph.list_vis()} or any(
        SAMPLE_VI.stem in v for v in graph.list_vis()
    )
