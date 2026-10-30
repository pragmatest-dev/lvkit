"""An inherited-method call is declared in lvnet's ``uses :`` list by the
callee's own qualified name (its owning library chain), the same name describe
prints, with a direct call beside it unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.netlist_build import build_netlist_from_graph
from tests.corpus_graphs import load_corpus_vi

_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source").resolve()


def _subvis(vi: Path) -> dict[str, str | None]:
    graph, key = load_corpus_vi(vi, _ROOT)
    return {
        d.qualified: d.path
        for d in build_netlist_from_graph(graph, key).dependencies
        if d.kind.value == "subVI"
    }


@pytest.mark.needs_samples
def test_inherited_method_declared_with_owning_class() -> None:
    subvis = _subvis(_ROOT / "Tests" / "MyFirstTest" / "testDemo.vi")
    assert subvis == {
        "TestCase.lvclass:failUnlessEqual.vi": "./Classes/TestCase/failUnlessEqual.vi"
    }


@pytest.mark.needs_samples
def test_inherited_and_direct_calls_side_by_side() -> None:
    subvis = _subvis(_ROOT / "Classes" / "TextTestRunner" / "run.vi")
    # Inherited from TestRunner / called directly on TestResult.
    assert subvis["TestRunner.lvclass:run.vi"] == "./Classes/TestRunner/run.vi"
    assert subvis["TestResult.lvclass:GetErrors.vi"] == (
        "./Classes/TestResult/GetErrors.vi"
    )
    assert "run.vi" not in subvis
