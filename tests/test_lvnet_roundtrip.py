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

Increment 3 surfaces the sub-kind discriminators the Phase-1 model had
flattened -- ``NetlistScope.sequence_is_flat``/``disable_kind`` -- so
``render_lvnet`` emits the real §8 keyword (``flat-sequence``/``stacked-
sequence``, ``diagram-disable``/``conditional-disable``/``type-
specialization``, ``event-structure``) instead of a hard-coded default, and
extends ``parse_lvnet``/``netlist_signature`` to cover all three families.
``WaveGen.vi`` (diagram-disable, plus a nested case and a feedback node --
both ALREADY covered by increment 2) and ``VI Tester Menu Launch.vi``
(flat-sequence) round-trip CLEANLY end to end.

Increment 4 closes the LAST gap increment 3 found: ``Graphical Test Runner -
Main UI - .vi`` (event-structure, with a nested case and several property-
node/invoke-node calls -- all of which already round-tripped correctly)
still failed the full round-trip for a DIFFERENT, unrelated reason -- a
String CONSTANT whose real value contains a raw control character (a CRLF
on ``Array To Spreadsheet String``'s ``delimiter`` input, and a 3-line
UI-status literal driving a case output tunnel) rendered UNESCAPED,
splitting one logical line across two physical lines and breaking this
line-oriented parser. ``netlist.py``'s new ``_lvnet_literal_token`` (the
one lvnet string-literal renderer, replacing the old ``_lvnet_scalar_value_
token``) now escapes a backslash/double-quote/newline/CR/tab (any other C0
control char as a hex escape), so the literal stays on ONE physical line;
this VI now round-trips CLEANLY too, closing the former ``xfail`` below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import load_vi_by_path
from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.lvnet_parse import (
    LvnetParseError,
    ParsedLvnet,
    _scan_quoted_literal,
    _unescape_lvnet_string,
    netlist_signature,
    parse_lvnet,
)
from lvkit.graph.netlist import (
    NetlistConstant,
    NetlistModule,
    build_netlist_from_graph,
)
from lvkit.graph.render_lvnet import (
    _lvnet_ambiguous_named_types,
    _lvnet_literal_token,
    render_lvnet,
)
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
        id="Graphical_Test_Runner_Main_UI_event_structure",
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

    # A name genuinely ambiguous in THIS module (§10's flat one-entry-per-
    # name `types :` footnote can't distinguish two occurrences of the same
    # nominal typedef resolving to different structures -- e.g. a Variant-
    # typed User Event data field, seen on WaveGen.vi's `Event Data.ctl`)
    # is computed ONCE from the real module and fed to BOTH sides, so the
    # strengthened type comparison excludes it identically on both.
    ambiguous = _lvnet_ambiguous_named_types(module)
    sig_module = netlist_signature(module, ambiguous)
    sig_parsed = netlist_signature(parsed, ambiguous)
    mismatch = _first_mismatch(sig_module, sig_parsed)
    assert mismatch is None, (
        f"netlist round-trip mismatch for {vi_path.name!r} at {mismatch[0]}: "
        f"module={mismatch[1]!r} parsed={mismatch[2]!r}"
    )


def test_control_char_string_constant_round_trips_on_one_physical_line() -> None:
    """Focused, corpus-independent regression for the escaping fix itself
    (increment 4): a labeled ``constant`` node whose real value carries a
    CRLF, a tab, an embedded double-quote, and a literal backslash -- the
    exact shape of value that broke ``Graphical Test Runner - Main UI -
    .vi``'s round-trip (a CRLF splitting the ``constant ... = ...`` line
    across two physical lines, which then broke this line-oriented parser
    downstream) -- must render as ONE physical line and round-trip through
    ``parse_lvnet``/``netlist_signature`` byte-for-byte.
    """
    control_char_value = 'Line one\r\nLine two\twith "quotes" and a \\backslash'
    escaped = _lvnet_literal_token(control_char_value)

    const = NetlistConstant(
        uid="const_1",
        name="StatusText",
        occurrence=None,
        type="String",
        # OLD render_netlist-parity text -- unused by render_lvnet.
        value=f"'{control_char_value}'",
        lvnet_value=escaped,
    )
    module = NetlistModule(vi_name="Synthetic.vi", inputs=[], outputs=[], body=[const])

    text = render_lvnet(module, verbose=True)
    const_lines = [
        line for line in text.split("\n") if line.strip().startswith("constant ")
    ]
    # Phase 2: body items now nest under "block-diagram :" -- one level (2
    # spaces) deeper than the old top-level indent.
    assert const_lines == [f"    constant StatusText_const_1 : String = {escaped}"]

    parsed = parse_lvnet(text)
    assert netlist_signature(module) == netlist_signature(parsed)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "plain",
        'has "quotes" inside',
        "trailing backslash \\",
        "CRLF\r\nsplit across lines",
        "tab\there",
        "null\x00byte",
        "bell\x07and\x1fus",
    ],
)
def test_lvnet_string_literal_escape_unescape_round_trip(value: str) -> None:
    """``_unescape_lvnet_string`` is the exact reverse of
    ``_lvnet_literal_token`` (md §4/§10) -- the "reverse the escapes
    symmetrically" requirement -- for every control-char shape it's meant
    to cover, not just the corpus VI's own two literals."""
    token = _lvnet_literal_token(value)
    assert "\n" not in token and "\r" not in token, (
        f"escaped token must stay on one physical line: {token!r}"
    )
    assert _unescape_lvnet_string(token) == value


def test_scan_quoted_literal_skips_escaped_quote() -> None:
    """A value containing an escaped double-quote must not end the scan
    early at the ``\\"`` -- only a REAL (unescaped) closing quote does."""
    token = _lvnet_literal_token('say "hi" please')
    assert token == '"say \\"hi\\" please"'
    end = _scan_quoted_literal(token, 0)
    assert end == len(token)


def test_scan_quoted_literal_raises_on_unterminated_quote() -> None:
    with pytest.raises(LvnetParseError):
        _scan_quoted_literal('"unterminated', 0)


def test_unescape_lvnet_string_rejects_malformed_escape() -> None:
    with pytest.raises(LvnetParseError):
        _unescape_lvnet_string('"bad \\q escape"')
