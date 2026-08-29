"""Tests for lvkit netlist -- graph -> text netlist projection.

Loads the JKI VI-Tester ``run.vi`` pair staged in the scratchpad (mirrors
``tests/test_diff.py``'s load-and-skip-if-absent convention: these VIs are
not part of the repo's sample corpus, so tests degrade gracefully when
they're not present in a given environment).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.netlist import (
    DefaultValue,
    EtaMerge,
    GammaMerge,
    MuMerge,
    NetlistFeedback,
    NetlistInstance,
    NetlistModule,
    NetlistScope,
    NetRef,
    build_netlist,
    index_module,
    netlist_to_dict,
    render_netlist,
)
from lvkit.load_mode import LoadMode

SCRATCHPAD = Path(
    "/tmp/claude-1000/-home-ryanf-repos-lvkit/3a7f874f-386b-432d-9712-edf3bc6c995e"
    "/scratchpad"
)
RUN_OLD = SCRATCHPAD / "run_OLD.vi"
RUN_NEW = SCRATCHPAD / "run_NEW.vi"
DEMO_SEARCH_PATH = Path(__file__).resolve().parent.parent / ".tmp" / "vi-tester-demo"


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[DEMO_SEARCH_PATH], layout=False)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _require_vis() -> None:
    if not RUN_OLD.exists() or not RUN_NEW.exists():
        pytest.skip("JKI run_OLD.vi/run_NEW.vi pair not staged in scratchpad")


# A corpus VI whose 5 front-panel indicators are each driven by a distinct
# producer — the regression for "boundary outputs carry no source net".
_COVERAGE_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/Calculate Test Coverage.vi"
)

# A corpus VI whose second Compound Arithmetic node has a real "Not" bubble
# on one of its INPUT terminals (`Terminal.inverted`, DCO objFlags bit 16) --
# `Compound Arithmetic#2` ANDs `Less?.result` with the NEGATION of
# `Equal?#2.equal`. Ground truth located by grepping the JKI-VI-Tester
# corpus's extracted `*_BDHb.xml` for `cpdArith` and parsing each candidate.
_INVERTED_INPUT_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Menu Launch/VI Tester Menu Launch.vi"
)

# The audit's Property Node black-box finding: this VI's Property Nodes
# cover a write-only node (uid 21, "Tree (strict)": "Active Item Tag" and
# "Open?", both written), a read-only node whose VALUE terminal is itself
# Refnum-typed (uid 1065, "Library": "Project" -- a "Library:Project"
# property returns a Project reference, so a type-based Refnum filter would
# wrongly treat that terminal as the object reference and leave it numeric),
# and a mixed read node (uid 21 in the Tree Cell Selection frame, "Tree
# (strict)": "ActiveColNum" write / "Cell String" read twice). Ground truth
# located by grepping the JKI-VI-Tester corpus's extracted ``*_BDHb.xml`` for
# ``propNode`` and parsing each candidate (see the module's real VI
# probe -- ``.tmp/probe_list.py`` during development).
_PROPERTY_NODE_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner - Main UI - .vi"
)

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")

# The loop analogue of the case gamma-merge fix (finding #1): a for-loop
# shift-registers the error cluster across TestCase_Init.vi calls (an
# INITIALIZED lSR, init'd from listAllTestMethods.vi's error out) and
# auto-indexes the built TestCase objects into an array via an lpTun output
# tunnel, consumed immediately after the loop by TestSuite_Init.vi. Before
# this fix, the shift-register reader inside the loop hopped straight
# through to the INIT wire (losing the recurrence) and the auto-indexed
# consumer outside the loop resolved to a bare unrelated constant ("tests
# (none)") instead of the built array. Ground truth located by grepping the
# JKI-VI-Tester corpus's extracted *_BDHb.xml for lSR+lpTun and parsing each
# candidate (see .tmp/probe_loop_candidates.py during development).
_SHIFT_REGISTER_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestLoader/"
    "loadTestsFromTestCase.vi"
)

# Real corpus VI whose case-scoped for-loops each carry a LAST_VALUE output
# tunnel, consumed immediately after the loop (Array Size / Sort Array__
# ogtk.vi / Conditional Auto-Indexing Tunnel__ogtk.vi). Ground truth located
# the same way as _SHIFT_REGISTER_VI.
# A VI with a genuine CONDITIONAL output tunnel (the orthogonal modifier).
_CONDITIONAL_LOOP_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/"
    "Prepend Test Project Path If New.vi"
)

# Real corpus VI with a genuinely UNINITIALIZED shift register (an array
# accumulator whose left terminal has no external wire) -- the
# "init -> [] ([String] default)" DefaultValue case. Ground truth located
# the same way as _SHIFT_REGISTER_VI (see .tmp/probe_uninitialized_sr.py).
_UNINITIALIZED_SR_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/"
    "Initialize Tests On Tree.vi"
)


# Real corpus VI with a split Feedback Node inside a while loop: it writes
# High Resolution Relative Seconds.vi's timestamp every iteration and reads
# the PREVIOUS iteration's value back (a z^-1 delay), so a downstream Subtract
# computes the per-iteration elapsed time (dt = now - prev). The master
# (hiddenFBNode) owns the read/output + initializer terminals; the slave
# (slaveFBInputNode) owns the written-value input; the master/slave link and
# feedbackNodeDelay are both explicit in the block-diagram heap. Ground truth
# located by grepping the extracted ``*_BDHb.xml`` for the feedback DCO
# classes (leftFeedback/rightFeedback/initFeedback + SlaveFBInputNode).
_FEEDBACK_VI = Path(".lvkit/cache/samples/lv-flex-channel-examples/WaveGen/WaveGen.vi")
_FEEDBACK_SEARCH_ROOT = Path(".lvkit/cache/samples/lv-flex-channel-examples/WaveGen")


def _load_vi(vi_path: Path, search_root: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), LoadMode.MINIMAL, search_paths=[search_root])
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _all_scopes(items: list) -> list[NetlistScope]:
    """Every NetlistScope in a body, recursively (through every frame)."""
    scopes: list[NetlistScope] = []
    for item in items:
        if isinstance(item, NetlistScope):
            scopes.append(item)
            for frame in item.frames:
                scopes.extend(_all_scopes(frame.body))
    return scopes


def _find_loop_scope(module: NetlistModule) -> NetlistScope | None:
    for scope in _all_scopes(module.body):
        if scope.kind in ("for", "while"):
            return scope
    return None


def _find_instance(scope: NetlistScope, name: str) -> NetlistInstance | None:
    for item in scope.frames[0].body:
        if isinstance(item, NetlistInstance) and item.name == name:
            return item
    return None


def _all_feedbacks(items: list) -> list[NetlistFeedback]:
    """Every NetlistFeedback in a body, recursively (through every frame)."""
    out: list[NetlistFeedback] = []
    for item in items:
        if isinstance(item, NetlistFeedback):
            out.append(item)
        elif isinstance(item, NetlistScope):
            for frame in item.frames:
                out.extend(_all_feedbacks(frame.body))
    return out


def test_feedback_node_renders_as_mu_and_class_names_do_not_leak() -> None:
    """A Feedback Node is a standalone Gated-SSA mu (a z^-N state element).
    The parser previously had no handler for its master/slave XML classes, so
    the raw ``hiddenFBNode``/``slaveFBInputNode`` class names leaked into the
    netlist and Components. Now the master projects as one ``fb{k}`` mu net
    (init + recur), the write side is dissolved, and no raw class name
    survives anywhere."""
    if not _FEEDBACK_VI.exists():
        pytest.skip("lv-flex-channel-examples sample corpus not present")
    graph, vi_name = _load_vi(_FEEDBACK_VI, _FEEDBACK_SEARCH_ROOT)
    module = build_netlist(graph, vi_name)

    feedbacks = _all_feedbacks(module.body)
    assert feedbacks, "expected a Feedback Node projected as a mu item"
    fb = feedbacks[0]
    assert fb.net == "fb0"
    assert fb.delay == 1  # feedbackNodeDelay "01" -> z^-1
    # Initializer terminal is unwired -> LabVIEW's DBL type default, never faked.
    assert isinstance(fb.init, DefaultValue)
    # Written every iteration -> a real recurrence (the timestamp source).
    assert isinstance(fb.recur, NetRef)
    assert fb.recur.bare == "High Resolution Relative Seconds.vi.0"

    out = render_netlist(module)
    # The raw XML class names must be GONE from the whole projection.
    assert "hiddenFBNode" not in out
    assert "slaveFBInputNode" not in out
    fb_lines = [ln for ln in out.splitlines() if ln.strip().startswith("fb0 :=")]
    assert fb_lines, "expected an fb0 mu-definition line"
    line = fb_lines[0].strip()
    assert "mu[z^-1]" in line
    assert "init ->" in line and "recur ->" in line
    assert "<-" not in line  # locked ASCII, arrows are -> only

    # Components must not declare the feedback halves either.
    comp_names = {c.name for c in module.components}
    assert "hiddenFBNode" not in comp_names
    assert "slaveFBInputNode" not in comp_names


def test_feedback_output_consumer_resolves_to_named_fb_net() -> None:
    """The downstream consumer reading the Feedback Node's output resolves to
    the named ``fb0`` net -- never the raw ``hiddenFBNode.0`` producer (the
    standalone-node analogue of a shift-register reader resolving to
    ``loop{id}.shift{k}``)."""
    if not _FEEDBACK_VI.exists():
        pytest.skip("lv-flex-channel-examples sample corpus not present")
    graph, vi_name = _load_vi(_FEEDBACK_VI, _FEEDBACK_SEARCH_ROOT)
    module = build_netlist(graph, vi_name)

    loop_scope = _find_loop_scope(module)
    assert loop_scope is not None
    subtract = _find_instance(loop_scope, "Subtract")
    assert subtract is not None
    fb_bindings = [
        b for b in subtract.inputs if b.net is not None and b.net.bare == "fb0"
    ]
    assert fb_bindings, (
        "Subtract should read the feedback output as fb0, got "
        f"{[b.net.bare for b in subtract.inputs if b.net is not None]}"
    )
    fb_net = fb_bindings[0].net
    assert fb_net is not None
    # A merge net has no producing node (like a boundary/const), renders bare.
    assert fb_net.node is None


def test_shift_register_renders_mu_and_inner_reader_resolves_to_shift_net() -> None:
    """Loop analogue of the case gamma-merge fix: a shift register is a
    genuine Gated-SSA mu recurrence, not a single hop-through to its init
    value. Before this fix, a reader inside the loop resolved straight
    through ``_paired_tunnel_id`` to the OUTER init wire, silently dropping
    the per-iteration recurrence written back by the previous call."""
    if not _SHIFT_REGISTER_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_vi(_SHIFT_REGISTER_VI, _JKI_SOURCE_ROOT)
    module = build_netlist(graph, vi_name)

    loop_scope = _find_loop_scope(module)
    assert loop_scope is not None
    mu_merges = [m for m in loop_scope.outputs if isinstance(m, MuMerge)]
    assert mu_merges, "expected at least one MuMerge on the for-loop scope"
    mu = mu_merges[0]
    assert mu.net.startswith("loop")
    assert ".shift" in mu.net
    assert isinstance(mu.init, (NetRef, DefaultValue))
    # This SR is genuinely written to every iteration -- a real recurrence.
    assert isinstance(mu.recur, NetRef)

    # A node inside the loop reading the LEFT SR terminal resolves to the
    # SAME short mu net -- never hops through to the init value.
    test_case_init = _find_instance(loop_scope, "TestCase_Init.vi")
    assert test_case_init is not None
    error_in_binding = next(
        b for b in test_case_init.inputs if b.terminal.startswith("error in")
    )
    error_in_net = error_in_binding.net
    assert error_in_net is not None
    assert error_in_net.node is None
    assert error_in_net.bare == mu.net

    out = render_netlist(module)
    mu_lines = [ln for ln in out.splitlines() if ":= mu(" in ln]
    assert mu_lines, "expected at least one mu-merge definition line"
    for line in mu_lines:
        assert "<-" not in line
        stripped = line.strip()
        assert stripped.startswith("shift")
        assert "init ->" in stripped


def test_auto_indexed_output_renders_eta_array_and_outside_consumer_resolves() -> None:
    """The loop analogue of the Build-Array gamma regression: an
    auto-indexed loop output tunnel is a genuine array merge across every
    iteration, not the inner per-iteration scalar producer. Before this
    fix, TestSuite_Init.vi's ``tests`` input (wired to this tunnel from
    OUTSIDE the loop) resolved to an unrelated bare constant instead of the
    built TestCase array."""
    if not _SHIFT_REGISTER_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_vi(_SHIFT_REGISTER_VI, _JKI_SOURCE_ROOT)
    module = build_netlist(graph, vi_name)

    loop_scope = _find_loop_scope(module)
    assert loop_scope is not None
    eta_merges = [m for m in loop_scope.outputs if isinstance(m, EtaMerge)]
    assert eta_merges, "expected at least one EtaMerge on the for-loop scope"
    eta = eta_merges[0]
    assert eta.net.startswith("loop")
    assert ".out" in eta.net
    assert eta.index_mode == "array"

    case_scope = next(
        i for i in module.body if isinstance(i, NetlistScope) and i.kind == "case"
    )
    no_error_frame = next(f for f in case_scope.frames if not f.is_default)
    suite_init = next(
        item
        for item in no_error_frame.body
        if isinstance(item, NetlistInstance) and item.name == "TestSuite_Init.vi"
    )
    tests_binding = next(b for b in suite_init.inputs if b.terminal.startswith("tests"))
    tests_net = tests_binding.net
    assert tests_net is not None
    assert tests_net.node is None
    assert tests_net.bare == eta.net

    out = render_netlist(module)
    eta_lines = [ln for ln in out.splitlines() if ":= eta(" in ln]
    assert any("array" in ln for ln in eta_lines)
    for line in eta_lines:
        assert "<-" not in line
        stripped = line.strip()
        assert stripped.startswith("out")


def test_eta_line_renders_base_mode_and_conditional_modifier() -> None:
    """The eta line renders the BASE mode token (``array``/``last``/...) and
    appends ``+cond`` for the orthogonal Conditional modifier -- never a
    separate ``conditional`` mode. Hermetic: no VI in the sample corpus has a
    genuine last-value OUTPUT tunnel (the old ``eta(last)`` cases were
    mislabeled indexing -- inner element vs outer array), so exercise the
    render directly."""
    from lvkit.graph.netlist import _eta_definition_line

    val = DefaultValue(literal="0", type_descriptor="I32")
    last = _eta_definition_line(
        EtaMerge(net="loop0.out0", index_mode="last", conditional=False, value=val),
        set(),
    )
    assert "eta(last," in last
    cond_index = _eta_definition_line(
        EtaMerge(net="loop0.out0", index_mode="array", conditional=True, value=val),
        set(),
    )
    assert "eta(array+cond," in cond_index
    cond_last = _eta_definition_line(
        EtaMerge(net="loop0.out0", index_mode="last", conditional=True, value=val),
        set(),
    )
    assert "eta(last+cond," in cond_last


def test_conditional_output_tunnel_carries_conditional_modifier() -> None:
    """A real VI with a Conditional output tunnel yields an EtaMerge whose
    base ``index_mode`` is unchanged and ``conditional`` is True (the modifier
    is orthogonal, never a mode)."""
    if not _CONDITIONAL_LOOP_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_vi(_CONDITIONAL_LOOP_VI, _JKI_SOURCE_ROOT)
    module = build_netlist(graph, vi_name)

    cond_etas = [
        m
        for scope in _all_scopes(module.body)
        for m in scope.outputs
        if isinstance(m, EtaMerge) and m.conditional
    ]
    assert cond_etas, "expected at least one conditional EtaMerge"
    assert all(e.index_mode in ("array", "last", "concat") for e in cond_etas)
    out = render_netlist(module)
    eta_lines = [ln for ln in out.splitlines() if ":= eta(" in ln]
    assert any("+cond," in ln for ln in eta_lines)
    for line in eta_lines:
        assert "<-" not in line
        assert line.strip().startswith("out")


def test_uninitialized_shift_register_init_is_type_default() -> None:
    """An uninitialized shift register's ``init`` is an explicit type
    ``DefaultValue`` (LabVIEW seeds it on the VI's first call) -- never
    ``None``, never silently resolved to something else."""
    if not _UNINITIALIZED_SR_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_vi(_UNINITIALIZED_SR_VI, _JKI_SOURCE_ROOT)
    module = build_netlist(graph, vi_name)

    default_inits = [
        m.init
        for scope in _all_scopes(module.body)
        for m in scope.outputs
        if isinstance(m, MuMerge) and isinstance(m.init, DefaultValue)
    ]
    assert default_inits, "expected at least one uninitialized-SR type default"
    assert any(dv.literal == "[]" for dv in default_inits)


def test_boundary_outputs_carry_their_source_net() -> None:
    """Every wired indicator resolves to the net that drives it (get_context
    used to declare output name/type but drop the producer→indicator wire)."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_COVERAGE_VI),
        LoadMode.MINIMAL,
        search_paths=[_COVERAGE_VI.parents[3]],
    )
    vi_name = graph.resolve_vi_name(_COVERAGE_VI.name)
    module = build_netlist(graph, vi_name)

    assert len(module.outputs) == 5
    # All five indicators are wired in this VI — none may be a dropped source.
    assert all(o.source is not None for o in module.outputs)
    # The source shows up inline in the rendered signature (arrow-free binding).
    header = render_netlist(module).splitlines()[0]
    assert "% Coverage=" in header
    assert "<-" not in header


