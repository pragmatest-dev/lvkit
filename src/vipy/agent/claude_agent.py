"""Claude API agent for VI-to-Python conversion.

Calls Anthropic's Claude API to generate Python code from VI context.
Used by the /convert skill for each VI in dependency order.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..types import TypeInfo


class TypeInfoEncoder(json.JSONEncoder):
    """JSON encoder that handles TypeInfo objects."""

    def default(self, obj):
        if isinstance(obj, TypeInfo):
            return obj.to_dict()
        return super().default(obj)

try:
    import anthropic
except ImportError:
    anthropic = None


@dataclass
class ConversionResult:
    """Result from Claude agent conversion."""

    success: bool
    code: str
    time_seconds: float
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


SYSTEM_PROMPT = """You are a code generator that converts LabVIEW VIs to Python functions.

You receive structured context from a Neo4j graph database and output Python code.

## Rules

1. Output ONLY valid Python code - no explanations, no markdown
2. Use exact function names from SubVI signatures provided
3. Use exact import statements provided
4. Follow data_flow for execution order
5. Use constants with their actual values
6. Type annotate all parameters and return type
7. Include `from __future__ import annotations` at top
8. Include `from typing import Any` if using Any
9. Include `from pathlib import Path` if using Path
"""


def build_prompt(
    vi_name: str,
    vi_context: dict[str, Any],
    subvi_imports: list[str],
    primitive_imports: list[str],
) -> str:
    """Build the prompt for Claude from VI context."""

    # Clean VI name for function
    func_name = vi_name.replace(".vi", "").replace(".VI", "")
    if ":" in func_name:
        func_name = func_name.split(":")[-1]
    func_name = func_name.lower().replace(" ", "_").replace("-", "_")
    func_name = "".join(c for c in func_name if c.isalnum() or c == "_")

    # Build imports section
    imports = []
    if subvi_imports:
        imports.extend(subvi_imports)
    if primitive_imports:
        imports.extend(primitive_imports)

    imports_text = "\n".join(imports) if imports else "# No SubVI or primitive imports needed"

    prompt = f"""Convert this LabVIEW VI to a Python function.

## VI: {vi_name}
## Function name: {func_name}

## Available Imports
{imports_text}

## VI Context (from graph database)
```json
{json.dumps(vi_context, indent=2, cls=TypeInfoEncoder)}
```

## Instructions
- Create function `{func_name}` with typed parameters from `inputs`
- Return values from `outputs`
- Follow `data_flow` for execution order
- Use `operations` to determine what to call
- Use constant values from `constants`

Output ONLY the Python code."""

    return prompt


def convert_vi(
    vi_name: str,
    vi_context: dict[str, Any],
    subvi_imports: list[str],
    primitive_imports: list[str],
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
) -> ConversionResult:
    """Convert a VI to Python using Claude API.

    Args:
        vi_name: Name of the VI
        vi_context: Context from graph.get_vi_context()
        subvi_imports: List of SubVI import statements
        primitive_imports: List of primitive import statements
        model: Claude model to use
        max_tokens: Max tokens for response

    Returns:
        ConversionResult with generated code
    """
    if anthropic is None:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=0,
            model=model,
            error="anthropic package not installed. Run: pip install anthropic",
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=0,
            model=model,
            error="ANTHROPIC_API_KEY environment variable not set",
        )

    client = anthropic.Anthropic(api_key=api_key)

    prompt = build_prompt(vi_name, vi_context, subvi_imports, primitive_imports)

    start_time = time.time()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        elapsed = time.time() - start_time

        # Extract code from response
        code = response.content[0].text

        # Strip markdown code blocks if present
        if "```python" in code:
            start = code.find("```python") + 9
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        elif "```" in code:
            start = code.find("```") + 3
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()

        return ConversionResult(
            success=True,
            code=code.strip(),
            time_seconds=elapsed,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    except Exception as e:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=time.time() - start_time,
            model=model,
            error=str(e),
        )


def convert_with_retry(
    vi_name: str,
    vi_context: dict[str, Any],
    subvi_imports: list[str],
    primitive_imports: list[str],
    validator,
    max_attempts: int = 3,
    model: str = "claude-sonnet-4-20250514",
) -> ConversionResult:
    """Convert VI with validation retry loop.

    Args:
        vi_name: Name of the VI
        vi_context: Context from graph
        subvi_imports: SubVI import statements
        primitive_imports: Primitive import statements
        validator: CodeValidator instance
        max_attempts: Max retry attempts
        model: Claude model to use

    Returns:
        ConversionResult with final code
    """
    total_time = 0
    total_input_tokens = 0
    total_output_tokens = 0

    # Build expected SubVIs for completeness check
    expected_subvis = [
        op["name"]
        for op in vi_context.get("operations", [])
        if "SubVI" in op.get("labels", []) and op.get("name")
    ]

    for attempt in range(1, max_attempts + 1):
        result = convert_vi(
            vi_name, vi_context, subvi_imports, primitive_imports, model
        )

        total_time += result.time_seconds
        total_input_tokens += result.input_tokens
        total_output_tokens += result.output_tokens

        if not result.success:
            return result

        # Validate
        validation = validator.validate(result.code, vi_name, [], expected_subvis)

        if validation.is_valid:
            return ConversionResult(
                success=True,
                code=result.code,
                time_seconds=total_time,
                model=model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )

        # Build error context for retry
        errors = [e.message for e in validation.errors]
        error_text = "\n".join(f"- {e}" for e in errors)

        # Update context with errors for next attempt
        vi_context = {
            **vi_context,
            "_previous_code": result.code,
            "_errors": errors,
            "_attempt": attempt,
        }

    # Max attempts exceeded
    return ConversionResult(
        success=False,
        code=result.code,
        time_seconds=total_time,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        error=f"Validation failed after {max_attempts} attempts: {errors[0] if errors else 'unknown'}",
    )
