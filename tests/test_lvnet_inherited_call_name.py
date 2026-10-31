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
def test_direct_calls_and_a_same_named_member_of_the_callers_class() -> None:
    subvis = _subvis(_ROOT / "Classes" / "TextTestRunner" / "run.vi")
    # TextTestRunner has its own run.vi (the caller) and inherits TestRunner's;
    # the body calls TestCase's and TestSuite's run.vi, so neither the caller
    # nor the ancestor's run.vi is declared.
    assert subvis["TestCase.lvclass:run.vi"] == "./Classes/TestCase/run.vi"
    assert subvis["TestSuite.lvclass:run.vi"] == "./Classes/TestSuite/run.vi"
    assert subvis["TextTestRunner.lvclass:makeResult.vi"] == (
        "./Classes/TextTestRunner/makeResult.vi"
    )
    assert "TestRunner.lvclass:run.vi" not in subvis
    assert "run.vi" not in subvis
