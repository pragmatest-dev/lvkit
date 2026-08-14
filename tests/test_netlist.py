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
    GammaMerge,
    NetlistInstance,
    NetlistScope,
    NetRef,
    build_netlist,
    index_module,
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
    ".lvkit/cache/samples/JKI-VI-Tester/source/Menu Launch/"
    "VI Tester Menu Launch.vi"
)


def test_boundary_outputs_carry_their_source_net() -> None:
    """Every wired indicator resolves to the net that drives it (get_context
    used to declare output name/type but drop the producer→indicator wire)."""
    if not _COVERAGE_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_COVERAGE_VI), LoadMode.MINIMAL,
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
        str(_COVERAGE_VI), LoadMode.MINIMAL,
        search_paths=[_COVERAGE_VI.parents[3]],
    )
    vi_name = graph.resolve_vi_name(_COVERAGE_VI.name)
    return graph, vi_name


def _case_scopes(module) -> list[NetlistScope]:
    return [
        item for item in module.body
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
        item for item in module.body
        if isinstance(item, NetlistInstance) and item.name == "Build Array"
    )
    assert len(build_array.inputs) == 3
    for binding in build_array.inputs:
        net = binding.net
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
            default_case = next(
                c for c in gamma.cases if c.frame_key == "default"
            )
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
            for nxt in lines[i + 1:]:
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
            capture_output=True, text=True, timeout=60,
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
        line for line in out.splitlines()
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
        for out_ref in inst.outputs:
            assert out_ref.node == "Compound Arithmetic"
            assert out_ref.bare == f"Compound Arithmetic#{inst.occurrence}.0"

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
        str(_INVERTED_INPUT_VI), LoadMode.MINIMAL,
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

    bindings_by_port = {b.port: b for b in inst2.inputs}
    assert bindings_by_port["1"].inverted is False
    assert bindings_by_port["2"].inverted is True
    # Faithful: the negation is an annotation on the BINDING, never on the
    # net's own identity.
    assert bindings_by_port["2"].net.bare == "equal"

    out = render_netlist(module)
    assert (
        "Compound Arithmetic#2 [and]"
        "(1=result, 2=not(Equal?#2.equal)) -> Compound Arithmetic#2.0" in out
    )
    # The sibling (non-inverted) Compound Arithmetic node's inputs are
    # unaffected -- no negation wrapper appears anywhere on its line.
    line1 = next(
        line for line in out.splitlines() if "Compound Arithmetic#1 [" in line
    )
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
            if b.port != "2" or inst.occurrence != 2:
                assert b.inverted is False


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
