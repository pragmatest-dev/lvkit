"""Regression tests for type-driven fixes (Fixes A-F).

Each test targets a specific bug that was caused by heuristic/string-based
guessing instead of using the actual lv_type data on terminals and constants.
"""

from __future__ import annotations

import math

from lvkit.codegen.context import _format_constant
from lvkit.graph.models import Constant
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
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
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
        )
        result = _format_constant(const)
        # Should be a valid float, not the raw hex string
        assert result != "'7FFFFFFFFFFFFFFF'"
        assert result != "7FFFFFFFFFFFFFFF"
        assert math.isnan(eval(result, {"__builtins__": {"float": float}}))

    def test_hex_int32_decoded_as_integer(self):
        """Hex string with NumInt32 type must decode as integer."""
        const = Constant(
            id="c2",
            value="0A",
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
        )
        result = _format_constant(const)
        assert result == "10"

    def test_hex_float32_decoded_as_float(self):
        """Hex string with NumFloat32 type must decode as float."""
        const = Constant(
            id="c3",
            value="40490FDB",
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat32"),
        )
        result = _format_constant(const)
        val = float(result)
        assert abs(val - 3.1415927) < 0.001

    def test_decimal_string_with_numeric_type(self):
        """Plain decimal string with numeric type stays as integer."""
        const = Constant(
            id="c4",
            value="42",
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt16"),
        )
        result = _format_constant(const)
        assert result == "42"

    def test_string_type_not_decoded_as_number(self):
        """A string constant that looks like a number must stay a string
        when lv_type says String."""
        const = Constant(
            id="c5",
            value='"42"',
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
        )
        result = _format_constant(const)
        assert result == "'42'"

    def test_empty_string_constant(self):
        """Empty string with String type should be ''."""
        const = Constant(
            id="c6",
            value='""',
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
        )
        result = _format_constant(const)
        assert result == "''"


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
            kind=LVTypeKind.CLUSTER,
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
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
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
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
        )
        assert term.is_error_cluster is False
