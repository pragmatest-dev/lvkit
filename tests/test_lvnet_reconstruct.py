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

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import NetlistModule, build_netlist_from_graph, render_lvnet
from tests.corpus_graphs import load_corpus_vi

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_FLEX_ROOT = Path(".lvkit/cache/samples/lv-flex-channel-examples")
_AF_ROOT = Path(".lvkit/cache/samples/actor-framework")
_ICON_EDITOR_ROOT = Path(".lvkit/cache/samples/ni-labview-icon-editor")
_DCAF_ROOT = Path(".lvkit/cache/samples/DCAF-DAQModule")
_MEASUREMENT_PLUGIN_ROOT = Path(".lvkit/cache/samples/measurement-plugin-labview")

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
    pytest.param(
        _AF_ROOT / "Tooling" / "Force Icon Editor to Unload.vi",
        _AF_ROOT,
        id="Force_Icon_Editor_to_Unload",
    ),
    pytest.param(
        _ICON_EDITOR_ROOT / "resource" / "plugins" / "lv_icon.vi",
        _ICON_EDITOR_ROOT,
        id="lv_icon_global_variables",
        marks=pytest.mark.xdist_group("lv_icon.vi"),
    ),
    pytest.param(
        _ICON_EDITOR_ROOT
        / "resource"
        / "plugins"
        / "NIIconEditor"
        / "Global Variables"
        / "Data.vi",
        _ICON_EDITOR_ROOT,
        id="Data_global_vi",
    ),
    pytest.param(
        _DCAF_ROOT / "source" / "module" / "execution" / "init.vi",
        _DCAF_ROOT,
        id="DCAF_init_in_place_element_border_elements",
    ),
    pytest.param(
        _MEASUREMENT_PLUGIN_ROOT
        / "Source"
        / "Runtime"
        / "Clients"
        / "Session Management V1"
        / "Session Reservation"
        / "Initialize Sessions Impl.vi",
        _MEASUREMENT_PLUGIN_ROOT,
        id="Initialize_Sessions_Impl_in_place_element_border_elements",
    ),
    pytest.param(
        _DCAF_ROOT / "source" / "editor node" / "Callback Change Tag2.vi",
        _DCAF_ROOT,
        id="Callback_Change_Tag2_in_place_element_array_border_nodes",
    ),
]


def _load(vi_path: Path, search_root: Path) -> tuple[InMemoryVIGraph, str] | None:
    if not vi_path.exists():
        return None
    try:
        # load_vi_by_path returns load_vi's OWN key for vi_path -- never
        # re-derived from vi_path.name, which collides across same-named
        # VIs (e.g. TestCase.lvclass:run.vi vs TestSuite.lvclass:run.vi).
        return load_corpus_vi(vi_path, search_root)
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