def _load_coverage_vi() -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_COVERAGE_VI),
        LoadMode.MINIMAL,
        search_paths=[_COVERAGE_VI.parents[3]],
    )
    vi_name = graph.resolve_vi_name(_COVERAGE_VI.name)
    return graph, vi_name


def _case_scopes(module) -> list[NetlistScope]:
    return [
        item
        for item in module.body
        if isinstance(item, NetlistScope) and item.kind == "case"
    ]


def test_case_output_gamma_merges_feed_build_array() -> None:
    """Finding #1 regression: each of the VI's 3 case structures counts a
    filtered-VI-count subtract; ``Build Array`` collects all three. Before
    the gamma-merge fix, ``_resolve_source`` hopped through the case's
    output tunnel into whichever frame ``_paired_tunnel_id`` found first
    (the unwired Default frame) and Build Array rendered with NO inputs at
    all. It must now show all 3 inputs, each resolved to a named
    ``case{N}.out{k}`` gamma-merge net -- never empty, never a bare
    ``Subtract`` instance name."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_coverage_vi()
    module = build_netlist(graph, vi_name)

    build_array = next(
        item
        for item in module.body
        if isinstance(item, NetlistInstance) and item.name == "Build Array"
    )
    assert len(build_array.inputs) == 3
    for binding in build_array.inputs:
        net = binding.net
        assert net is not None
        # A gamma net is a boundary-shaped NetRef (node=None, like a literal
        # or a VI control) whose bare name is the case's own merge net.
        assert net.node is None
        assert net.bare.startswith("case")
        assert ".out" in net.bare

    out = render_netlist(module)
    assert "Build Array()" not in out
    for line in out.splitlines():
        if line.strip().startswith("Build Array("):
            assert "case" in line
            assert ".out" in line
            break
    else:
        pytest.fail("no 'Build Array(' line in rendered netlist")


def test_case_scopes_carry_gamma_merge_with_selector_and_type_default() -> None:
    """Every case scope in the VI declares a ``GammaMerge`` per output
    tunnel, carrying the real selector net and one source per frame -- the
    unwired ``Default`` frame's source must be an explicit ``DefaultValue``
    (LabVIEW's "use default if unwired"), never omitted and never silently
    resolved to another frame's producer."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_coverage_vi()
    module = build_netlist(graph, vi_name)

    case_scopes = _case_scopes(module)
    assert len(case_scopes) == 3
    for scope in case_scopes:
        assert scope.outputs, f"case scope {scope.uid} has no gamma merges"
        for gamma in scope.outputs:
            assert isinstance(gamma, GammaMerge)
            assert gamma.net.startswith("case")
            assert ".out" in gamma.net
            assert gamma.selector is not None  # "Ignore ..." is always wired
            frame_keys = {c.frame_key for c in gamma.cases}
            assert "default" in frame_keys
            assert len(gamma.cases) == len(scope.frames)
            default_case = next(c for c in gamma.cases if c.frame_key == "default")
            # At least one of this case's output tunnels leaves the Default
            # frame genuinely unwired (a type default), the other is fed by
            # the Default-frame pass-through of the filtered array -- assert
            # the SHAPE (NetRef or DefaultValue), not which specific tunnel.
            assert isinstance(default_case.source, (NetRef, DefaultValue))

    # At least one gamma across the VI must show the real "use default if
    # unwired" type-default substitution, faithfully labeled.
    default_values = [
        c.source
        for scope in case_scopes
        for gamma in scope.outputs
        if isinstance(gamma, GammaMerge)  # a case scope's outputs are all gammas
        for c in gamma.cases
        if isinstance(c.source, DefaultValue)
    ]
    assert default_values, "expected at least one unwired-tunnel type default"
    assert any(dv.render() == "0 (I32 default)" for dv in default_values)


def test_gamma_definition_line_uses_short_net_name_and_arrow_only() -> None:
    """The rendered ``out{k} := gamma(...)`` line uses the SHORT local net
    name (not the fully qualified ``case{N}.out{k}``) and the locked ``->``
    arrow only -- never ``<-``."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_coverage_vi()
    out = render_netlist(build_netlist(graph, vi_name))

    gamma_lines = [ln for ln in out.splitlines() if ":= gamma(" in ln]
    assert gamma_lines, "expected at least one gamma-merge definition line"
    for line in gamma_lines:
        assert "<-" not in line
        stripped = line.strip()
        assert stripped.startswith("out")
        assert not stripped.startswith("case")


class TestBuildNetlist:
    """Execute build_netlist + render_netlist against the real run_NEW.vi
    and assert on real substrings/invariants -- never syntax-only, and
    never a hand-written full-text golden (too brittle)."""

    def test_contains_known_real_elements(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))

        assert "CallTestMethod" in out
        assert "addSkipped" in out
        assert "case (" in out

    def test_signature_line_has_arrow(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))

        header = out.splitlines()[0]
        assert "->" in header
        assert header.startswith(vi_name.split(":")[-1].rsplit(".vi", 1)[0]) or (
            vi_name in header
        )

    def test_output_is_ascii(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))
        assert out.isascii()

    def test_no_unicode_arrows_or_boxes(self):
        """The only arrow is '->' -- no '<-', no unicode box-drawing/arrows
        (the syntax is locked to plain ASCII)."""
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))
        assert "<-" not in out
        assert "→" not in out  # "->" unicode arrow
        assert "─" not in out  # box-drawing

    def test_every_case_scope_has_at_least_one_frame_line(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))
        lines = out.splitlines()

        found_case = False
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if not (stripped.startswith("case (") or stripped == "case:"):
                continue
            found_case = True
            indent = len(ln) - len(ln.lstrip(" "))
            frame_lines = 0
            for nxt in lines[i + 1 :]:
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt.strip() and nxt_indent <= indent:
                    break
                if nxt.strip().startswith('"'):
                    frame_lines += 1
            assert frame_lines >= 1, f"case scope at line {i} has no frame lines"
        assert found_case, "expected at least one 'case (' scope in run_NEW.vi"

    def test_build_and_render_repeatable_same_process(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out1 = render_netlist(build_netlist(graph, vi_name))
        out2 = render_netlist(build_netlist(graph, vi_name))
        assert out1 == out2

    def test_run_old_also_builds(self):
        """Sanity check the other half of the pair builds cleanly too."""
        _require_vis()
        graph, vi_name = _load(RUN_OLD)
        out = render_netlist(build_netlist(graph, vi_name))
        assert out.isascii()
        assert "->" in out.splitlines()[0]


def test_netlist_deterministic_across_hash_seeds():
    """Node UIDs live in hash-randomized sets (_vi_nodes), so occurrence
    numbering and node order must be checked across separate interpreters,
    not just re-running in-process (see test_render.py's identical pattern
    for render_vi_file)."""
    _require_vis()

    script = (
        "import hashlib\n"
        "from pathlib import Path\n"
        "from lvkit.graph.core import InMemoryVIGraph\n"
        "from lvkit.graph.netlist import build_netlist, render_netlist\n"
        f"graph = InMemoryVIGraph()\n"
        f"graph.load_vi({str(RUN_NEW)!r}, "
        f"search_paths=[Path({str(DEMO_SEARCH_PATH)!r})], layout=False)\n"
        f"vi_name = graph.resolve_vi_name({RUN_NEW.name!r})\n"
        "out = render_netlist(build_netlist(graph, vi_name))\n"
        "assert out.isascii()\n"
        "print(hashlib.sha256(out.encode()).hexdigest())\n"
    )

    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert digests[0] == digests[1]


# --------------------------------------------------------------------------- #
# nMux (Bundle/Unbundle By Name) field-name resolution -- the canonical
# ``op_walk.stamp_nmux_lane_names`` seam that also backs render/diff/describe.
# --------------------------------------------------------------------------- #

_TESTRESULT_DIR = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestResult",
)
_TESTRESULT_VI = _TESTRESULT_DIR / "GetTestsRun.vi"


def test_nmux_class_private_data_resolves_under_minimal_load_no_search_path():
    """``GetTestsRun.vi`` unbundles ``TestResult.lvclass``'s own private data
    (a genuine ``nMux`` node, not the IPES cluster-border kind) via its
    single ``testsRun`` field. Loaded MINIMAL with NO search path at all --
    an isolated copy in its own temp dir, so ``TestResult.lvclass`` and its
    sibling method VIs are entirely unreachable -- the netlist must still
    show the REAL field name ``testsRun``, resolved from the VI's own
    embedded "Cluster of class private data" snapshot
    (``op_walk._own_class_private_data_fields``/``stamp_nmux_lane_names``),
    not a bare index or the node's own generic port fallback. Before this
    seam existed, netlist/diff had no own-embedded fallback at all (only
    render did) -- this reproduces exactly the scenario that used to leave
    the field unresolved (``Bundle/Unbundle By Name.1``)."""
    if not _TESTRESULT_VI.exists():
        pytest.skip("JKI-VI-Tester sample not available")

    with tempfile.TemporaryDirectory() as tmp:
        isolated_vi = Path(tmp) / _TESTRESULT_VI.name
        shutil.copy(_TESTRESULT_VI, isolated_vi)

        graph = InMemoryVIGraph()
        graph.load_vi(str(isolated_vi), mode=LoadMode.MINIMAL)
        vi_name = graph.resolve_vi_name(isolated_vi.name)
        assert not graph.get_class_fields("TestResult.lvclass"), (
            "this test's premise is that the owning class is NOT reachable"
        )

        out = render_netlist(build_netlist(graph, vi_name))
        assert "testsRun" in out
        assert "Bundle/Unbundle By Name.1" not in out


def _cpdarith_instances(module) -> list[NetlistInstance]:
    instances, _scopes = index_module(module)
    return [i for i in instances.values() if i.name == "Compound Arithmetic"]


def test_compound_arithmetic_renders_its_operator() -> None:
    """Audit finding: a cpdArith node's operator (add/multiply/and/or/xor,
    already parsed onto ``PrimitiveOperation.operation``) used to be dropped
    by the netlist projection -- every Compound Arithmetic instance
    rendered identically regardless of whether it was an AND, OR, or XOR.
    This VI's two Compound Arithmetic nodes both AND two Not-Equal? results
    -- the rendered line must now say so via a bracketed suffix, and the
    IR field driving it must carry the raw operation string."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_coverage_vi()
    module = build_netlist(graph, vi_name)

    instances = _cpdarith_instances(module)
    assert len(instances) == 2
    for inst in instances:
        assert inst.operation == "and"

    out = render_netlist(module)
    lines = [
        line
        for line in out.splitlines()
        if "Compound Arithmetic#" in line and "(1=" in line
    ]
    assert len(lines) == 2
    for line in lines:
        assert "[and]" in line
    assert (
        "Compound Arithmetic#1 [and]"
        "(1=Not Equal?#1.result, 2=Not Equal?#2.result) -> "
        "Compound Arithmetic#1.0" in out
    )


