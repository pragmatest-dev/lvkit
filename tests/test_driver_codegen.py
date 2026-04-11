"""Tests for driver terminal resolution, polymorphic variant detection,
constant formatting, and dead code preservation of side effects.

Covers changes from the DAQmx Digital IO fix:
- Boolean constant formatting (True/False not strings)
- Path-like strings not wrapped in Path()
- Wait (ms) primitive (primResID 1302) generates time.sleep()
- Dead code eliminator preserves side-effecting calls
- polySelector variant name extraction from XML
- poly_selector_names lookup in vilib resolver
"""

from __future__ import annotations

import ast

from lvpy.codegen.ast_optimizer import eliminate_dead_code
from lvpy.codegen.context import _format_constant
from lvpy.graph_types import Constant, LVType

# =============================================================
# Boolean constant formatting
# =============================================================


class TestBooleanFormatting:
    """Boolean constants should format as True/False, not strings."""

    def test_boolean_true_from_hex_01(self):
        const = Constant(
            id="c1", value="True",
            lv_type=LVType(kind="primitive", underlying_type="Boolean"),
        )
        assert _format_constant(const) == "True"

    def test_boolean_false_from_hex_0000(self):
        const = Constant(
            id="c2", value="0000",
            lv_type=LVType(kind="primitive", underlying_type="Boolean"),
        )
        assert _format_constant(const) == "False"

    def test_boolean_false_from_zero(self):
        const = Constant(
            id="c3", value="0",
            lv_type=LVType(kind="primitive", underlying_type="Boolean"),
        )
        assert _format_constant(const) == "False"

    def test_boolean_true_from_one(self):
        const = Constant(
            id="c4", value="1",
            lv_type=LVType(kind="primitive", underlying_type="Boolean"),
        )
        assert _format_constant(const) == "True"


# =============================================================
# Path-like string formatting
# =============================================================


class TestPathFormatting:
    """Strings with slashes should NOT be wrapped in Path() unless lv_type is Path."""

    def test_channel_string_not_wrapped_in_path(self):
        """DAQmx channel strings like 'Dev1/port0/line0' are strings, not paths."""
        const = Constant(
            id="c1", value='"Dev1/port0/line0"',
            lv_type=LVType(kind="primitive", underlying_type="Tag"),
        )
        result = _format_constant(const)
        assert "Path(" not in result
        assert "Dev1/port0/line0" in result

    def test_actual_path_type_uses_path(self):
        """Constants with underlying_type=Path should use Path()."""
        const = Constant(
            id="c2", value="some/file.txt",
            lv_type=LVType(kind="primitive", underlying_type="Path"),
        )
        result = _format_constant(const)
        assert result == "Path('some/file.txt')"

    def test_plain_string_no_path(self):
        """Plain strings without path-like content stay as strings."""
        const = Constant(
            id="c3", value='"hello"',
            lv_type=LVType(kind="primitive", underlying_type="String"),
        )
        result = _format_constant(const)
        assert result == "'hello'"


# =============================================================
# Dead code eliminator preserves side effects
# =============================================================


class TestDeadCodeSideEffects:
    """Dead assignments with function calls must preserve the call."""

    def test_dead_assignment_with_call_preserved(self):
        """time.sleep() assigned to unused var should keep the call."""
        code = """
def test_func():
    unused = time.sleep(0.5)
    x = 1
    return x
"""
        module = ast.parse(code)
        optimized = eliminate_dead_code(module)
        result = ast.unparse(optimized)

        # The call must be preserved as an expression statement
        assert "time.sleep(0.5)" in result
        # But the assignment should be gone
        assert "unused = " not in result

    def test_dead_assignment_without_call_removed(self):
        """Dead assignment to a literal should be fully removed."""
        code = """
def test_func():
    unused = 42
    x = 1
    return x
"""
        module = ast.parse(code)
        optimized = eliminate_dead_code(module)
        result = ast.unparse(optimized)

        assert "unused" not in result
        assert "42" not in result

    def test_dead_assignment_with_method_call_preserved(self):
        """task.write() assigned to unused var should keep the call."""
        code = """
def test_func():
    result = task.write(True)
    return None
"""
        module = ast.parse(code)
        optimized = eliminate_dead_code(module)
        result = ast.unparse(optimized)

        assert "task.write(True)" in result
        assert "result = " not in result


# =============================================================
# Wait (ms) primitive resolution
# =============================================================


