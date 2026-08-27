"""`render_lvnet` -- the lvnet TEXT surface (Phase A).

See `docs/_internal/design/netlist-language.md` for the full spec ("lvnet");
§16 is the golden reference render this module's first test targets.

IMPORTANT caveat on the golden fixture below: it is NOT a verbatim copy of
§16's hand-written markdown text. Building `render_lvnet` and comparing its
REAL output against the ACTUAL `loadTestsFromTestCase.vi` graph surfaced five
places where the md's hand-typed example diverges from the real VI/from its
own stated rules -- each is called out at its exact line below with the
receipt. None of these are invented syntax or guessed behavior; every one
traces to a verified fact (a real terminal name, a real wire, or the md's
own §9 prose). See the implementation report for the full writeup.
"""

# ruff: noqa: E501 -- the golden fixture below is DATA (a byte-exact VI
# render), not code; wrapping it would break the byte comparison.

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.interface_order import WiringRequirement
from lvkit.graph.netlist import (
    ConnectorPaneTerminal,
    _lvnet_requirement_trailing,
    _render_term_group,
    _TermLine,
    build_netlist_from_graph,
    render_lvnet,
)
from lvkit.load_mode import LoadMode

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_GOLDEN_VI = _JKI_SOURCE_ROOT / "Classes" / "TestLoader" / "loadTestsFromTestCase.vi"

