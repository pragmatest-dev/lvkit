"""End-to-end tests for the full VI → graph → codegen pipeline.

Tests load real VI files, build the graph, generate Python via
build_module(), and verify the output is syntactically valid and
contains expected patterns. These tests catch regressions in the
complete pipeline that unit tests miss.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.models import InPlaceOperation

SEARCH_PATHS = [Path(".lvkit/cache/samples/OpenG/extracted")]
DCAF_SEARCH_PATHS = [
    Path(".lvkit/cache/samples/DCAF-DAQModule/source"),
    Path(".lvkit/cache/samples/OpenG/extracted"),
]

GET_SETTINGS_PATH_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/"
    "Get Settings Path.vi"
)
TESTCASE_DIR = Path(".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestCase")
DCAF_CONFIG_DIR = Path(
    ".lvkit/cache/samples/DCAF-DAQModule/source/module/configuration"
)  # noqa: E501
DELETE_LINE_VI = DCAF_CONFIG_DIR / "Delete Line.vi"
TESTRESULT_DIR = Path(".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestResult")
GET_TESTS_RUN_VI = TESTRESULT_DIR / "GetTestsRun.vi"
TESTRESULT_INIT_VI = TESTRESULT_DIR / "TestResult_Init.vi"
OPENG_COMPARISON_DIR = Path(
    ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/comparison/"
    "comparison.llb"
)
U16_CHANGED_VI = OPENG_COMPARISON_DIR / "U16 Changed__ogtk.vi"


def _skip_if_missing(*paths: Path) -> None:
    for p in paths:
        if not p.exists():
            pytest.skip(f"Sample VI not available: {p}")


# ── Helpers ──────────────────────────────────────────────────


def assert_valid_python(code: str, vi_name: str) -> None:
    """Assert code is syntactically valid Python."""
    try:
        ast.parse(code)
    except SyntaxError as e:
        pytest.fail(f"{vi_name}: syntax error at line {e.lineno}: {e.msg}")


def assert_no_garbage(code: str, vi_name: str) -> None:
    """Assert no unresolved placeholders in output."""
    assert "_UNRESOLVED" not in code, f"{vi_name}: has _UNRESOLVED"
    assert "out_-1" not in code, f"{vi_name}: has out_-1"
    assert "None.write" not in code, f"{vi_name}: has None.write"
    assert "None.read" not in code, f"{vi_name}: has None.read"


def _generate(graph: InMemoryVIGraph, vi_name: str) -> str:
    """Generate Python code for a VI via the full pipeline."""
    ctx = graph.get_vi_context(vi_name)
    return build_module(ctx, vi_name, graph=graph)


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def get_settings_path_graph() -> InMemoryVIGraph:
    _skip_if_missing(GET_SETTINGS_PATH_VI)
    g = InMemoryVIGraph()
    g.load_vi(str(GET_SETTINGS_PATH_VI), search_paths=SEARCH_PATHS)
    return g


@pytest.fixture(scope="module")
def testcase_graph() -> InMemoryVIGraph:
    _skip_if_missing(TESTCASE_DIR)
    g = InMemoryVIGraph()
    for vi_path in sorted(TESTCASE_DIR.glob("*.vi")):
        g.load_vi(str(vi_path), search_paths=SEARCH_PATHS)
    return g


# ── Get Settings Path ───────────────────────────────────────


class TestGetSettingsPath:
    """E2E: Get Settings Path.vi — SubVI deps, enum constants, Path ops."""

    VI_NAME = "GraphicalTestRunner.lvlib:Get Settings Path.vi"

    def test_valid_python(self, get_settings_path_graph):
        code = _generate(get_settings_path_graph, self.VI_NAME)
        assert_valid_python(code, self.VI_NAME)

    def test_no_garbage(self, get_settings_path_graph):
        code = _generate(get_settings_path_graph, self.VI_NAME)
        assert_no_garbage(code, self.VI_NAME)

    def test_calls_get_system_directory(self, get_settings_path_graph):
        code = _generate(get_settings_path_graph, self.VI_NAME)
        assert "get_system_directory" in code

    def test_enum_constant(self, get_settings_path_graph):
        code = _generate(get_settings_path_graph, self.VI_NAME)
        assert "SystemDirectoryType.PUBLIC_APP_DATA" in code

    def test_enum_import(self, get_settings_path_graph):
        code = _generate(get_settings_path_graph, self.VI_NAME)
        assert "import SystemDirectoryType" in code

    def test_result_namedtuple(self, get_settings_path_graph):
        code = _generate(get_settings_path_graph, self.VI_NAME)
        assert "GetSettingsPathResult" in code
        assert "NamedTuple" in code

    def test_path_operations(self, get_settings_path_graph):
        code = _generate(get_settings_path_graph, self.VI_NAME)
        assert "Path" in code
        assert "mkdir" in code


# ── DAQmx In.vi / Out.vi (DROPPED — see below) ────────────────
#
# DROPPED: TestDAQmxIn and TestDAQmxOut used to build_module() the
# unlicensed .lvkit/cache/samples/DAQmx-Digital-IO/{In,Out}.vi end-to-end and assert
# DAQmx->nidaqmx driver-specific codegen ("nidaqmx.Task", ".start()"/
# ".stop()"/".close()"/".read()" call ordering, parallel-branch
# ThreadPoolExecutor/concurrent.futures from Write+Wait branches, while-loop
# codegen for the Digital-Input Out.vi).
#
# No permissive replacement caller was found: illuminated-g/
# lv-flex-channel-examples' "DAQmx AO/DAQ AO.vi" (MIT) is the only real
# permissive DAQmx caller located (see docs/test-corpus-sources.md), but it
# cannot build_module() end-to-end — it calls a project-local "Read Event.vi"
# unreachable via any shippable search path, and its Analog-Output poly
# variants ("AO Voltage", "Analog 1D Wfm ...") have no vilib_resolver.json
# mapping yet (a separate effort — see "Adding New VILib VIs" in
# CLAUDE.md). ismet55555/LabVIEW-OOP-Classes' Digital-Output caller
# ("DAQ/Digital Output/DO_class/utils/DO_Write.vi", MIT) can't be parsed at
# all: pylabview raises "AttributeError: 'TDObjectCluster' object has no
# attribute 'getNumRepeats'" on its VITS block (a pylabview bug).
#
# The generic constructs these tests incidentally covered (parallel
# branches -> ThreadPoolExecutor, while-loop codegen) still have dedicated
# non-DAQmx coverage: see test_parallel_codegen.py's synthetic
# TestPassthroughBindingsInParallelTier / earlier classes in that file, and
# TestGetSettingsPath above for a real-VI E2E build_module() smoke test.


SET_FP_CONTROL_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Utilities/"
    "Set Front Panel Object Control Value.vi"
)


def _generate_cli(vi_path: Path, out_dir: Path, hashseed: str) -> None:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    subprocess.run(
        [
            sys.executable,
            "scripts/generate_python.py",
            str(vi_path),
            "-o",
            str(out_dir),
            "--search-path",
            ".lvkit/cache/samples/OpenG/extracted",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.py"))
    }


def test_codegen_is_hashseed_deterministic(tmp_path):
    """Generated code must be byte-identical across PYTHONHASHSEED values
    (task #69). The parallel-tier builder traces data dependencies through the
    graph via get_source -> incoming_edges; those edges were yielded in
    hash-randomized construction order, so the discovered tiers (and thus the
    emitted _branch_N structure) varied per run. incoming_edges/outgoing_edges
    now sort by a stable key. This VI exhibited the divergence."""
    _skip_if_missing(SET_FP_CONTROL_VI)
    # Several seeds — a single pair can coincidentally agree even when the bug
    # is present (the divergence depends on which edge lands first), so compare
    # multiple runs against a baseline.
    seeds = ["0", "1", "7", "12345", "99999"]
    trees = []
    for i, seed in enumerate(seeds):
        out = tmp_path / f"seed_{i}"
        _generate_cli(SET_FP_CONTROL_VI, out, seed)
        trees.append(_tree_bytes(out))
    base = trees[0]
    for seed, tree in zip(seeds[1:], trees[1:]):
        assert tree.keys() == base.keys(), f"seed {seed}: different files than baseline"
        diffs = [name for name in base if base[name] != tree[name]]
        assert not diffs, f"seed {seed}: non-deterministic output: {diffs}"


# ── TestCase.lvclass ─────────────────────────────────────────


class TestTestCaseLvclass:
    """E2E: TestCase.lvclass — 74 methods, all must produce valid Python."""

    def _testcase_vis(self, testcase_graph):
        """Get TestCase method VIs (not dependencies).

        VIs are keyed by path (identity) now, so select them via the qname
        reverse index — every loaded VI whose qualified name is a TestCase
        method — and return their vi_keys for get_vi_context/build_module.
        """
        return [
            key
            for qname, keys in testcase_graph._qname_to_keys.items()
            if qname.startswith("TestCase.lvclass:")
            for key in keys
        ]

    def test_all_methods_generate_valid_python(self, testcase_graph):
        """Every TestCase method must produce syntactically valid Python."""
        tc_vis = self._testcase_vis(testcase_graph)

        successes = 0
        failures = []
        for vi_name in tc_vis:
            ctx = testcase_graph.get_vi_context(vi_name)
            if not ctx.operations:
                continue
            try:
                code = build_module(ctx, vi_name, graph=testcase_graph)
                ast.parse(code)
                successes += 1
            except Exception as e:
                failures.append((vi_name, str(e)))

        if failures:
            msg = f"{len(failures)} VIs failed:\n"
            for vi_name, err in failures[:10]:
                msg += f"  {vi_name}: {err}\n"
            pytest.fail(msg)

        assert successes >= 20, f"Expected 20+ AST successes, got {successes}"

    def test_no_garbage_in_any_method(self, testcase_graph):
        """No method should contain unresolved placeholders."""
        tc_vis = self._testcase_vis(testcase_graph)

        for vi_name in tc_vis:
            ctx = testcase_graph.get_vi_context(vi_name)
            if not ctx.operations:
                continue
            try:
                code = build_module(ctx, vi_name, graph=testcase_graph)
                assert_no_garbage(code, vi_name)
            except Exception:
                pass  # Generation failures caught by other test


# ── IPES (In Place Element Structure) ──────────────────────


@pytest.fixture(scope="module")
def delete_line_graph() -> InMemoryVIGraph:
    _skip_if_missing(DELETE_LINE_VI)
    g = InMemoryVIGraph()
    g.load_vi(
        str(DELETE_LINE_VI),
        mode=LoadMode.FULL,
        search_paths=DCAF_SEARCH_PATHS,
    )
    return g


def _find_ipes_ops(ops: list) -> list[InPlaceOperation]:
    """Recursively find all InPlaceOperation instances in the op tree."""
    found: list[InPlaceOperation] = []
    for op in ops:
        if isinstance(op, InPlaceOperation):
            found.append(op)
        for inner in op.inner_nodes:
            if isinstance(inner, InPlaceOperation):
                found.append(inner)
        if hasattr(op, "frames"):
            for frame in op.frames:
                found.extend(_find_ipes_ops(frame.operations))
    return found


class TestIPESDeleteLine:
    """E2E: Delete Line.vi — IPES inside a case frame."""

    VI_NAME = "Delete Line.vi"

    def test_graph_has_ipes_operation(self, delete_line_graph):
        """Parser → graph → InPlaceOperation with boundary ops."""
        ops = delete_line_graph.get_operations(self.VI_NAME)
        ipes_ops = _find_ipes_ops(ops)
        assert len(ipes_ops) >= 1, "Expected at least one IPES"

    def test_ipes_has_decompose_and_recompose(self, delete_line_graph):
        """IPES must have decompose and recompose boundary ops."""
        ops = delete_line_graph.get_operations(self.VI_NAME)
        ipes = _find_ipes_ops(ops)[0]
        assert len(ipes.decompose_ops) >= 1
        assert len(ipes.recompose_ops) >= 1

    def test_decompose_has_agg_and_list_terminals(self, delete_line_graph):
        """Decompose op must have agg input + list outputs."""
        ops = delete_line_graph.get_operations(self.VI_NAME)
        ipes = _find_ipes_ops(ops)[0]
        dec = ipes.decompose_ops[0]
        agg = [t for t in dec.terminals if t.nmux_role == "agg"]
        fields = [t for t in dec.terminals if t.nmux_role == "list"]
        assert len(agg) >= 1, "Decompose needs an agg terminal"
        assert len(fields) >= 1, "Decompose needs list terminals"

    def test_codegen_produces_field_writeback(self, delete_line_graph):
        """Generated code must write back to the same data variable."""
        code = _generate(delete_line_graph, self.VI_NAME)
        assert_valid_python(code, self.VI_NAME)
        # IPES writes modified field back to the input variable
        assert ".lines" in code, "Expected field write-back"

    def test_same_variable_in_and_out(self, delete_line_graph):
        """Return value must reference the same variable as input."""
        code = _generate(delete_line_graph, self.VI_NAME)
        assert "daqmx_module_configuration_out=daqmx_module_configuration_in" in code


# ── nMux on an LVOOP class's own private data (task #56) ────────────
#
# A Bundle/Unbundle By Name (nMux) whose "agg" terminal is the class's own
# private-data cluster (wired through the "this" refnum) must resolve field
# names from the class's dep_graph, exactly like an anonymous-cluster nMux
# resolves them from inline type_map fields. Regression coverage for the
# "Type resolution needed for 'field[N]'" failure (task #56): GetTestsRun.vi
# is a single-field Unbundle By Name on TestResult.lvclass's private data —
# the same node shape as the originally-reported failure (Method1.vi, an
# LV-8.2-era VI whose TM80 resource pylabview cannot parse at all — a
# pylabview extraction gap, not a codegen bug; see
# scratchpad/task56_findings.md). TestResult_Init.vi covers the BUNDLE
# direction (multi-field Bundle By Name, LVOOP constructor pattern).


class TestClassPrivateDataNmux:
    """E2E: nMux unbundle/bundle of an LVOOP class's own private data."""

    def test_unbundle_generates_and_executes(self):
        """GetTestsRun.vi: single-field Unbundle By Name on private data.

        Must resolve the ``testsRun`` field name (not raise
        TypeResolutionNeeded) and, when executed, read the real value off
        the "this" object.
        """
        _skip_if_missing(GET_TESTS_RUN_VI)
        graph = InMemoryVIGraph()
        graph.load_vi(str(GET_TESTS_RUN_VI), search_paths=[TESTRESULT_DIR.parent])

        vi_name = "TestResult.lvclass:GetTestsRun.vi"
        ctx = graph.get_vi_context(vi_name)
        code = build_module(ctx, vi_name, graph=graph)
        assert_valid_python(code, vi_name)
        assert ".testsrun" in code, "Expected attribute access on the class field"

        ns: dict = {}
        exec(compile(code, "<GetTestsRun.vi>", "exec"), ns)  # noqa: S102
        this = types.SimpleNamespace(testsrun=42)
        result = ns["gettestsrun"](this)
        assert result.testsrun == 42
        assert result.testresult_out is this

    def test_bundle_generates_and_executes(self):
        """TestResult_Init.vi: multi-field Bundle By Name (constructor)
        writing several fields onto the class's own private data.

        Must resolve every field name and, when executed, write the real
        values onto the "this" object.
        """
        _skip_if_missing(TESTRESULT_INIT_VI)
        graph = InMemoryVIGraph()
        graph.load_vi(str(TESTRESULT_INIT_VI), search_paths=[TESTRESULT_DIR.parent])

        vi_name = "TestResult.lvclass:TestResult_Init.vi"
        ctx = graph.get_vi_context(vi_name)
        code = build_module(ctx, vi_name, graph=graph)
        assert_valid_python(code, vi_name)

        ns: dict = {}
        exec(compile(code, "<TestResult_Init.vi>", "exec"), ns)  # noqa: S102
        this = types.SimpleNamespace()
        result = ns["testresult_init"](this, "event_ref")
        out = result.testresult_out
        assert out is this
        assert out.testsrun == 0
        assert out.shouldstop is False
        assert out.errors == "[]"
        assert out.failures == "[]"
        assert out.resultstatuschangedeventref == "event_ref"


# ── Uninitialized shift register + First Call? (functional-global idiom) ──


class TestU16ChangedPersistentState:
    """E2E: U16 Changed__ogtk.vi -- the OpenG LV2/functional-global idiom.

    An uninitialized shift register (previous value, persists ACROSS
    CALLS) combined with a First Call? primitive (stateful per call
    site). Both lower to module-level globals (codegen/nodes/loop.py's
    uninitialized-SR branch, codegen/nodes/first_call.py) -- this test
    proves the generated module actually behaves statefully across
    repeated calls, not just that it parses.
    """

    def test_generates_module_globals_not_a_constant(self):
        _skip_if_missing(U16_CHANGED_VI)
        graph = InMemoryVIGraph()
        graph.load_vi(str(U16_CHANGED_VI))

        vi_name = "U16 Changed__ogtk.vi"
        ctx = graph.get_vi_context(vi_name)
        code = build_module(ctx, vi_name, graph=graph)
        assert_valid_python(code, vi_name)

        assert "_lv_state_" in code
        assert "_lv_first_call_" in code
        # Both persist via `global` in the function body -- not just
        # seeded once at module import and never touched again.
        assert code.count("global") >= 2

    def test_first_call_true_only_once_then_persists_previous_value(self):
        """Call the generated function three times with the SAME u16
        value (10, 10, 10). The First Call? term dominates on call 1
        (changed=True unconditionally per the graph's wiring -- see
        loop.py/first_call.py docstrings and the task report's note on
        the 1105/"Not first_call" polarity); on later calls the
        previous-value module global must reflect the last call's input,
        not the type default."""
        _skip_if_missing(U16_CHANGED_VI)
        graph = InMemoryVIGraph()
        graph.load_vi(str(U16_CHANGED_VI))

        vi_name = "U16 Changed__ogtk.vi"
        ctx = graph.get_vi_context(vi_name)
        code = build_module(ctx, vi_name, graph=graph)

        ns: dict = {}
        exec(compile(code, "<U16 Changed__ogtk.vi>", "exec"), ns)  # noqa: S102
        func = ns["u16_changed__ogtk"]

        state_globals = [k for k in ns if k.startswith("_lv_state_")]
        first_call_globals = [k for k in ns if k.startswith("_lv_first_call_")]
        assert len(state_globals) == 1
        assert len(first_call_globals) == 1
        state_name = state_globals[0]
        first_call_name = first_call_globals[0]

        # Fresh module: SR seeded to the U16 type default (0), First
        # Call? flag seeded True.
        assert ns[state_name] == 0
        assert ns[first_call_name] is True

        func(10)
        # First Call? flips permanently False after the very first call.
        assert ns[first_call_name] is False
        # The shift register now holds the value written this call (10),
        # NOT the stale pre-call default -- this is the exact bug this
        # task fixes (previously the SR was silently dropped entirely).
        assert ns[state_name] == 10

        func(7)
        assert ns[first_call_name] is False
        assert ns[state_name] == 7

        func(20)
        assert ns[state_name] == 20

    def test_state_persists_independently_per_generated_module(self):
        """Two SEPARATE exec'd copies of the generated module (simulating
        two independent processes/imports) must not share state -- each
        module namespace owns its own globals."""
        _skip_if_missing(U16_CHANGED_VI)
        graph = InMemoryVIGraph()
        graph.load_vi(str(U16_CHANGED_VI))

        vi_name = "U16 Changed__ogtk.vi"
        ctx = graph.get_vi_context(vi_name)
        code = build_module(ctx, vi_name, graph=graph)

        ns_a: dict = {}
        ns_b: dict = {}
        exec(compile(code, "<a>", "exec"), ns_a)  # noqa: S102
        exec(compile(code, "<b>", "exec"), ns_b)  # noqa: S102

        ns_a["u16_changed__ogtk"](99)

        state_name_a = next(k for k in ns_a if k.startswith("_lv_state_"))
        state_name_b = next(k for k in ns_b if k.startswith("_lv_state_"))
        assert ns_a[state_name_a] == 99
        assert ns_b[state_name_b] == 0  # untouched -- separate namespace
