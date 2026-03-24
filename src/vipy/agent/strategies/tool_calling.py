"""Tool calling strategy: LLM can call tools to gather information.

The LLM has access to tools:
- read_subvi: Read already-converted SubVI code
- check_types: Run mypy on code
- read_primitive: Read primitive implementation
- query_dataflow: Get detailed data flow

LLM decides when it has enough info and submits final code.
"""

from __future__ import annotations

import time
from typing import Any

from vipy.graph_types import VIContext

from ...llm import generate_code
from ..context_builder import ContextBuilder
from ..tools import AgentTools, format_tools_for_prompt, parse_tool_call
from . import register_strategy
from .base import ConversionStrategy, StrategyResult


@register_strategy
class ToolCallingStrategy(ConversionStrategy):
    """LLM-driven tool use for information gathering."""

    name = "tool_calling"
    description = "LLM can call tools to gather information before generating code"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.max_iterations = kwargs.get("max_iterations", 10)

    def convert(
        self,
        vi_name: str,
        vi_context: VIContext,
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code with tool-assisted information gathering."""
        start_time = time.time()

        # Initialize tools (need state for read_subvi)
        # For now, create a minimal tools instance
        from ..state import ConversionState
        state = ConversionState()

        # Mark converted deps in state
        for dep_name, dep_info in converted_deps.items():
            if hasattr(dep_info, 'module_name'):
                # Create a mock output path for the state
                mock_path = self.output_dir / f"{dep_info.module_name}.py"
                if mock_path.exists():
                    state.mark_converted(dep_name, mock_path)

        tools = AgentTools(
            state=state,
            graph=None,  # Not needed for most tools
            output_dir=self.output_dir,
        )

        # Build initial context with tool descriptions (library-aware imports)
        from_library = self._get_library_name(vi_name)
        base_context = ContextBuilder.build_vi_context(
            vi_context=vi_context,
            vi_name=vi_name,
            converted_deps=converted_deps,
            shared_types=[],
            primitives_available=primitive_names,
            primitive_context=primitive_context,
            from_library=from_library,
        )

        tools_prompt = format_tools_for_prompt(tools)
        context = f"""{base_context}

{tools_prompt}

## Additional Help

If you're unsure about something, use the tools to get more information:
- Use `read_subvi` to see how a SubVI is implemented
- Use `check_types` to verify your code before submitting
- Use `read_primitive` to understand a primitive function

When ready, submit your final code with `submit_code`.
"""

        expected_subvis = self._get_expected_subvis(vi_context)
        expected_output_count = len(vi_context.outputs)
        tool_calls: list[str] = []
        code = ""
        errors: list[str] = []

        for iteration in range(self.max_iterations):
            # Generate response
            response = generate_code(context, self.llm_config)

            # Parse for tool call
            tool_name, params = parse_tool_call(response)

            if tool_name:
                tool_calls.append(f"{tool_name}({params})")

                if tool_name == "submit_code":
                    # Extract code
                    code = params.get("code", "") or self._extract_code(response)

                    # Validate
                    validation = self.validator.validate(
                        code, vi_name, [], expected_subvis,
                        expected_output_count=expected_output_count,
                    )

                    if validation.is_valid:
                        return StrategyResult(
                            success=True,
                            code=code,
                            attempts=iteration + 1,
                            time_seconds=time.time() - start_time,
                            metadata={
                                "strategy": self.name,
                                "tool_calls": tool_calls,
                            },
                        )
                    else:
                        errors = [e.message for e in validation.errors]
                        context = f"""Your submitted code has errors:

{chr(10).join(f'- {e}' for e in errors)}

Fix these errors and submit again. Use tools if needed.
"""
                else:
                    # Execute tool
                    result = tools.execute(tool_name, **params)
                    if result.success:
                        context = f"""Tool result for {tool_name}:

{result.output}

Continue with your task.
"""
                    else:
                        context = f"""Tool {tool_name} failed: {result.error}

Try a different approach.
"""
            else:
                # No tool call - try to extract code directly
                code = self._extract_code(response)
                if code:
                    tool_calls.append("submit_code (implicit)")

                    validation = self.validator.validate(
                        code, vi_name, [], expected_subvis,
                        expected_output_count=expected_output_count,
                    )

                    if validation.is_valid:
                        return StrategyResult(
                            success=True,
                            code=code,
                            attempts=iteration + 1,
                            time_seconds=time.time() - start_time,
                            metadata={
                                "strategy": self.name,
                                "tool_calls": tool_calls,
                            },
                        )
                    else:
                        errors = [e.message for e in validation.errors]
                        context = f"""Your code has errors:

{chr(10).join(f'- {e}' for e in errors)}

Use tools to investigate and fix.
"""
                else:
                    context = """I couldn't understand your response. Please either:
1. Use a tool with TOOL:/PARAM: format
2. Submit code with submit_code tool
"""

        # Max iterations exceeded
        return StrategyResult(
            success=False,
            code=code,
            attempts=self.max_iterations,
            time_seconds=time.time() - start_time,
            errors=errors or ["Max iterations reached"],
            metadata={
                "strategy": self.name,
                "tool_calls": tool_calls,
            },
        )