# The FULL, verified `render_lvnet` output for the real `loadTestsFromTestCase.vi`
# graph (captured by actually running the renderer against the real corpus VI,
# then hand-annotated below with every place it diverges from §16's literal
# markdown text -- see the module docstring and the implementation report).
_GOLDEN_LVNET = '''\
vi loadTestsFromTestCase.vi :
  in   TestLoader in       : TestLoader.lvclass
  in   TestCase            : TestCase.lvclass
  in   error in (no error) : Error
  out  TestLoader out      : TestLoader.lvclass
  out  TestSuite           : TestSuite.lvclass
  out  error out           : Error

  case error in (no error) :
    frame "No Error" :
      subVI listAllTestMethods_1 : TestCase.lvclass:listAllTestMethods.vi
        in   TestCase in         : TestCase.lvclass = TestCase
        in   error in (no error) : Error            = error in (no error)
        out  TestCase out        : TestCase.lvclass
        out  test methods        : [String]
        out  error out           : Error
      for-loop :
        subVI TestCase_Init_1 : TestCase.lvclass:TestCase_Init.vi
          in   TestCase in            : TestCase.lvclass = listAllTestMethods_1::TestCase out
          in   methodName ("runTest") : String           = listAllTestMethods_1::test methods
          in   GUID ("")              : String           default ""
          in   error in (no error)    : Error            = loop0::shift0
          out  TestCase out           : TestCase.lvclass
          out  error out              : Error
        shift-register loop0::shift0 :
          init = listAllTestMethods_1::error out
          each = TestCase_Init_1::error out
        tunnel loop0::out0 : auto-indexing = TestCase_Init_1::TestCase out
      subVI TestSuite_Init_1 : TestSuite.lvclass:TestSuite_Init.vi
        in   TestSuite in                    : TestSuite.lvclass default
        in   tests (none)                    : [LabVIEW Object]  = loop0::out0
        in   testSuiteStatusChanged EventRef : UserEvent refnum{suiteStatusChanged--Cluster} default
        in   GUID ("")                       : String            default ""
        in   error in (no error)             : Error             = loop0::shift0
        out  TestSuite out                   : TestSuite.lvclass
        out  error out                       : Error
      case0::out0 = TestSuite_Init_1::error out
      case0::out1 = TestLoader in
      case0::out2 = TestSuite_Init_1::TestSuite out
    frame "Error" :
      case0::out0 = error in (no error)
      case0::out1 = TestLoader in
      case0::out2 = (default TestSuite.lvclass)

  TestLoader out = case0::out1
  TestSuite      = case0::out2
  error out      = case0::out0\
'''
# Divergences from §16's literal markdown text (each verified against the real
# graph, not guessed) -- captured by ACTUALLY running the renderer against the
# real corpus VI (`.tmp/render_golden.py`), never hand-edited to "look nicer":
#
# 1. Every node instance now declares `<keyword> <handle> : <component>` --
#    `subVI listAllTestMethods_1 : TestCase.lvclass:listAllTestMethods.vi` --
#    per the REVISED md's §7/§9 handle rule (strip `.vi`/`.ctl`, despace,
#    suffix `_N` from 1 even for a lone instance) and every net reference
#    to that instance uses the SAME handle with the `::` scope-resolution
#    separator (§9) -- e.g. `listAllTestMethods_1::TestCase out`,
#    `loop0::shift0`, `case0::out1`. This supersedes §16's OLD
#    `subVI listAllTestMethods.vi` / `listAllTestMethods.vi.TestCase out` /
#    `loop0.shift0` forms, which predate the revision.
# 2. Port names on every subVI's OWN terminal lines ("error in (no error)",
#    "methodName (\"runTest\")", "GUID (\"\")", "tests (none)") are shown
#    VERBATIM -- these are the terminal's REAL, LabVIEW-authored FP-control
#    label text (verified: `front_panel.py`'s `label_elem.text` straight off
#    the parsed VI, not a synthesized suffix). §16 shows these stripped of
#    the trailing "(...)" annotation on a subVI's OWN port lines (but keeps
#    it on loadTestsFromTestCase.vi's OWN boundary "error in (no error)",
#    since that text doubles as the net's own identity). No documented rule
#    describes this stripping (not in §2-§10, not in §17) -- inventing a
#    strip-trailing-parenthetical heuristic would violate the project's
#    "no string-matching/heuristics" law, so the verbatim authored name is
#    kept instead. Flagged as OPEN for the maintainer.
# 3. `TestSuite_Init.vi`'s call carries a 5th REAL input --
#    "testSuiteStatusChanged EventRef" (index 9, a genuine, unwired
#    UserEvent-refnum parameter on its connector pane) -- that §16 omits
#    entirely. Verified directly off the graph node's own terminals; almost
#    certainly a transcription gap in the hand-written example, not a
#    rendering choice.
# 4. `TestSuite_Init.vi`'s "error in" wires from `loop0::shift0` (the
#    for-loop's shift-register net), NOT `listAllTestMethods_1::error out`
#    as §16 shows -- verified against the REAL wire (an incoming edge on
#    that terminal), and independently corroborated by the ALREADY-SHIPPED,
#    already-tested `render_netlist`/`build_netlist` (Operation-based) path,
#    untouched this session, which resolves the SAME net (modulo the ``.``
#    vs ``::`` separator). §16's text does not match the real VI's
#    dataflow here.
# 5. The `constant GUID_1 : String = "TC-001"` node does not appear: the
#    real VI's `TestCase_Init.vi` call has NO "GUID" input at all wired to a
#    constant -- its GUID input is genuinely UNWIRED (verified: no incoming
#    edge on that terminal), and the whole VI has ZERO `ConstantNode`s on
#    its own diagram (verified: `ctx.constants` is empty). §16's constant
#    example is illustrative of the `constant` SYNTAX, not sourced from this
#    real VI.
# 6. One alignment inconsistency: §16's own `TestSuite_Init.vi` block prints
#    `out  TestSuite out: TestSuite.lvclass` with ZERO gap before the colon,
#    one space short of the "(max name length in this block) + 1" rule
#    every other aligned block in §16 follows (verified by measuring exact
#    column positions in 3 other blocks, all consistently max+1). Rendered
#    here with the same +1 padding as everywhere else, for one consistent
#    rule rather than reproducing an isolated typo.
# 7. `testSuiteStatusChanged EventRef`'s type renders
#    `UserEvent refnum{suiteStatusChanged--Cluster}` -- the inner cluster's
#    OWN `typedef_name` (verified: `LVType.typedef_name` on that terminal's
#    element type is literally `"...suiteStatusChanged--Cluster"`, stripped
#    the same way `type_descriptor()` always has) makes it a NAMED type, so
#    lvnet's terse renderer (§10/§11, this pass) shows the bare name alone
#    -- never the full `{TestSuite, suiteStatus}` field expansion, which is
#    verbose-only and there is no verbose mode yet. Recursing into the
#    refnum's element this way is the SAME mechanism that turns a 300-member
#    named enum into just its name elsewhere in the corpus (see the
#    `Graphical Test Runner` render in the implementation report).
# 8. Two terminals' defaults render the BARE word `default` with no
#    parenthetical -- `TestSuite in` and `testSuiteStatusChanged EventRef`,
#    both class/refnum types with no literal default
#    (`DefaultValue.literal == "?"`) -- per §4's revised terminal-line rule:
#    the `: <Type>` column already names the type, so repeating it as
#    `default (default TestSuite.lvclass)` (§16's OLD text, and a literal
#    double "default") is redundant on a terminal line specifically. A
#    DRIVE-POSITION default (`case0::out2 = (default TestSuite.lvclass)`,
#    below) is UNCHANGED -- it has no type column of its own to lean on.
#
# NOTE: under the revised §9 rule every node-port net is ALWAYS fully
# qualified as `<handle>::<port>` -- never a bare, ambiguity-gated form (the
# OLD lvnet draft's `ambiguous_bares`-gated qualification, which this test
# file's PRIOR revision flagged as a divergence at "test methods"/"TestSuite
# out", no longer applies: those two ARE now qualified, like every other
# node-port reference, consistent with §9's one uniform rule).