class TestWaitMsPrimitive:
    """primResID 1302 should resolve to Wait (ms), not Bundle."""

    def test_1302_resolves_to_wait_ms(self):
        from lvpy.primitive_resolver import get_resolver
        resolver = get_resolver()
        resolved = resolver.resolve(prim_id=1302)

        assert resolved is not None
        assert resolved.name == "Wait (ms)"
        # python_code may be a dict (template) or string
        if isinstance(resolved.python_code, dict):
            assert any("time.sleep" in v for v in resolved.python_code.values())
        else:
            assert "time.sleep" in resolved.python_code
        assert "import time" in resolved.imports

    def test_1302_has_correct_terminals(self):
        from lvpy.primitive_resolver import get_resolver
        resolver = get_resolver()
        resolved = resolver.resolve(prim_id=1302)

        inputs = [t for t in resolved.terminals if t.direction == "in"]
        outputs = [t for t in resolved.terminals if t.direction == "out"]

        assert len(inputs) == 1
        assert inputs[0].name == "milliseconds_to_wait"
        assert len(outputs) == 1
        assert outputs[0].name == "millisecond_timer_value"


# =============================================================
# Polymorphic variant extraction from XML
# =============================================================


class TestPolyVariantExtraction:
    """polySelector variant names should be extracted from polyIUse nodes."""

    def test_poly_variant_parsed_from_vi(self):
        """In.vi's Create Virtual Channel should resolve to 'Digital Output'."""
        from lvpy.graph import connect
        mg = connect()
        mg.load_vi("samples/DAQmx-Digital-IO/In.vi")

        for op in mg.get_operations("In.vi"):
            if op.name == "DAQmx Create Virtual Channel.vi":
                assert op.poly_variant_name == "Digital Output"
                return
        raise AssertionError("DAQmx Create Virtual Channel.vi not found")

    def test_write_variant_parsed(self):
        """In.vi's Write should have a Digital Bool variant name."""
        from lvpy.graph import connect
        mg = connect()
        mg.load_vi("samples/DAQmx-Digital-IO/In.vi")

        for op in mg.get_operations("In.vi"):
            if op.name == "DAQmx Write.vi":
                assert op.poly_variant_name is not None
                assert "Digital" in op.poly_variant_name
                return
        raise AssertionError("DAQmx Write.vi not found")

    def test_non_poly_nodes_have_no_variant(self):
        """Non-polymorphic nodes should have poly_variant_name=None."""
        from lvpy.graph import connect
        mg = connect()
        mg.load_vi("samples/DAQmx-Digital-IO/In.vi")

        for op in mg.get_operations("In.vi"):
            if op.name == "DAQmx Start Task.vi":
                assert op.poly_variant_name is None
                return
        raise AssertionError("DAQmx Start Task.vi not found")


# =============================================================
# poly_selector_names resolution in vilib resolver
# =============================================================


class TestPolyResolverLookup:
    """Resolver should find variant entries by polySelector name."""

    def test_digital_output_resolves_to_do_line(self):
        from lvpy.vilib_resolver import get_resolver
        resolver = get_resolver()
        entry = resolver.resolve_poly_variant(
            "DAQmx Create Virtual Channel.vi", "Digital Output"
        )
        assert entry is not None
        assert "do_channels.add_do_chan" in entry.python_code

    def test_digital_input_resolves_to_di_line(self):
        from lvpy.vilib_resolver import get_resolver
        resolver = get_resolver()
        entry = resolver.resolve_poly_variant(
            "DAQmx Create Virtual Channel.vi", "Digital Input"
        )
        assert entry is not None
        assert "di_channels.add_di_chan" in entry.python_code

    def test_ai_voltage_resolves(self):
        from lvpy.vilib_resolver import get_resolver
        resolver = get_resolver()
        entry = resolver.resolve_poly_variant(
            "DAQmx Create Virtual Channel.vi", "AI Voltage"
        )
        assert entry is not None
        assert "ai_channels.add_ai_voltage_chan" in entry.python_code

    def test_unknown_selector_returns_none(self):
        from lvpy.vilib_resolver import get_resolver
        resolver = get_resolver()
        entry = resolver.resolve_poly_variant(
            "DAQmx Create Virtual Channel.vi", "Nonexistent Variant"
        )
        assert entry is None


# =============================================================
# End-to-end: In.vi generates correct output
# =============================================================


class TestInViEndToEnd:
    """End-to-end test that In.vi generates correct Python."""

    def _build_in_vi(self):
        from lvpy.codegen.builder import build_module
        from lvpy.graph import connect

        mg = connect()
        mg.load_vi("samples/DAQmx-Digital-IO/In.vi")
        ctx = mg.get_vi_context("In.vi")
        return build_module(ctx, "In.vi", graph=mg)

    def test_generates_without_error(self):
        code = self._build_in_vi()
        assert "def in_():" in code

    def test_uses_do_channel(self):
        code = self._build_in_vi()
        assert "do_channels.add_do_chan" in code
        assert "ai_channels" not in code

    def test_has_time_sleep(self):
        code = self._build_in_vi()
        assert "import time" in code
        assert "time.sleep(500 / 1000)" in code

    def test_boolean_values(self):
        code = self._build_in_vi()
        assert ".write(True)" in code
        assert ".write(False)" in code
        assert ".write(None)" not in code
        assert ".write('True')" not in code

    def test_no_path_wrapping(self):
        code = self._build_in_vi()
        assert "Path(" not in code
        assert "'Dev1/port0/line0'" in code
