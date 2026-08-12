"""Regression tests for type-driven fixes (Fixes A-F).

Each test targets a specific bug that was caused by heuristic/string-based
guessing instead of using the actual lv_type data on terminals and constants.
"""

from __future__ import annotations

from lvkit.codegen.context import CodeGenContext, _format_constant
from lvkit.codegen.nodes import primitive
from lvkit.graph.models import Constant
from lvkit.models import ClusterField, LVType, Terminal
from lvkit.primitive_resolver import _collect_imports

# ── Fix A: Type-driven constant decoding ────────────────────────────


class TestTypeDrivenConstantDecoding:
    """Constants should be decoded using lv_type, not by guessing from
    string content or hex length."""

    def test_hex_float64_decoded_as_float(self):
        """Bug 11: '7FFFFFFFFFFFFFFF' with NumFloat64 type must decode to float,
        not be returned as a raw hex string."""
        const = Constant(
            id="c1",
            value="7FFFFFFFFFFFFFFF",
            lv_type=LVType(kind="primitive", underlying_type="NumFloat64"),
        )
        result = _format_constant(const)
        # Should be a valid float, not the raw hex string
        assert result != "'7FFFFFFFFFFFFFFF'"
        assert result != "7FFFFFFFFFFFFFFF"
        float(result)  # must not raise

    def test_hex_int32_decoded_as_integer(self):
        """Hex string with NumInt32 type must decode as integer."""
        const = Constant(
            id="c2",
            value="0A",
            lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
        )
        result = _format_constant(const)
        assert result == "10"

    def test_hex_float32_decoded_as_float(self):
        """Hex string with NumFloat32 type must decode as float."""
        const = Constant(
            id="c3",
            value="40490FDB",
            lv_type=LVType(kind="primitive", underlying_type="NumFloat32"),
        )
        result = _format_constant(const)
        val = float(result)
        assert abs(val - 3.1415927) < 0.001

    def test_decimal_string_with_numeric_type(self):
        """Plain decimal string with numeric type stays as integer."""
        const = Constant(
            id="c4",
            value="42",
            lv_type=LVType(kind="primitive", underlying_type="NumInt16"),
        )
        result = _format_constant(const)
        assert result == "42"

    def test_string_type_not_decoded_as_number(self):
        """A string constant that looks like a number must stay a string
        when lv_type says String."""
        const = Constant(
            id="c5",
            value='"42"',
            lv_type=LVType(kind="primitive", underlying_type="String"),
        )
        result = _format_constant(const)
        assert result == "'42'"

    def test_empty_string_constant(self):
        """Empty string with String type should be ''."""
        const = Constant(
            id="c6",
            value='""',
            lv_type=LVType(kind="primitive", underlying_type="String"),
        )
        result = _format_constant(const)
        assert result == "''"


# ── Fix B: Type-driven terminal defaults ────────────────────────────