def _load_golden() -> tuple[InMemoryVIGraph, str] | None:
    if not _GOLDEN_VI.exists():
        return None
    graph = InMemoryVIGraph()
    try:
        graph.load_vi(
            str(_GOLDEN_VI),
            LoadMode.MINIMAL,
            search_paths=[_JKI_SOURCE_ROOT],
            layout=False,
        )
    except Exception:
        return None
    vi_name = graph.resolve_vi_name(_GOLDEN_VI.name)
    return graph, vi_name


@pytest.mark.needs_samples
def test_golden_load_tests_from_test_case_matches_verified_render() -> None:
    """Byte-for-byte against the VERIFIED render (see module docstring for
    the 6 documented divergences from §16's literal markdown text)."""
    loaded = _load_golden()
    if loaded is None:
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = loaded
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name="loadTestsFromTestCase.vi")
    assert text == _GOLDEN_LVNET


# ============================================================
# Phase-B slice: verbose/terse switch + the §5 tri-state `wiring_rule`.
# ============================================================

# The golden VI's own boundary block, VERBOSE -- captured the same way as
# `_GOLDEN_LVNET` (actually running the renderer, not hand-typed). Every
# terminal here resolves REAL `wiring_rule` values off the graph: the four
# non-error terminals are all authored `Recommended` (rule=2, or rule=1 on
# an OUTPUT, which `requirement_state` folds to Recommended -- see
# `interface_order.requirement_rank`'s docstring); `TestLoader in`/`error
# out` are unresolved (rule=0/INVALID) so they render NO keyword at all,
# same as terse (§5: never claim a resolved state that was never authored).
_GOLDEN_LVNET_VERBOSE_BOUNDARY = """\
vi loadTestsFromTestCase.vi :
  in   TestLoader in       : TestLoader.lvclass
  in   TestCase            : TestCase.lvclass   recommended
  in   error in (no error) : Error              recommended
  out  TestLoader out      : TestLoader.lvclass recommended
  out  TestSuite           : TestSuite.lvclass  recommended
  out  error out           : Error"""


@pytest.mark.needs_samples
def test_golden_verbose_boundary_shows_recommended_and_omits_unknown() -> None:
    """`verbose=True` on the SAME real golden VI: its boundary lines gain the
    §5 bare requirement keyword; the two UNKNOWN (unresolved) terminals
    render with none, identically to terse. This is the actual, run render
    (not hand-typed) -- see the fixture's own comment for the real
    `wiring_rule` values behind each line."""
    loaded = _load_golden()
    if loaded is None:
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = loaded
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(
        module, display_name="loadTestsFromTestCase.vi", verbose=True
    )
    boundary_block = text.split("\n\n", 1)[0]
    assert boundary_block == _GOLDEN_LVNET_VERBOSE_BOUNDARY

    # terse (default / verbose=False) is completely unaffected.
    terse = render_lvnet(module, display_name="loadTestsFromTestCase.vi")
    assert terse == _GOLDEN_LVNET


