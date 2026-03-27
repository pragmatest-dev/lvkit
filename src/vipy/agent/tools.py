"""Tools for agentic VI conversion.

Provides tools that an LLM agent can use to gather information
and iterate on code generation.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..graph import VIGraph
    from .state import ConversionState


@dataclass
class ToolResult:
    """Result from executing a tool."""

    success: bool
    output: str
    error: str | None = None


class AgentTools:
    """Tools available to the LLM agent for code generation.

    These tools let the agent:
    - Read already-converted SubVI code to understand how to call them
    - Run mypy to check for type errors
    - Query the graph for additional VI information
    - Search for primitive implementations
    """

    def __init__(
        self,
        state: ConversionState,
        graph: VIGraph,
        output_dir: Path,
        primitives_dir: Path | None = None,
    ) -> None:
        self.state = state
        self.graph = graph
        self.output_dir = output_dir
        self.primitives_dir = primitives_dir or output_dir / "primitives"

    def get_tool_descriptions(self) -> list[dict[str, Any]]:
        """Get tool descriptions for the LLM prompt."""
        return [
            {
                "name": "read_subvi",
                "description": (
                    "Read the Python code of an already-converted SubVI"
                    " to understand how to call it"
                ),
                "parameters": {
                    "vi_name": (
                        "Name of the VI (e.g., 'Create Dir if Non-Existant__ogtk.vi')"
                    )
                }
            },
            {
                "name": "check_types",
                "description": "Run mypy type checker on Python code and get errors",
                "parameters": {
                    "code": "Python code to check"
                }
            },
            {
                "name": "read_primitive",
                "description": "Read the implementation of a primitive function",
                "parameters": {
                    "function_name": (
                        "Name of the primitive function (e.g., 'build_path')"
                    )
                }
            },
            {
                "name": "query_dataflow",
                "description": (
                    "Get detailed data flow for a VI showing how data"
                    " moves between operations"
                ),
                "parameters": {
                    "vi_name": "Name of the VI to query"
                }
            },
            {
                "name": "submit_code",
                "description": (
                    "Submit final Python code for validation."
                    " Use this when you're confident the code is correct."
                ),
                "parameters": {
                    "code": "Final Python code"
                }
            },
        ]

    def execute(self, tool_name: str, **params: Any) -> ToolResult:
        """Execute a tool and return the result."""
        try:
            if tool_name == "read_subvi":
                return self._read_subvi(params.get("vi_name", ""))
            elif tool_name == "check_types":
                return self._check_types(params.get("code", ""))
            elif tool_name == "read_primitive":
                return self._read_primitive(params.get("function_name", ""))
            elif tool_name == "query_dataflow":
                return self._query_dataflow(params.get("vi_name", ""))
            elif tool_name == "submit_code":
                return ToolResult(success=True, output=params.get("code", ""))
            else:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Unknown tool: {tool_name}"
                )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def _read_subvi(self, vi_name: str) -> ToolResult:
        """Read the code of a converted SubVI."""
        if not vi_name:
            return ToolResult(success=False, output="", error="vi_name is required")

        module = self.state.get_module(vi_name)
        if not module:
            return ToolResult(
                success=False,
                output="",
                error=f"SubVI '{vi_name}' has not been converted yet"
            )

        try:
            code = module.output_path.read_text()
            return ToolResult(success=True, output=code)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def _check_types(self, code: str) -> ToolResult:
        """Run mypy on code and return errors."""
        if not code:
            return ToolResult(success=False, output="", error="code is required")

        # Write to temp file
        temp_path = self.output_dir / "_temp_agent_check.py"
        try:
            temp_path.write_text(code)

            result = subprocess.run(
                [
                    sys.executable, "-m", "mypy",
                    "--ignore-missing-imports",
                    "--no-error-summary",
                    "--no-color",
                    str(temp_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                return ToolResult(success=True, output="No type errors found!")
            else:
                return ToolResult(success=True, output=result.stdout)
        finally:
            temp_path.unlink(missing_ok=True)

    def _read_primitive(self, function_name: str) -> ToolResult:
        """Read a primitive implementation."""
        if not function_name:
            return ToolResult(
                success=False, output="", error="function_name is required"
            )

        # Look for the primitive in the primitives package
        primitives_init = self.primitives_dir / "__init__.py"
        if not primitives_init.exists():
            return ToolResult(
                success=False,
                output="",
                error="Primitives package not found"
            )

        # Read and search for the function
        code = primitives_init.read_text()

        # Find the function definition
        lines = code.split("\n")
        in_function = False
        function_lines = []
        indent_level = 0

        for line in lines:
            if f"def {function_name}(" in line:
                in_function = True
                indent_level = len(line) - len(line.lstrip())
                function_lines.append(line)
            elif in_function:
                if line.strip() == "":
                    function_lines.append(line)
                elif (
                    line.startswith(" " * (indent_level + 1))
                    or line.strip().startswith("#")
                ):
                    function_lines.append(line)
                elif line.strip() and not line.startswith(" " * (indent_level + 1)):
                    # End of function
                    break

        if function_lines:
            return ToolResult(success=True, output="\n".join(function_lines))
        else:
            return ToolResult(
                success=False,
                output="",
                error=f"Function '{function_name}' not found in primitives"
            )

    def _query_dataflow(self, vi_name: str) -> ToolResult:
        """Query detailed data flow for a VI."""
        if not vi_name:
            return ToolResult(success=False, output="", error="vi_name is required")

        try:
            context = self.graph.get_vi_context(vi_name)
            data_flow = context.data_flow

            if not data_flow:
                return ToolResult(success=True, output="No data flow connections found")

            # Format data flow as readable text
            lines = ["Data flow connections:", ""]
            for flow in data_flow:
                from_name = flow.get("from_parent_name", "?")
                from_labels = flow.get("from_parent_labels", [])
                to_name = flow.get("to_parent_name", "?")
                to_labels = flow.get("to_parent_labels", [])

                from_type = next(
                    (lbl for lbl in from_labels if lbl not in ("Input", "Output")), ""
                )
                to_type = next(
                    (lbl for lbl in to_labels if lbl not in ("Input", "Output")), ""
                )

                lines.append(f"  {from_name} ({from_type}) → {to_name} ({to_type})")

            return ToolResult(success=True, output="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


def format_tools_for_prompt(tools: AgentTools) -> str:
    """Format tool descriptions for inclusion in LLM prompt."""
    descriptions = tools.get_tool_descriptions()

    lines = ["## Available Tools", ""]
    lines.append(
        "You can use these tools by responding with a tool call in this format:"
    )
    lines.append("```")
    lines.append("TOOL: tool_name")
    lines.append("PARAM: param_name = value")
    lines.append("```")
    lines.append("")
    lines.append("Available tools:")
    lines.append("")

    for tool in descriptions:
        lines.append(f"### {tool['name']}")
        lines.append(f"{tool['description']}")
        lines.append("Parameters:")
        for param, desc in tool['parameters'].items():
            lines.append(f"  - {param}: {desc}")
        lines.append("")

    lines.append("When you're done, use the submit_code tool with your final code.")

    return "\n".join(lines)


def parse_tool_call(response: str) -> tuple[str | None, dict[str, str]]:
    """Parse a tool call from LLM response.

    Returns (tool_name, params) or (None, {}) if no tool call found.
    """
    lines = response.strip().split("\n")
    tool_name = None
    params: dict[str, str] = {}

    for line in lines:
        line = line.strip()
        if line.startswith("TOOL:"):
            tool_name = line[5:].strip()
        elif line.startswith("PARAM:"):
            param_part = line[6:].strip()
            if "=" in param_part:
                key, value = param_part.split("=", 1)
                params[key.strip()] = value.strip()

    return tool_name, params
