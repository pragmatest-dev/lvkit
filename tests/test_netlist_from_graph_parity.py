"""Phase 1 parity gate: ``build_netlist_from_graph`` vs ``build_netlist``.

``build_netlist_from_graph`` (``graph/netlist.py``) is a new SIBLING builder
that walks the ``GraphNode`` graph directly instead of the ``Operation``
projection. Phase 1 makes NO behavioral change -- it must reproduce
``build_netlist``'s output byte-for-byte. This test is the gate: for a
curated set of sample-corpus VIs (one per structure kind: case+for-loop with
a shift register and an auto-indexed output tunnel, a Feedback Node, an Event
Structure with locals, Property Nodes, and a Compound Arithmetic node with an
inverted input), plus a broader capped sample of JKI-VI-Tester VIs, load the
graph ONCE and assert both builders produce:

1. byte-identical ``render_netlist()`` text, and
2. equal ``netlist_to_dict()`` JSON.

VIs are loaded from ``.lvkit/cache/samples/`` (gitignored, pulled via
project scripts -- see ``project_samples_local_only`` in memory); tests
degrade gracefully (skip) when the corpus isn't present, mirroring
``tests/test_netlist.py``'s convention. ``LoadMode.MINIMAL``/``layout=False``
throughout, and the broader-coverage sweep caps the VI count and deletes
each graph before the next -- loading many VIs in one process can OOM WSL
(see CLAUDE.md "Searching for Code in VIs").
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.netlist import (
    build_netlist,
    build_netlist_from_graph,
    netlist_to_dict,
    render_netlist,
)
from lvkit.load_mode import LoadMode

_SAMPLES = Path(".lvkit/cache/samples")
_JKI_SOURCE_ROOT = _SAMPLES / "JKI-VI-Tester" / "source"


def _load(vi_path: Path, search_root: Path) -> tuple[InMemoryVIGraph, str] | None:
    """Load ``vi_path`` into a fresh graph, or ``None`` when the VI/corpus
    isn't present or fails to load (callers skip in that case, per the
    plan's "skip any VI that fails to load" instruction)."""
    if not vi_path.exists():
        return None
    graph = InMemoryVIGraph()
    try:
        graph.load_vi(
            str(vi_path), LoadMode.MINIMAL, search_paths=[search_root], layout=False
        )
    except Exception:
        return None
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _first_diff_line(a: str, b: str) -> str:
    """First differing line between two texts -- the debugging signal for a
    parity-mismatch assertion message, not itself part of the check."""
    a_lines = a.splitlines()
    b_lines = b.splitlines()
    for i, (x, y) in enumerate(
        itertools.zip_longest(a_lines, b_lines, fillvalue="<missing line>")
    ):
        if x != y:
            return f"line {i}: build_netlist={x!r} vs build_netlist_from_graph={y!r}"
    return "<no textual diff found>"


def _assert_parity(graph: InMemoryVIGraph, vi_name: str, label: str) -> None:
    from_op = build_netlist(graph, vi_name)
    from_graph = build_netlist_from_graph(graph, vi_name)

    text_op = render_netlist(from_op)
    text_graph = render_netlist(from_graph)
    assert text_graph == text_op, (
        f"{label}: render_netlist mismatch -- {_first_diff_line(text_op, text_graph)}"
    )

    dict_op = netlist_to_dict(from_op)
    dict_graph = netlist_to_dict(from_graph)
    assert dict_graph == dict_op, f"{label}: netlist_to_dict mismatch"


# ============================================================
# Curated VIs -- one per structure kind
# ============================================================

# case + for-loop with a shift register (mu) and an auto-indexed output
# tunnel (eta) -- see tests/test_netlist.py's _SHIFT_REGISTER_VI.
_CASE_LOOP_SHIFT_ETA_VI = (
    _JKI_SOURCE_ROOT / "Classes" / "TestLoader" / "loadTestsFromTestCase.vi"
)

# A split Feedback Node (z^-1) inside a while loop.
_FEEDBACK_VI = _SAMPLES / "lv-flex-channel-examples" / "WaveGen" / "WaveGen.vi"
_FEEDBACK_SEARCH_ROOT = _SAMPLES / "lv-flex-channel-examples" / "WaveGen"