def test_verbose_requirement_keyword_required_and_optional() -> None:
    """`required`/`optional` on real `ConnectorPaneTerminal`s, exercised
    directly against the SAME functions `render_lvnet` calls
    (`_lvnet_requirement_trailing` + `_render_term_group`).

    Not sourced from a real VI: a bounded scan of `loadTestsFromTestCase.vi`
    plus ~8 Create/Init-style top-level VIs across 3 other sample corpora
    (JKI-VI-Tester, LabVIEW-OOP-Classes, actor-framework -- see
    `.tmp/scan_requirement_states*.py`) found only `Recommended`/`Unknown`
    wiring rules ever authored at a top-level connector pane -- never an
    explicit Required INPUT or an Optional terminal. `Recommended`/`Unknown`
    are already covered against real data by
    `test_golden_verbose_boundary_shows_recommended_and_omits_unknown`
    above; this test covers the two states real corpus data didn't exercise,
    against the real rendering code path.
    """
    required = ConnectorPaneTerminal(
        name="in1",
        type="DBL",
        direction="input",
        index=0,
        wiring_requirement=WiringRequirement.REQUIRED,
        default=None,
    )
    optional = ConnectorPaneTerminal(
        name="in2",
        type="String",
        direction="input",
        index=1,
        wiring_requirement=WiringRequirement.OPTIONAL,
        default="",
    )
    unknown = ConnectorPaneTerminal(
        name="in3",
        type="Boolean",
        direction="input",
        index=2,
        wiring_requirement=WiringRequirement.UNKNOWN,
        default=False,
    )
    assert _lvnet_requirement_trailing(required) == "required"
    assert _lvnet_requirement_trailing(optional) == "optional"
    assert _lvnet_requirement_trailing(unknown) is None

    entries = [
        _TermLine("in ", t.name, t.type, _lvnet_requirement_trailing(t))
        for t in (required, optional, unknown)
    ]
    lines = _render_term_group(entries, "  ")
    assert lines == [
        "  in   in1 : DBL     required",
        "  in   in2 : String  optional",
        "  in   in3 : Boolean",
    ]


# ============================================================
# OPEN-invariant (md §7, revised): property-node/invoke-node/feedback-node
# are now FULLY designed -- handle + component + terminals, NEVER a TODO.
# in-place-element/formula-node render the same handle+terminal block but
# still end with exactly one TODO for their one undesigned part.
# local-variable (Local/Global Variable) is the ONE kind still fully
# deferred: a bare keyword line, no handle, immediately followed by a TODO.
# No construct anywhere may emit invented syntax beyond this.
# ============================================================

_PROPERTY_NODE_VI = (
    _JKI_SOURCE_ROOT
    / "User Interfaces"
    / "Graphical Test Runner"
    / "Graphical Test Runner - Main UI - .vi"
)
_FEEDBACK_VI = Path(".lvkit/cache/samples/lv-flex-channel-examples/WaveGen/WaveGen.vi")
_FEEDBACK_SEARCH_ROOT = Path(".lvkit/cache/samples/lv-flex-channel-examples/WaveGen")

# Kinds whose declaration line is now `<keyword> <handle> : <component>` and
# must NEVER be followed by a TODO -- §7 designed them fully.
_FULLY_DESIGNED_KEYWORDS = ("property-node", "invoke-node", "feedback-node")
# Kinds whose declaration line is the SAME `<keyword> <handle> : <component>`
# form, but whose own block still ends with exactly one trailing TODO for
# the one part §17 item 6 leaves undesigned.
_STILL_OPEN_KEYWORDS = ("in-place-element", "formula-node")