def test_compound_arithmetic_operator_does_not_perturb_net_names() -> None:
    """The operator is a display annotation only -- it must NOT change the
    net names a Compound Arithmetic instance produces or is referenced by
    (the ``NetRef``/occurrence identity), since those ripple into every
    downstream wire reference and the ambiguous-bare disambiguation."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_coverage_vi()
    module = build_netlist(graph, vi_name)

    instances = _cpdarith_instances(module)
    assert {i.uid for i in instances} != set()
    for inst in instances:
        assert inst.name == "Compound Arithmetic"  # unsuffixed by operation
        for out in inst.outputs:
            assert out.net.node == "Compound Arithmetic"
            assert out.net.bare == f"Compound Arithmetic#{inst.occurrence}.0"

    out = render_netlist(module)
    # The producing-net names referenced downstream are unchanged -- no
    # operator text leaks into a net name.
    assert "Compound Arithmetic#1.0" in out
    assert "Compound Arithmetic#2.0" in out
    assert "[and].0" not in out
    assert "and.0" not in out


def test_non_cpdarith_instance_has_no_operation_suffix() -> None:
    """Regression: an ordinary instance (no ``operation`` on its op) must
    render and serialize exactly as before -- no suffix, ``operation`` is
    ``None``."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_coverage_vi()
    module = build_netlist(graph, vi_name)

    instances, _scopes = index_module(module)
    non_cpdarith = [i for i in instances.values() if i.name != "Compound Arithmetic"]
    assert non_cpdarith
    for inst in non_cpdarith:
        assert inst.operation is None

    out = render_netlist(module)
    assert "Subtract#1(y=Array Size#2.size, x=Array Size#1.size) -> difference" in out
    assert "Subtract#1 [" not in out


