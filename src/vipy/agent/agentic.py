"""Agentic conversion loop with tool use.

This module provides an enhanced conversion agent that can use tools
to gather information and iterate on code generation. Use this for
complex VIs where single-shot generation fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..llm import LLMConfig, generate_code
from .context import ContextBuilder
from .tools import AgentTools, format_tools_for_prompt, parse_tool_call
from .validator import CodeValidator, ValidatorConfig

if TYPE_CHECKING:
    from .state import ConversionState
    from ..graph import VIGraph


@dataclass
class AgenticConfig:
    """Configuration for agentic conversion."""

    output_dir: Path
    max_iterations: int = 10  # Max tool use iterations
    llm_config: LLMConfig | None = None


@dataclass
class AgenticResult:
    """Result from agentic conversion."""

    success: bool
    code: str
    iterations: int
    tool_calls: list[str]
    errors: list[str]


class AgenticConverter:
    """Agentic VI converter with tool use.

    Unlike the standard ConversionAgent which uses single-shot generation
    with retry, this converter gives the LLM tools to:

    1. Read already-converted SubVI code
    2. Run mypy to check types
    3. Read primitive implementations
    4. Query the VI graph for more context

    The LLM can iterate using these tools until it's confident in its code,
    then submits the final result.

    Usage:
        converter = AgenticConverter(state, graph, config)
        result = converter.convert(vi_name, vi_context)
    """

    def __init__(
        self,
        state: ConversionState,
        graph: VIGraph,
        config: AgenticConfig,
    ) -> None:
        self.state = state
        self.graph = graph
        self.config = config
        self.llm_config = config.llm_config or LLMConfig()

        self.tools = AgentTools(
            state=state,
            graph=graph,
            output_dir=config.output_dir,
        )

        self.validator = CodeValidator(
            ValidatorConfig(
                output_dir=config.output_dir,
                check_syntax=True,
                check_imports=True,
                check_types=True,
            )
        )

    def convert(
        self,
        vi_name: str,
        vi_context: dict,
        converted_deps: dict,
        primitive_names: list[str],
        primitive_context: dict,
    ) -> AgenticResult:
        """Convert a VI using agentic tool use.

        Args:
            vi_name: Name of the VI
            vi_context: VI context from graph
            converted_deps: Already-converted SubVI signatures
            primitive_names: Available primitive function names
            primitive_context: Primitive context with hints

        Returns:
            AgenticResult with success/failure and generated code
        """
        # Build initial context (with library-aware imports)
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

        tools_prompt = format_tools_for_prompt(self.tools)

        # Same task, but with tools available
        context = f"""{base_context}

{tools_prompt}

## Additional Help

If you're unsure about something, you can use the tools above to get more information:
- Use `read_subvi` to see how a SubVI is implemented
- Use `check_types` to verify your code before submitting
- Use `read_primitive` to understand a primitive function

When ready, submit your final code with `submit_code`.
"""

        tool_calls: list[str] = []
        errors: list[str] = []
        final_code = ""

        for iteration in range(self.config.max_iterations):
            # Generate response
            response = generate_code(context, self.llm_config)

            # Parse for tool call
            tool_name, params = parse_tool_call(response)

            if tool_name:
                tool_calls.append(f"{tool_name}({params})")

                if tool_name == "submit_code":
                    # Extract code from params or response
                    final_code = params.get("code", "")
                    if not final_code:
                        # Try to extract from markdown block
                        final_code = self._extract_code(response)

                    # Validate
                    expected_subvis = [
                        op["name"]
                        for op in vi_context.get("operations", [])
                        if "SubVI" in op.get("labels", []) and op.get("name")
                    ]

                    validation = self.validator.validate(
                        final_code, vi_name, [], expected_subvis
                    )

                    if validation.is_valid:
                        return AgenticResult(
                            success=True,
                            code=final_code,
                            iterations=iteration + 1,
                            tool_calls=tool_calls,
                            errors=[],
                        )
                    else:
                        # Feed validation errors back
                        errors = [e.message for e in validation.errors]
                        context = f"""Your submitted code has errors:

{chr(10).join(f'- {e}' for e in errors)}

Previous code:
```python
{final_code}
```

Fix these errors and submit again. Use tools if needed.
"""
                else:
                    # Execute tool and add result to context
                    result = self.tools.execute(tool_name, **params)

                    if result.success:
                        context = f"""Tool result for {tool_name}:

{result.output}

Continue with your task. Use more tools or submit_code when ready.
"""
                    else:
                        context = f"""Tool {tool_name} failed: {result.error}

Try a different approach or tool.
"""
            else:
                # No tool call - try to extract code directly
                final_code = self._extract_code(response)
                if final_code:
                    # Treat as implicit submit
                    tool_calls.append("submit_code (implicit)")

                    expected_subvis = [
                        op["name"]
                        for op in vi_context.get("operations", [])
                        if "SubVI" in op.get("labels", []) and op.get("name")
                    ]

                    validation = self.validator.validate(
                        final_code, vi_name, [], expected_subvis
                    )

                    if validation.is_valid:
                        return AgenticResult(
                            success=True,
                            code=final_code,
                            iterations=iteration + 1,
                            tool_calls=tool_calls,
                            errors=[],
                        )
                    else:
                        errors = [e.message for e in validation.errors]
                        context = f"""Your code has errors:

{chr(10).join(f'- {e}' for e in errors)}

Use tools to investigate and fix. Submit when ready.
"""
                else:
                    # Couldn't parse anything useful
                    context = """I couldn't understand your response. Please either:
1. Use a tool with the TOOL:/PARAM: format
2. Submit code with submit_code tool
"""

        # Max iterations reached
        return AgenticResult(
            success=False,
            code=final_code,
            iterations=self.config.max_iterations,
            tool_calls=tool_calls,
            errors=errors or ["Max iterations reached"],
        )

    def _extract_code(self, response: str) -> str:
        """Extract Python code from LLM response."""
        if "```python" in response:
            start = response.find("```python") + 9
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        if "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        return response.strip()

    def _get_library_name(self, vi_name: str) -> str | None:
        """Extract library name from qualified VI name."""
        if ":" not in vi_name:
            return None
        library = vi_name.split(":", 1)[0]
        library = library.replace(".lvlib", "").replace(".lvclass", "")
        result = library.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or None