def _assert_no_invented_open_syntax(text: str) -> dict[str, int]:
    """Structural invariant over every §7 node-kind row that isn't plain
    ``subVI``/``function``/``constant``. Returns a count per keyword found
    (0 for one not exercised by this particular VI) so callers can assert
    coverage where they expect it.

    - ``local-variable`` (Local/Global Variable) is STILL fully deferred:
      a BARE keyword line (no handle, no component) immediately followed by
      exactly one ``# TODO(lvnet): ...`` line -- never anything else.
    - ``property-node``/``invoke-node`` are now fully designed: their header
      carries `` : <more-specific-type>`` (the object class [+ method]) and is
      never immediately followed by a TODO.
    - ``feedback-node`` is fully designed too, but is a state REGISTER, so it
      has NO more-specific-type: its header is
      ``feedback-node <handle> (<N> iteration[s]) :`` (the trailing ``:`` opens
      its ``init``/``each`` block), and is never followed by a TODO.
    - ``in-place-element``/``formula-node`` render that SAME
      handle+component header, then a full terminal block, then EXACTLY one
      trailing ``# TODO(lvnet): ...`` line as the last line of their own
      block (before the next sibling, found by indentation returning to the
      header's own level) -- never more, never invented.
    """
    lines = text.splitlines()
    counts = {
        k: 0
        for k in ("local-variable", *_FULLY_DESIGNED_KEYWORDS, *_STILL_OPEN_KEYWORDS)
    }

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "local-variable":
            counts["local-variable"] += 1
            assert i + 1 < len(lines), "local-variable has no following line"
            next_line = lines[i + 1].strip()
            assert next_line.startswith("# TODO(lvnet):"), (
                f"local-variable must be followed by a TODO placeholder, "
                f"got {next_line!r}"
            )
            continue

        for kw in _FULLY_DESIGNED_KEYWORDS:
            if stripped.startswith(kw + " "):
                counts[kw] += 1
                assert i + 1 < len(lines), f"{kw!r} header has no following line"
                next_line = lines[i + 1].strip()
                assert not next_line.startswith("# TODO(lvnet):"), (
                    f"{kw!r} is fully designed (md §7) -- must not emit a "
                    f"TODO, got {next_line!r}"
                )
                if kw == "feedback-node":
                    # a state register: NO more-specific-type; the trailing
                    # `:` opens its init/each block (md §7).
                    assert stripped.endswith(":"), (
                        f"feedback-node header must open a block: {stripped!r}"
                    )
                    assert next_line.startswith("init ="), (
                        f"feedback-node must be followed by its init line, "
                        f"got {next_line!r}"
                    )
                else:
                    assert " : " in stripped, (
                        f"{kw!r} header missing its more-specific-type: {stripped!r}"
                    )

        for kw in _STILL_OPEN_KEYWORDS:
            if stripped.startswith(kw + " "):
                counts[kw] += 1
                assert " : " in stripped, f"{kw!r} header missing its component: {stripped!r}"
                header_indent = len(line) - len(line.lstrip(" "))
                block: list[str] = []
                j = i + 1
                while j < len(lines):
                    candidate = lines[j]
                    if candidate.strip() == "":
                        break
                    candidate_indent = len(candidate) - len(candidate.lstrip(" "))
                    if candidate_indent <= header_indent:
                        break
                    block.append(candidate.strip())
                    j += 1
                assert block, f"{kw!r} header has no following terminal block: {stripped!r}"
                todo_lines = [ln for ln in block if ln.startswith("# TODO(lvnet):")]
                assert len(todo_lines) == 1, (
                    f"{kw!r} block must carry exactly one TODO, got {todo_lines!r}"
                )
                assert block[-1].startswith("# TODO(lvnet):"), (
                    f"{kw!r} block's TODO must be its LAST line, got {block[-1]!r}"
                )

    return counts


@pytest.mark.needs_samples
def test_property_and_invoke_nodes_render_handle_and_component_no_todo() -> None:
    """A real VI with both Property Nodes and Invoke Nodes: every one now
    renders `<keyword> <handle> : <ObjectClass[.Method]>` -- never a bare
    keyword, never a TODO (md §7, revised)."""
    if not _PROPERTY_NODE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_PROPERTY_NODE_VI),
        LoadMode.MINIMAL,
        search_paths=[_JKI_SOURCE_ROOT],
        layout=False,
    )
    vi_name = graph.resolve_vi_name(_PROPERTY_NODE_VI.name)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module)
    counts = _assert_no_invented_open_syntax(text)
    assert counts["property-node"] > 0, "expected at least one property-node"
    assert counts["invoke-node"] > 0, "expected at least one invoke-node"

    # Structural spot-checks against REAL data (verified via
    # `.tmp/render_open_kinds.py`): a Boolean property (`ObjectClass` =
    # `Bool`) and a Tree-control method call (`ObjectClass.Method`).
    assert "property-node Abort_2 : Bool" in text
    assert "invoke-node Test_Hierarchy_Tree_1 : Tree (strict).Point To Row Column" in text