def _load_inverted_input_vi() -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_INVERTED_INPUT_VI),
        LoadMode.MINIMAL,
        search_paths=[_INVERTED_INPUT_VI.parents[1]],
    )
    vi_name = graph.resolve_vi_name(_INVERTED_INPUT_VI.name)
    return graph, vi_name


def test_compound_arithmetic_inverted_input_renders_negation_prefix() -> None:
    """Audit finding: an INPUT terminal's "Not" bubble (``Terminal.inverted``,
    already parsed from DCO objFlags bit 16 -- see ``construction.py``) used
    to be dropped entirely by the netlist projection, so ``x AND NOT y``
    rendered identically to ``x AND y``. This VI's second Compound
    Arithmetic node ANDs ``Less?.result`` with the NEGATION of
    ``Equal?#2.equal`` -- the rendered line must now wrap that one input in
    ``not(...)``, and only that one. (``not(net)``, not a bare ``NOT ``/``!``
    prefix, so it reads clearly and can't be mistaken for the primitive
    named "Not Equal?".)"""
    if not _INVERTED_INPUT_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_inverted_input_vi()
    module = build_netlist(graph, vi_name)

    instances = _cpdarith_instances(module)
    assert {i.occurrence for i in instances} == {1, 2}
    inst2 = next(i for i in instances if i.occurrence == 2)

    bindings_by_port = {b.terminal: b for b in inst2.inputs}
    assert bindings_by_port["1"].inverted is False
    assert bindings_by_port["2"].inverted is True
    # Faithful: the negation is an annotation on the BINDING, never on the
    # net's own identity.
    net2 = bindings_by_port["2"].net
    assert net2 is not None
    assert net2.bare == "equal"

    out = render_netlist(module)
    assert (
        "Compound Arithmetic#2 [and]"
        "(1=result, 2=not(Equal?#2.equal)) -> Compound Arithmetic#2.0" in out
    )
    # The sibling (non-inverted) Compound Arithmetic node's inputs are
    # unaffected -- no negation wrapper appears anywhere on its line.
    line1 = next(line for line in out.splitlines() if "Compound Arithmetic#1 [" in line)
    assert "not(" not in line1


