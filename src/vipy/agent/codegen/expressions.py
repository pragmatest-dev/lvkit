"""Expression builder for primitives and SubVI calls.

Generates Python expressions from operation data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dataflow import DataFlowTracer


@dataclass
class Expression:
    """A generated expression."""
    code: str
    output_vars: list[str]
    needs_import: list[str] = None  # Additional imports needed
    pre_statements: list[str] = None  # Statements to emit before the assignment

    def __post_init__(self):
        if self.needs_import is None:
            self.needs_import = []
        if self.pre_statements is None:
            self.pre_statements = []


class ExpressionBuilder:
    """Builds Python expressions from VI operations.

    Handles primitive dict hints, input substitution, and
    multi-output unpacking.
    """

    def __init__(self, tracer: DataFlowTracer):
        """Initialize with data flow tracer.

        Args:
            tracer: DataFlowTracer for resolving inputs
        """
        self.tracer = tracer

    def build_primitive(
        self,
        python_hint: str | dict[str, str],
        input_values: list[str],
        input_names: list[str],
        wired_outputs: list[tuple[int, str, str]],  # (index, term_id, output_name)
    ) -> Expression:
        """Build expression for a primitive.

        Args:
            python_hint: Python code or {output_name: expr} dict
            input_values: Values for each input (in order)
            input_names: Terminal names for each input (in order)
            wired_outputs: List of (index, terminal_id, output_name) for wired outputs

        Returns:
            Expression with code and output variable names
        """
        # Build input substitution map
        input_map = {}
        for name, value in zip(input_names, input_values):
            if name:
                key = self._to_var_name(name)
                input_map[key] = value
                input_map[name.lower().replace(" ", "_")] = value

        if isinstance(python_hint, dict):
            return self._build_dict_hint(python_hint, input_map, wired_outputs)
        else:
            return self._build_string_hint(python_hint, input_map, wired_outputs)

    def _build_dict_hint(
        self,
        hint_dict: dict[str, str],
        input_map: dict[str, str],
        wired_outputs: list[tuple[int, str, str]],
    ) -> Expression:
        """Build expression from dict-format hint.

        Dict format: {output_name: expression, "_body": optional side effect}
        """
        expressions = []
        output_vars = []

        for idx, term_id, output_name in wired_outputs:
            # Look up expression for this output
            expr = hint_dict.get(output_name)
            if not expr:
                # Try normalized name
                # Strip trailing underscores - hint keys use them to avoid
                # Python keywords (e.g., "is_" for terminal "is?")
                for key in hint_dict:
                    if key != "_body":
                        normalized_key = self._to_var_name(key).rstrip("_")
                        normalized_output = output_name.rstrip("_")
                        if normalized_key == normalized_output:
                            expr = hint_dict[key]
                            break

            if expr:
                substituted = self._substitute(expr, input_map)
                expressions.append(substituted)
                output_vars.append(output_name)
                # Register in tracer
                self.tracer.register_variable(term_id, output_name)
            else:
                # No hint for this output - use None placeholder
                # This happens when primitive has more outputs than hints
                expressions.append("None  # TODO: no hint for this output")
                output_vars.append(output_name)
                self.tracer.register_variable(term_id, output_name)

        # Handle _body (side effect) - emit as separate statement before outputs
        pre_statements = []
        body = hint_dict.get("_body")
        if body:
            pre_statements.append(self._substitute(body, input_map))

        # Build final expression
        if len(expressions) == 0:
            code = "pass" if not body else ""
        elif len(expressions) == 1:
            code = expressions[0]
        else:
            code = ", ".join(expressions)

        return Expression(
            code=code, output_vars=output_vars, pre_statements=pre_statements
        )

    def _build_string_hint(
        self,
        hint: str,
        input_map: dict[str, str],
        wired_outputs: list[tuple[int, str, str]],
    ) -> Expression:
        """Build expression from string-format hint."""
        # Strip assignment if present
        code = hint
        if "=" in code and not any(op in code for op in ["==", "!=", "<=", ">="]):
            eq_pos = code.find("=")
            if eq_pos > 0 and code[eq_pos-1] not in "!<>" and code[eq_pos+1] != "=":
                code = code[eq_pos + 1:].strip()

        # Strip trailing comment
        if "#" in code:
            code = code[:code.find("#")].strip()

        # Substitute inputs
        code = self._substitute(code, input_map)

        # Output vars from wired outputs
        output_vars = [name for idx, term_id, name in wired_outputs]

        # Register outputs in tracer
        for idx, term_id, name in wired_outputs:
            self.tracer.register_variable(term_id, name)

        return Expression(code=code, output_vars=output_vars)

    def build_subvi_call(
        self,
        function_name: str,
        input_values: list[str],
        result_var: str,
    ) -> Expression:
        """Build expression for a SubVI call.

        Args:
            function_name: The Python function name
            input_values: Input argument values
            result_var: Variable name for the result

        Returns:
            Expression with call code
        """
        args = ", ".join(input_values)
        code = f"{function_name}({args})"
        return Expression(code=code, output_vars=[result_var])

    def _substitute(self, template: str, input_map: dict[str, str]) -> str:
        """Substitute input variables in template."""
        result = template
        # Sort by length (longest first) to avoid partial replacements
        for name, value in sorted(input_map.items(), key=lambda x: -len(x[0])):
            if name:
                pattern = r'\b' + re.escape(name) + r'\b'
                result = re.sub(pattern, value, result, flags=re.IGNORECASE)
        return result

    def _to_var_name(self, name: str) -> str:
        """Convert terminal name to Python variable name."""
        if not name:
            return ""
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "var_" + result
        return result
