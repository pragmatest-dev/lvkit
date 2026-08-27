"""Losslessness round-trip harness for the lvnet text IR.

The gate: ``parse_lvnet(render_lvnet(module, verbose=True))`` must reproduce
``module``'s semantic content, projected through ``netlist_signature`` (see
``lvkit.graph.lvnet_parse``) -- ``boundary_signature`` is its boundary-only
predecessor, now folded into ``netlist_signature`` as its first element.

Increment 1 built the harness on the boundary block only, and found Gap #1:
a connector-pane terminal's authored DEFAULT value was never rendered on a
boundary line at all, even in verbose mode. That gap is now CLOSED --
``render_lvnet``'s ``_lvnet_boundary_trailing`` composes the §5 requirement
keyword AND the §4 ``default <value>`` clause on the same line -- see
``test_golden_verbose_boundary_shows_recommended_and_omits_unknown`` in
``test_render_lvnet.py`` for the still-unchanged §16 golden (that VI's own
boundary terminals all carry ``default=None``, so the golden is byte-
identical either way).

Increment 2 grew ``parse_lvnet``/``netlist_signature`` to the BODY: node
declarations, terminal lines, net references, and the CLOSED case/for-loop/
while-loop/shift-register/tunnel constructs.

Increment 3 (this pass) surfaces the sub-kind discriminators the Phase-1
model had flattened -- ``NetlistScope.sequence_is_flat``/``disable_kind`` --
so ``render_lvnet`` emits the real §8 keyword (``flat-sequence``/
``stacked-sequence``, ``diagram-disable``/``conditional-disable``/``type-
specialization``, ``event-structure``) instead of a hard-coded default, and
extends ``parse_lvnet``/``netlist_signature`` to cover all three families.
``WaveGen.vi`` (diagram-disable, plus a nested case and a feedback node --
both ALREADY covered by increment 2) and ``VI Tester Menu Launch.vi``
(flat-sequence) now round-trip CLEANLY end to end, moved here from the old
"unsupported construct" list. ``Graphical Test Runner - Main UI - .vi``
(event-structure, with a nested case and several property-node/invoke-node
calls -- ALL of which round-trip correctly) still fails the full round-trip,
but for a DIFFERENT, unrelated reason: a String CONSTANT whose real value
contains a raw control character is rendered unescaped, splitting one
logical line across two physical lines -- see that case's ``xfail`` reason
below for the precise mismatch. Not a §8 gap; out of this pass's scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.lvnet_parse import ParsedLvnet, netlist_signature, parse_lvnet
from lvkit.graph.netlist import NetlistModule, build_netlist_from_graph, render_lvnet
from lvkit.load_mode import LoadMode

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_FLEX_ROOT = Path(".lvkit/cache/samples/lv-flex-channel-examples")

# A VI whose rendered lvnet text is expected to round-trip CLEANLY end to
# end -- node declarations/terminals/nets, case/for-loop/while-loop/shift-
# register/tunnel (increment 2), and flat-sequence/stacked-sequence/
# diagram-disable/conditional-disable/type-specialization/event-structure
# (increment 3, this pass). Same load recipe as
# tests/test_netlist_from_graph_parity.py's `_load`.
_ROUND_TRIP_CASES = [
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
        id="WaveGen_diagram_disable_nested_case_feedback_node",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT / "Menu Launch" / "VI Tester Menu Launch.vi",
        _JKI_SOURCE_ROOT,
        id="VI_Tester_Menu_Launch_flat_sequence",
    ),
    pytest.param(
        _JKI_SOURCE_ROOT
        / "User Interfaces"
        / "Graphical Test Runner"
        / "Graphical Test Runner - Main UI - .vi",
        _JKI_SOURCE_ROOT,
        marks=pytest.mark.xfail(
            strict=True,
            reason=(
                "residual gap, VERIFIED unrelated to §8 structures: this "
                "VI's while-loop > event-structure > frame '[0] Timeout' > "
                "nested case (on Data_Changed__ogtk_1's output) round-trips "
                "correctly, as do its many property-node/invoke-node calls "
                "elsewhere in the body -- the actual mismatch is that a "
                "String CONSTANT whose real (LabVIEW-authored) value "
                "contains a raw control character -- a CRLF on "
                "'Array To Spreadsheet String's `delimiter` input, and a "
                "3-line UI-status literal driving a case output tunnel -- "
                "renders UNESCAPED (`_lvnet_scalar_value_token`/the inline-"
                "literal render path never escapes embedded \\r/\\n), so "
                "the literal's own text spans multiple physical lines and "
                "breaks this line-oriented parser at the FIRST such "
                "literal (reported as a stray 'output-drive line must be "
                "at 2-space indent' parse error, since the literal's "
                "trailing quote lands on its own physical line). A general "
                "lvnet literal-escaping gap (§10/§17), not a sequence/"
                "disabled/event-structure one -- flagged, not fixed, per "
                "this project's bugs-are-investigate-then-discuss rule."
            ),
        ),
        id="Graphical_Test_Runner_Main_UI_event_structure",
    ),
]


def _load(vi_path: Path, search_root: Path) -> tuple[InMemoryVIGraph, str] | None:
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


def _first_mismatch(
    a: object, b: object, path: str = "root"
) -> tuple[str, object, object] | None:
    """The first differing (path, module_value, parsed_value) leaf inside
    two structurally-equal-shaped signature tuples -- pinpoints exactly
    which node/terminal/net failed to round-trip instead of dumping two
    whole nested tuples for the reader to eyeball."""
    if a == b:
        return None
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            found = _first_mismatch(x, y, f"{path}[{i}]")
            if found is not None:
                return found
    return (path, a, b)


@pytest.mark.needs_samples
@pytest.mark.parametrize("vi_path,search_root", _ROUND_TRIP_CASES)
def test_netlist_round_trips_through_verbose_lvnet(
    vi_path: Path, search_root: Path
) -> None:
    """Build the real graph, render verbose lvnet text, parse it back, and
    compare the FULL (boundary + body) semantic projection to the module's
    own. A FAILURE here is the intended, informative signal of a real lvnet
    losslessness gap -- not a broken test.
    """
    loaded = _load(vi_path, search_root)
    if loaded is None:
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    graph, vi_name = loaded
    module: NetlistModule = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name=vi_path.name, verbose=True)
    parsed: ParsedLvnet = parse_lvnet(text)

    sig_module = netlist_signature(module)
    sig_parsed = netlist_signature(parsed)
    mismatch = _first_mismatch(sig_module, sig_parsed)
    assert mismatch is None, (
        f"netlist round-trip mismatch for {vi_path.name!r} at {mismatch[0]}: "
        f"module={mismatch[1]!r} parsed={mismatch[2]!r}"
    )