def test_compound_arithmetic_non_inverted_inputs_unchanged() -> None:
    """Non-inverted inputs on the SAME node as an inverted one render exactly
    as they did before ``inverted`` existed -- no stray ``NOT `` prefix, and
    the net names/occurrence tags are stable (identical to what
    ``test_compound_arithmetic_operator_does_not_perturb_net_names`` already
    asserts for the operator suffix)."""
    if not _INVERTED_INPUT_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_inverted_input_vi()
    module = build_netlist(graph, vi_name)

    instances = _cpdarith_instances(module)
    for inst in instances:
        for b in inst.inputs:
            if b.terminal != "2" or inst.occurrence != 2:
                assert b.inverted is False


def _load_property_node_vi() -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_PROPERTY_NODE_VI),
        LoadMode.MINIMAL,
        search_paths=[_PROPERTY_NODE_VI.parents[3]],
    )
    vi_name = graph.resolve_vi_name(_PROPERTY_NODE_VI.name)
    return graph, vi_name


def _property_node_instances(module) -> list[NetlistInstance]:
    instances, _scopes = index_module(module)
    return [i for i in instances.values() if i.name == "Property Node"]


def test_property_node_write_properties_render_named_bindings_and_object_class() -> (
    None
):
    """Audit finding: a Property Node is a black box in the netlist -- which
    properties it accesses, and whether each is read or written, was
    completely lost (every value port rendered as a bare numeric index, e.g.
    ``Property Node#1(0=..., 4=..., 5=True)``). uid 21 writes two properties
    ("Active Item Tag", "Open?") on a "Tree (strict)" reference -- the
    rendered line must show the object CLASS as a bracket suffix and each
    WRITTEN property as a named ``port=net`` input binding, never a numeric
    index."""
    if not _PROPERTY_NODE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_property_node_vi()
    module = build_netlist(graph, vi_name)

    inst = next(i for i in _property_node_instances(module) if i.uid == "21")
    assert inst.object_name == "Tree (strict)"
    assert [(p.name, p.direction) for p in inst.properties] == [
        ("Active Item Tag", "write"),
        ("Open?", "write"),
    ]
    port_names = {b.terminal for b in inst.inputs}
    assert {"Active Item Tag", "Open?"}.issubset(port_names)
    assert "4" not in port_names and "5" not in port_names

    out = render_netlist(module)
    assert (
        "Property Node#1 [Tree (strict)]"
        "(0=Invoke Node#1.1, Active Item Tag=Invoke Node#1.11, Open?=True) -> "
        "Property Node#1.1, Property Node#1.3" in out
    )


