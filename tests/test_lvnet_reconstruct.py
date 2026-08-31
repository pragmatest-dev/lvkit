"""Full-model-reconstruction + re-render idempotence for the lvnet text IR.

The strongest losslessness gate (Element 4): for a real corpus VI, with
``T = render_lvnet(m, verbose=True)``::

    render_lvnet(reconstruct_module(parse_lvnet(T)), verbose=True) == T

i.e. the verbose text alone is enough to rebuild a RENDER-EQUIVALENT
``NetlistModule`` -- byte-identical re-render, not just a matching
``netlist_signature`` projection (the existing, weaker round-trip gate in
``test_lvnet_roundtrip.py``, kept unchanged and still green alongside this).

A failure here is the informative signal this element exists to produce: a
precise byte diff plus which construct/VI it came from, never patched over
by fudging ``reconstruct_module``.

All 7 corpus VIs below reconstruct BYTE-IDENTICALLY -- every handle, net,
structural id, merge, terminal type, AND the ``types :`` footnote
``; ./path`` nav suffix. (The earlier gap -- ``_parse_types_block`` dropping
that suffix -- is closed: ``ParsedLvnet.types`` now maps a name to a
``ParsedTypeDef`` carrying both the structural def and its ``path``, and
``reconstruct_module`` restores ``LVType.typedef_path`` from it so
``_render_lvnet_types`` re-renders the exact suffix.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import load_vi_by_path
from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import NetlistModule, build_netlist_from_graph, render_lvnet
from lvkit.load_mode import LoadMode

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_FLEX_ROOT = Path(".lvkit/cache/samples/lv-flex-channel-examples")

# Same corpus VIs as test_lvnet_roundtrip.py's _ROUND_TRIP_CASES -- this is
# the SAME text idempotence question asked one level deeper (full model
# reconstruction, not just a comparable-projection match).
_IDEMPOTENCE_CASES = [
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TestLoader" / "loadTestsFromTestCase.vi",
        _JKI_SOURCE_ROOT,
        id="loadTestsFromTestCase",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TestCase" / "run.vi",
        _JKI_SOURCE_ROOT,
        id="TestCase_run",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TestSuite" / "run.vi",
        _JKI_SOURCE_ROOT,
        id="TestSuite_run",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Classes" / "TextTestRunner" / "run.vi",
        _JKI_SOURCE_ROOT,
        id="TextTestRunner_run",
    ),
    pytest.param(
        _FLEX_ROOT / "WaveGen" / "WaveGen.vi",
        _FLEX_ROOT / "WaveGen",
        id="WaveGen",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Menu Launch" / "VI Tester Menu Launch.vi",
        _JKI_SOURCE_ROOT,
        id="VI_Tester_Menu_Launch",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT
        / "User Interfaces"
        / "Graphical Test Runner"
        / "Graphical Test Runner - Main UI - .vi",
        _JKI_SOURCE_ROOT,
        id="Graphical_Test_Runner_Main_UI",
    ),
]


def _load(vi_path: Path, search_root: Path) -> tuple[InMemoryVIGraph, str] | None:
    if not vi_path.exists():
        return None
    try:
        # load_vi_by_path returns load_vi's OWN key for vi_path -- never
        # re-derived from vi_path.name, which collides across same-named
        # VIs (e.g. TestCase.lvclass:run.vi vs TestSuite.lvclass:run.vi).
        return load_vi_by_path(
            vi_path, LoadMode.MINIMAL, search_paths=[search_root], layout=False
        )
    except Exception:
        return None


def _first_line_diff(a: str, b: str) -> str:
    a_lines = a.split("\n")
    b_lines = b.split("\n")
    for i, (x, y) in enumerate(zip(a_lines, b_lines)):
        if x != y:
            return f"line {i + 1}: original={x!r} reconstructed={y!r}"
    if len(a_lines) != len(b_lines):
        return (
            f"line count differs: original has {len(a_lines)}, "
            f"reconstructed has {len(b_lines)} "
            f"(first extra line: "
            f"{(b_lines[len(a_lines) :] or a_lines[len(b_lines) :])[0]!r})"
        )
    return "<no diff found -- texts equal?>"  # pragma: no cover


@pytest.mark.needs_samples
@pytest.mark.parametrize("vi_path,search_root", _IDEMPOTENCE_CASES)
def test_reconstruct_module_reproduces_verbose_lvnet_byte_for_byte(
    vi_path: Path, search_root: Path
) -> None:
    loaded = _load(vi_path, search_root)
    if loaded is None:
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    graph, vi_name = loaded
    module: NetlistModule = build_netlist_from_graph(graph, vi_name)
    original_text = render_lvnet(module, display_name=vi_path.name, verbose=True)

    parsed = parse_lvnet(original_text)
    reconstructed = reconstruct_module(parsed)
    reconstructed_text = render_lvnet(
        reconstructed, display_name=vi_path.name, verbose=True
    )

    assert reconstructed_text == original_text, (
        f"reconstruction did not re-render byte-identically for "
        f"{vi_path.name!r}: {_first_line_diff(original_text, reconstructed_text)}"
    )