@pytest.mark.needs_samples
def test_feedback_node_renders_handle_component_init_each() -> None:
    """A real split Feedback Node (1-iteration): renders
    `feedback-node <net> (<N> iteration[s]) :` (a state register -- no
    more-specific-type; the count is a parenthetical attribute; the handle IS
    its own `fbK` net, §7), then `init =`/`each =` -- never a TODO."""
    if not _FEEDBACK_VI.exists():
        pytest.skip("lv-flex-channel-examples sample corpus not present")
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_FEEDBACK_VI),
        LoadMode.MINIMAL,
        search_paths=[_FEEDBACK_SEARCH_ROOT],
        layout=False,
    )
    vi_name = graph.resolve_vi_name(_FEEDBACK_VI.name)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module)
    counts = _assert_no_invented_open_syntax(text)
    assert counts["feedback-node"] > 0, "expected at least one feedback-node"

    lines = text.splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.strip().startswith("feedback-node "))
    assert lines[idx].strip() == "feedback-node fb0 (1 iteration) :"
    assert lines[idx + 1].strip() == "init = 0.0"
    assert lines[idx + 2].strip() == "each = High_Resolution_Relative_Seconds_1::0"


def test_in_place_element_and_formula_node_synthetic_block_invariant() -> None:
    """No real corpus VI exercising these two kinds was required by this
    pass -- so this is a SYNTHETIC structural check of
    ``_assert_no_invented_open_syntax``'s block-scanning logic itself (never
    a claim about a real render): a fabricated ``in-place-element``/
    ``formula-node`` block, handle+terminals then exactly one trailing TODO,
    passes; a block with a SECOND invented line after the TODO is rejected.
    """
    good = (
        "vi Fake.vi :\n"
        "\n"
        "  in-place-element IPE_1 : In Place Element\n"
        "    in   0 : DBL = x\n"
        "    out  1 : DBL\n"
        "    # TODO(lvnet): in-place-element decompose/recompose pairing was never designed (md §17 item 6)\n"
        "  formula-node Formula_1 : Formula Node\n"
        "    in   x : DBL = x\n"
        "    out  y : DBL\n"
        "    # TODO(lvnet): formula-node script rendering needs the `script` field plumbed\n"
        "\n"
    )
    counts = _assert_no_invented_open_syntax(good)
    assert counts["in-place-element"] == 1
    assert counts["formula-node"] == 1

    bad = good.replace(
        "    # TODO(lvnet): in-place-element decompose/recompose pairing was never designed (md §17 item 6)\n",
        "    # TODO(lvnet): in-place-element decompose/recompose pairing was never designed (md §17 item 6)\n"
        "    invented_extra_line = 1\n",
    )
    with pytest.raises(AssertionError):
        _assert_no_invented_open_syntax(bad)


# ============================================================
# Smoke: TestCase/run.vi, TestSuite/run.vi (CLOSED-core), TextTestRunner/run.vi
# (the "OPEN stress case" candidate) -- must render without crashing and must
# never emit invented OPEN inner syntax.
# ============================================================

_RUN_VIS = [
    _JKI_SOURCE_ROOT / "Classes" / "TestCase" / "run.vi",
    _JKI_SOURCE_ROOT / "Classes" / "TestSuite" / "run.vi",
    _JKI_SOURCE_ROOT / "Classes" / "TextTestRunner" / "run.vi",
]


@pytest.mark.needs_samples
@pytest.mark.parametrize("vi_path", _RUN_VIS, ids=lambda p: p.parent.name)
def test_render_lvnet_smoke_run_vis(vi_path: Path) -> None:
    """Renders without crashing; the OPEN invariant holds even if this
    particular VI happens to exercise zero OPEN constructs (in which case
    ``_assert_no_invented_open_syntax`` simply returns all-zero counts -- not
    itself asserted here, since these 3 run.vi's turned out (verified) to
    contain none: only subVI calls, primitives, constants, and a case
    structure each)."""
    if not vi_path.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(vi_path), LoadMode.MINIMAL, search_paths=[_JKI_SOURCE_ROOT], layout=False
    )
    vi_name = graph.resolve_vi_name(vi_path.name)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module)
    _assert_no_invented_open_syntax(text)
    assert text.startswith("vi ")
    # Never leak the OLD renderer's gamma/mu/eta vocabulary into lvnet text.
    for banned in (":= gamma(", ":= mu(", ":= eta(", "not("):
        assert banned not in text