def test_property_node_read_property_correlates_refnum_valued_terminal() -> None:
    """Audit finding + faithfulness regression: a property's VALUE can itself
    be Refnum-typed (uid 1065's "Library:Project" property returns a Project
    reference) -- a type-based Refnum/error-cluster filter would wrongly
    exclude that terminal (mistaking it for the object reference passthrough)
    and leave it numeric. The STRUCTURAL dcoList correlation
    (``op_walk.correlate_property_terminals`` via
    ``PropertyOperation.value_terminal_ids``) must still name it "Project",
    while the object reference (port "0") and error terminals (ports "2"/
    "3") stay numeric."""
    if not _PROPERTY_NODE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_property_node_vi()
    module = build_netlist(graph, vi_name)

    inst = next(i for i in _property_node_instances(module) if i.uid == "1065")
    assert inst.object_name == "Library"
    assert len(inst.properties) == 1
    prop = inst.properties[0]
    assert prop.name == "Project"
    assert prop.direction == "read"
    assert prop.net is not None
    assert prop.net.bare == "Project"

    out = render_netlist(module)
    line = next(line for line in out.splitlines() if "Property Node#7 [" in line)
    assert line.endswith("Project")
    assert ".4" not in line


def test_property_node_non_value_terminals_keep_numeric_ports() -> None:
    """Terminals that are NOT property values -- the object reference IN,
    error IN, and error OUT -- must keep their existing numeric-port
    treatment; only the correlated property VALUE terminal gets a real
    name. Same uid 1065 instance as the Refnum-correlation test."""
    if not _PROPERTY_NODE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_property_node_vi()
    module = build_netlist(graph, vi_name)

    inst = next(i for i in _property_node_instances(module) if i.uid == "1065")
    input_ports = {b.terminal for b in inst.inputs}
    assert input_ports == {"0", "2"}  # object ref in, error in -- unlabeled
    output_ports = {o.net.terminal for o in inst.outputs}
    # ref-out (1) and error-out (3) stay numeric; only the correlated
    # property value terminal (originally "4") is named.
    assert output_ports == {"1", "3", "Project"}