class TestTypeDrivenTerminalDefaults:
    """Unwired terminals should get defaults from lv_type, not from
    name substring matching."""

    def test_numeric_terminal_gets_zero_not_empty_string(self):
        """Bug 1: Unwired numeric terminal (e.g., 'length') must default
        to 0, not '' (which happened when 'string' was in the name)."""
        term = Terminal(
            id="t1",
            index=0,
            direction="input",
            name="string length",  # name contains "string" but type is numeric
            lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
        )
        ctx = CodeGenContext()
        result = primitive._default_for_type(term, ctx)
        assert result == "0"

    def test_string_terminal_gets_empty_string(self):
        """Unwired string terminal should default to ''."""
        term = Terminal(
            id="t2",
            index=1,
            direction="input",
            name="input",
            lv_type=LVType(kind="primitive", underlying_type="String"),
        )
        ctx = CodeGenContext()
        result = primitive._default_for_type(term, ctx)
        assert result == "''"

    def test_boolean_terminal_gets_false(self):
        """Unwired boolean terminal should default to False."""
        term = Terminal(
            id="t3",
            index=2,
            direction="input",
            name="enable",
            lv_type=LVType(kind="primitive", underlying_type="Boolean"),
        )
        ctx = CodeGenContext()
        result = primitive._default_for_type(term, ctx)
        assert result == "False"

    def test_path_terminal_gets_path_default(self):
        """Unwired path terminal should default to Path('.')."""
        term = Terminal(
            id="t4",
            index=3,
            direction="input",
            name="file path",
            lv_type=LVType(kind="primitive", underlying_type="Path"),
        )
        ctx = CodeGenContext()
        result = primitive._default_for_type(term, ctx)
        assert result == "Path('.')"
        assert "from pathlib import Path" in ctx.imports

    def test_array_terminal_gets_empty_list(self):
        """Unwired array terminal should default to []."""
        term = Terminal(
            id="t5",
            index=4,
            direction="input",
            name="data",
            lv_type=LVType(kind="array", underlying_type=None),
        )
        ctx = CodeGenContext()
        result = primitive._default_for_type(term, ctx)
        assert result == "[]"

    def test_no_type_gets_none(self):
        """Unwired terminal with no type info should default to None."""
        term = Terminal(
            id="t6",
            index=5,
            direction="input",
            name="unknown",
            lv_type=None,
        )
        ctx = CodeGenContext()
        result = primitive._default_for_type(term, ctx)
        assert result == "None"

    def test_refnum_terminal_uses_type_not_name(self):
        """Fix F: Unwired refnum terminal should be detected by lv_type,
        not by 'refnum' in terminal name."""
        term = Terminal(
            id="t7",
            index=0,
            direction="input",
            name="some_input",  # no "refnum" in name
            lv_type=LVType(kind="primitive", underlying_type="Refnum"),
        )
        ctx = CodeGenContext()
        # With no primResID match for file I/O, should fall through to default
        result = primitive._default_for_type(term, ctx)
        assert result == "None"


# ── Fix C: _import field reading ────────────────────────────────────


class TestImportFieldReading:
    """Primitive resolver must read both 'imports' list and '_import' string."""

    def test_collect_imports_string(self):
        """Bug 5: _import as string (e.g., 'import random') must be collected."""
        prim = {"_import": "import random"}
        result = _collect_imports(prim)
        assert result == ["import random"]

    def test_collect_imports_list(self):
        """_import as list must be collected."""
        prim = {"_import": ["import os", "import sys"]}
        result = _collect_imports(prim)
        assert result == ["import os", "import sys"]

    def test_collect_imports_both_fields(self):
        """Both 'imports' and '_import' should be merged."""
        prim = {
            "imports": ["import math"],
            "_import": "import random",
        }
        result = _collect_imports(prim)
        assert result == ["import math", "import random"]

    def test_collect_imports_empty(self):
        """No import fields should return empty list."""
        prim = {"name": "foo"}
        result = _collect_imports(prim)
        assert result == []


# ── Fix F: Error cluster detection by type, not name ────────────────


class TestErrorClusterByType:
    """Error cluster detection must use is_error_cluster from the type system,
    never string matching on terminal/parameter names."""

    def _error_cluster_type(self) -> LVType:
        return LVType(
            kind="cluster",
            underlying_type="Cluster",
            fields=[
                ClusterField(name="status"),
                ClusterField(name="code"),
                ClusterField(name="source"),
            ],
        )

    def test_error_named_terminal_without_error_type_is_not_skipped(self):
        """A terminal named 'error_out' but without error cluster type
        should NOT be treated as an error cluster."""
        term = Terminal(
            id="t1",
            index=0,
            direction="output",
            name="error_out",
            lv_type=LVType(kind="primitive", underlying_type="String"),
        )
        # is_error_cluster should be False because the type is String
        assert term.is_error_cluster is False

    def test_actual_error_cluster_is_detected(self):
        """A terminal with proper error cluster type should be detected."""
        term = Terminal(
            id="t2",
            index=1,
            direction="output",
            name="some_output",
            lv_type=self._error_cluster_type(),
        )
        assert term.is_error_cluster is True

    def test_non_error_cluster_with_error_in_name(self):
        """Terminals with 'error' in name but non-cluster type must not
        be classified as error clusters."""
        term = Terminal(
            id="t3",
            index=2,
            direction="input",
            name="error_count",
            lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
        )
        assert term.is_error_cluster is False
