"""Coverage for the LIbd/BDHP dependency-path-ref parser fix.

`_extract_subvi_info` walks both the LIvi link table *and* the block-diagram
LIbd/BDHP iUse records (IUVI/PUPV). Some callers (e.g. DAQmx) carry their
path refs only under LIbd, so the second scope is what makes their SubVI
dependencies resolvable. These tests assert the resulting graph state
directly — they were previously coupled to the JSON export layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.models import (
    CaseOperation,
    Operation,
    SequenceOperation,
)

SEARCH_PATHS = [Path("samples/OpenG/extracted")]
DAQMX_CALLER_VI = Path("samples/lv-flex-channel-examples/DAQmx AO/DAQ AO.vi")
TESTCASE_CLASS = Path(
    "samples/JKI-VI-Tester/source/Classes/TestCase/TestCase.lvclass"
)
TESTCASE_SEARCH = [Path("samples/JKI-VI-Tester/source")]


def _skip_if_missing(*paths: Path) -> None:
    for p in paths:
        if not p.exists():
            pytest.skip(f"Sample not available: {p}")


@pytest.fixture(scope="module")
def daqmx_graph() -> InMemoryVIGraph:
    _skip_if_missing(DAQMX_CALLER_VI)
    g = InMemoryVIGraph()
    g.load_vi(str(DAQMX_CALLER_VI), mode=LoadMode.NONE, search_paths=SEARCH_PATHS)
    return g


@pytest.fixture(scope="module")
def testcase_graph() -> InMemoryVIGraph:
    _skip_if_missing(TESTCASE_CLASS)
    g = InMemoryVIGraph()
    g.load_lvclass(
        str(TESTCASE_CLASS), mode=LoadMode.FULL, search_paths=TESTCASE_SEARCH
    )
    return g


def _walk_ops(ops: list[Operation]):
    """Yield every operation, recursing into structure frames + inner nodes."""
    for op in ops:
        yield op
        if isinstance(op, (CaseOperation, SequenceOperation)):
            for frame in op.frames:
                yield from _walk_ops(frame.operations)
        yield from _walk_ops(op.inner_nodes)


def test_stub_deps_carry_path_tokens(testcase_graph):
    """Unresolved deps carry path_tokens threaded from the parser's link refs."""
    g = testcase_graph
    stubs_with_tokens = [
        n for n in g._stubs
        if g._dep_graph.has_node(n)
        and g._dep_graph.nodes[n].get("path_tokens")
    ]
    assert stubs_with_tokens, "expected at least one stub with path_tokens"
    # At least one stub should carry a vendor-library root token. Class-member
    # stubs use caller-relative tokens (also valid) — this assertion protects
    # the <vilib>/<userlib> path specifically.
    rooted = [
        n for n in stubs_with_tokens
        if g._dep_graph.nodes[n]["path_tokens"][0] in ("<vilib>", "<userlib>")
    ]
    assert rooted, "expected at least one stub with a <vilib>/<userlib> path"


def test_vilib_iuses_carry_qualified_path(daqmx_graph):
    """DAQmx (vilib) iuses carry qualified_path via the LIbd/BDHP parser fix."""
    g = daqmx_graph
    ctx = g.get_vi_context(g.resolve_vi_name("DAQ AO.vi"))
    paths = [
        op.qualified_path
        for op in _walk_ops(ctx.operations)
        if op.qualified_path
    ]
    assert paths, "expected at least one operation with a qualified_path"
    rooted = [
        p for p in paths
        if p.startswith("<vilib>") or p.startswith("<userlib>")
    ]
    assert rooted, f"expected a <vilib>/<userlib> qualified_path, got {paths}"