def _invoke_node_instances(module) -> list[NetlistInstance]:
    instances, _scopes = index_module(module)
    return [i for i in instances.values() if i.name == "Invoke Node"]


def test_invoke_node_renders_method_and_object_and_keeps_numeric_params() -> None:
    """Audit finding: an Invoke Node is a black box in the netlist -- WHICH
    method it calls (the entire meaning of the node) was completely dropped,
    rendering as bare numeric ports (``Invoke Node#1(0=..., 6=...)``). uid
    6753 invokes "Point To Row Column" on a "Tree (strict)" reference -- the
    rendered line must show ``object:method`` as a bracket suffix, the SAME
    slot the Property Node work already uses for ``object_name`` alone.
    Method PARAMETER names are never available in the VI file (they live in
    the method's VI-server signature) -- ports 0/6 and the output occurrence
    ports must stay exactly as numeric as before this fix; only the node's
    OWN identity gains the method it calls, and net names (the ``->``
    outputs, the input nets) are untouched."""
    if not _PROPERTY_NODE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_property_node_vi()
    module = build_netlist(graph, vi_name)

    inst = next(i for i in _invoke_node_instances(module) if i.uid == "6753")
    assert inst.object_name == "Tree (strict)"
    assert inst.method_name == "Point To Row Column"
    input_ports = {b.terminal for b in inst.inputs}
    assert input_ports == {"0", "6"}  # params stay numeric -- names unrecoverable
    output_ports = {o.net.terminal for o in inst.outputs}
    assert output_ports == {"1", "3", "5", "7", "9", "11", "13", "15"}

    out = render_netlist(module)
    assert (
        "Invoke Node#1 [Tree (strict):Point To Row Column]"
        "(0=Event Data Node#12.3, 6=Event Data Node#12.4) -> "
        "Invoke Node#1.1, Invoke Node#1.3, Invoke Node#1.5, Invoke Node#1.7, "
        "Invoke Node#1.9, Invoke Node#1.11, Invoke Node#1.13, Invoke Node#1.15" in out
    )


