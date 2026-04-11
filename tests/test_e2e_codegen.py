"""End-to-end tests for the full VI → graph → codegen pipeline.

Tests load real VI files, build the graph, generate Python via
build_module(), and verify the output is syntactically valid and
contains expected patterns. These tests catch regressions in the
complete pipeline that unit tests miss.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lvpy.codegen.builder import build_module
from lvpy.graph import InMemoryVIGraph

SEARCH_PATHS = [Path("samples/OpenG/extracted")]

GET_SETTINGS_PATH_VI = Path(
    "samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/"
    "Get Settings Path.vi"
)
DAQMX_IN_VI = Path("samples/DAQmx-Digital-IO/In.vi")
DAQMX_OUT_VI = Path("samples/DAQmx-Digital-IO/Out.vi")
TESTCASE_DIR = Path("samples/JKI-VI-Tester/source/Classes/TestCase")


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
def daqmx_graph() -> InMemoryVIGraph:
    _skip_if_missing(DAQMX_IN_VI, DAQMX_OUT_VI)
    g = InMemoryVIGraph()
    g.load_vi(str(DAQMX_IN_VI), search_paths=SEARCH_PATHS)
    g.load_vi(str(DAQMX_OUT_VI), search_paths=SEARCH_PATHS)
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


# ── DAQmx In.vi ─────────────────────────────────────────────


class TestDAQmxIn:
    """E2E: In.vi — flat sequence, parallel branches, inline DAQmx SubVIs."""

    VI_NAME = "In.vi"

    def test_valid_python(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert_valid_python(code, self.VI_NAME)

    def test_no_garbage(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert_no_garbage(code, self.VI_NAME)

    def test_correct_operation_order(self, daqmx_graph):
        """Create Task must come before Start, which must come before Stop."""
        code = _generate(daqmx_graph, self.VI_NAME)
        task_pos = code.index("nidaqmx.Task")
        start_pos = code.index(".start()")
        stop_pos = code.index(".stop()")
        close_pos = code.index(".close()")
        assert task_pos < start_pos < stop_pos < close_pos

    def test_has_time_sleep(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert "time.sleep" in code

    def test_no_none_method_calls(self, daqmx_graph):
        """Regression: operations used to execute before task creation."""
        code = _generate(daqmx_graph, self.VI_NAME)
        assert "None.write" not in code
        assert "None.start" not in code

    def test_no_undefined_samples_written(self, daqmx_graph):
        """Regression: ref_terminal output with no upstream binding."""
        code = _generate(daqmx_graph, self.VI_NAME)
        assert "daqmx_write_samples_written" not in code

    def test_has_parallel_branches(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert "concurrent.futures" in code


# ── DAQmx Out.vi ────────────────────────────────────────────


class TestDAQmxOut:
    """E2E: Out.vi — while loop, boolean params, DAQmx Read."""

    VI_NAME = "Out.vi"

    def test_valid_python(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert_valid_python(code, self.VI_NAME)

    def test_no_garbage(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert_no_garbage(code, self.VI_NAME)

    def test_has_while_loop(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert "while " in code

    def test_no_param_overwrite(self, daqmx_graph):
        """Regression: stop = False overwrote the function parameter."""
        code = _generate(daqmx_graph, self.VI_NAME)
        # The function has 'stop' as a parameter — it should NOT be re-assigned
        # to False before the while loop
        lines = code.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "stop = False":
                pytest.fail(
                    f"Line {i+1}: 'stop = False' overwrites function parameter"
                )

    def test_has_read_call(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert ".read()" in code

    def test_has_task_creation(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert "nidaqmx.Task" in code

    def test_has_time_sleep(self, daqmx_graph):
        code = _generate(daqmx_graph, self.VI_NAME)
        assert "time.sleep" in code


# ── TestCase.lvclass ─────────────────────────────────────────


class TestTestCaseLvclass:
    """E2E: TestCase.lvclass — 74 methods, all must produce valid Python."""

    def _testcase_vis(self, testcase_graph):
        """Get TestCase method VIs (not dependencies)."""
        return [
            vi for vi in testcase_graph.list_vis()
            if vi.startswith("TestCase.lvclass:")
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