# An Event Structure with Local Variables on its diagram.
_EVENT_LOCALS_VI = _SAMPLES / "lv-flex-channel-examples" / "DAQmx AO" / "DAQ AO.vi"
_EVENT_LOCALS_SEARCH_ROOT = _SAMPLES / "lv-flex-channel-examples" / "DAQmx AO"

# Property Nodes (read-only, write-only, and mixed read/write in one node).
_PROPERTY_NODE_VI = (
    _JKI_SOURCE_ROOT
    / "User Interfaces"
    / "Graphical Test Runner"
    / "Graphical Test Runner - Main UI - .vi"
)

# A Compound Arithmetic node with a genuine "Not" bubble on one input.
_COMPOUND_ARITH_VI = _JKI_SOURCE_ROOT / "Menu Launch" / "VI Tester Menu Launch.vi"


def test_parity_case_loop_shift_eta() -> None:
    loaded = _load(_CASE_LOOP_SHIFT_ETA_VI, _JKI_SOURCE_ROOT)
    if loaded is None:
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = loaded
    _assert_parity(graph, vi_name, _CASE_LOOP_SHIFT_ETA_VI.name)


def test_parity_feedback_node() -> None:
    loaded = _load(_FEEDBACK_VI, _FEEDBACK_SEARCH_ROOT)
    if loaded is None:
        pytest.skip("lv-flex-channel-examples sample corpus not present")
    graph, vi_name = loaded
    _assert_parity(graph, vi_name, _FEEDBACK_VI.name)


def test_parity_event_structure_and_locals() -> None:
    loaded = _load(_EVENT_LOCALS_VI, _EVENT_LOCALS_SEARCH_ROOT)
    if loaded is None:
        pytest.skip("lv-flex-channel-examples sample corpus not present")
    graph, vi_name = loaded
    _assert_parity(graph, vi_name, _EVENT_LOCALS_VI.name)


def test_parity_property_nodes() -> None:
    loaded = _load(_PROPERTY_NODE_VI, _JKI_SOURCE_ROOT)
    if loaded is None:
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = loaded
    _assert_parity(graph, vi_name, _PROPERTY_NODE_VI.name)


def test_parity_compound_arith_inverted_input() -> None:
    loaded = _load(_COMPOUND_ARITH_VI, _JKI_SOURCE_ROOT)
    if loaded is None:
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = loaded
    _assert_parity(graph, vi_name, _COMPOUND_ARITH_VI.name)


# ============================================================
# Broader coverage: up to 15 more JKI-VI-Tester VIs
# ============================================================

_EXTRA_VI_CAP = 15


def test_parity_broader_jki_corpus_sample() -> None:
    """Capped, deterministic sweep over more JKI-VI-Tester VIs for broader
    construct coverage -- one graph loaded and discarded per VI (see module
    docstring: loading many VIs in one process can OOM WSL)."""
    if not _JKI_SOURCE_ROOT.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    candidates = sorted(_JKI_SOURCE_ROOT.rglob("*.vi"))[:_EXTRA_VI_CAP]
    if not candidates:
        pytest.skip("no VIs found under JKI-VI-Tester source root")

    checked = 0
    failures: list[str] = []
    for vi_path in candidates:
        graph = InMemoryVIGraph()
        try:
            graph.load_vi(
                str(vi_path),
                LoadMode.MINIMAL,
                search_paths=[_JKI_SOURCE_ROOT],
                layout=False,
            )
        except Exception:
            continue
        vi_name = graph.resolve_vi_name(vi_path.name)
        try:
            _assert_parity(graph, vi_name, vi_path.name)
            checked += 1
        except AssertionError as exc:
            failures.append(f"{vi_path.name}: {exc}")

    if failures:
        pytest.fail(
            f"{len(failures)}/{checked + len(failures)} VI(s) failed parity:\n"
            + "\n".join(failures)
        )
    assert checked > 0, "no VI in the broader sample actually loaded/compared"