def test_invoke_node_second_object_class_also_renders() -> None:
    """A second real invoke -- uid 915 ("Invoke Node#4") calls
    "Library.Open" on an "App" reference -- covers a distinct object CLASS
    and method in the same VI, confirming the bracket isn't hardcoded to one
    node's data."""
    if not _PROPERTY_NODE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_property_node_vi()
    module = build_netlist(graph, vi_name)

    inst = next(i for i in _invoke_node_instances(module) if i.uid == "915")
    assert inst.object_name == "App"
    assert inst.method_name == "Library.Open"

    out = render_netlist(module)
    line = next(line for line in out.splitlines() if "Invoke Node#4 [" in line)
    assert "Invoke Node#4 [App:Library.Open](" in line


def test_invoke_node_without_object_name_shows_method_only() -> None:
    """Faithfulness: when ``object_name`` genuinely isn't resolvable, the
    bracket must still show the method alone (``[method]``, not
    ``[None:method]`` or a dropped bracket) -- never fabricate an object
    class that wasn't in the file."""
    inst = NetlistInstance(
        uid="1",
        name="Invoke Node",
        occurrence=None,
        inputs=[],
        outputs=[],
        method_name="Some Method",
    )
    module = NetlistModule(vi_name="p.vi", inputs=[], outputs=[], body=[inst])

    out = render_netlist(module)
    assert "Invoke Node [Some Method](" in out


def test_nmux_class_private_data_dep_graph_case_unchanged():
    """Regression guard for the DEP-fields case (the class DOES resolve via
    the search path, as it always could): the resolved field name must be
    IDENTICAL whether or not the VI's own embedded snapshot is also
    available -- the own-embedded fallback must never override an
    authoritative dep_graph resolution, and this path must be byte-for-byte
    unchanged by the nMux-resolution consolidation."""
    if not _TESTRESULT_VI.exists():
        pytest.skip("JKI-VI-Tester sample not available")

    graph = InMemoryVIGraph()
    graph.load_vi(str(_TESTRESULT_VI), search_paths=[_TESTRESULT_DIR.parent])
    vi_name = "TestResult.lvclass:GetTestsRun.vi"

    out = render_netlist(build_netlist(graph, vi_name))
    assert "testsRun" in out
    assert "Bundle/Unbundle By Name.1" not in out


def test_get_context_dict_carries_representation_audit_fields() -> None:
    """The MCP get_context surface (netlist_to_dict on a real VI, end-to-end)
    carries the representation-audit additions -- not just the IR dataclasses:
    case gamma merges, loop eta merges, and the Compound Arithmetic operator.
    Calculate Test Coverage has all three (3 filter cases, 3 for-loops with
    auto-indexed outputs, and cpdArith ANDs). Guards against a projection
    regression that would leave get_context silently lossy again."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph, vi_name = _load_coverage_vi()
    d = netlist_to_dict(build_netlist(graph, vi_name))

    def _walk(items):
        for it in items:
            yield it
            if it.get("kind") == "scope":
                for fr in it["frames"]:
                    yield from _walk(fr["body"])

    body_items = list(_walk(d["body"]))
    scopes = [i for i in body_items if i.get("kind") == "scope"]
    # case gamma merges present in a case scope's outputs
    case_outs = [
        o for sc in scopes if sc["scope_kind"] == "case" for o in sc["outputs"]
    ]
    assert any(o["kind"] == "gamma" and o["selector"] for o in case_outs)
    # loop eta merges present in a loop scope's outputs
    loop_outs = [
        o
        for sc in scopes
        if sc["scope_kind"] in ("for", "while")
        for o in sc["outputs"]
    ]
    assert any(o["kind"] == "eta" for o in loop_outs)
    # Compound Arithmetic operator carried on an instance
    insts = [i for i in body_items if i.get("kind") == "instance"]
    assert any(i.get("operation") == "and" for i in insts)
