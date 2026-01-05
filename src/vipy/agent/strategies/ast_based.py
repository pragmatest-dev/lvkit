"""Baseline strategy: Deterministic AST-based code generation from VI graph.

This is the default strategy. It:
1. Generates syntactically valid Python directly from VI graph using AST
2. Validates (syntax, imports, types, completeness)
3. On failure, optionally falls back to LLM refinement

No LLM required for basic conversion - fully deterministic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...llm import generate_code
from ..codegen import build_module
from ..context import ContextBuilder
from . import register_strategy
from .base import ConversionStrategy, StrategyResult


@register_strategy
class BaselineStrategy(ConversionStrategy):
    """AST-based deterministic code generation (default).

    Generates syntactically valid Python code directly from the VI graph
    using AST nodes. No LLM required. Code is guaranteed valid Python syntax.
    """

    name = "baseline"
    description = "Deterministic AST-based code generation (default)"

    def convert(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code using AST-based builder."""
        start_time = time.time()

        # Generate code using AST builder
        try:
            code = build_module(vi_context, vi_name)
        except Exception as e:
            # Fall back to error result if AST generation fails
            return StrategyResult(
                success=False,
                code="",
                attempts=1,
                time_seconds=time.time() - start_time,
                errors=[f"AST generation failed: {e}"],
                metadata={"strategy": self.name},
            )

        # Save generated code for review
        self._save_generated(vi_name, code)

        # Validate
        expected_subvis = self._get_expected_subvis(vi_context)
        expected_output_count = len(vi_context.get("outputs", []))

        validation = self.validator.validate(
            code, vi_name, [], expected_subvis,
            expected_output_count=expected_output_count,
        )

        if validation.is_valid:
            return StrategyResult(
                success=True,
                code=code,
                attempts=1,
                time_seconds=time.time() - start_time,
                metadata={"strategy": self.name, "refined": False},
            )

        # If validation failed, try LLM refinement
        errors = [e.message for e in validation.errors]

        # Check if we have placeholders that need LLM help
        has_placeholders = "???" in code or "PRIMITIVE_" in code

        if has_placeholders or errors:
            refined_result = self._refine_with_llm(
                vi_name, vi_context, code, errors, converted_deps,
                primitive_names, primitive_context, start_time,
            )
            if refined_result.success:
                return refined_result

        # Return best attempt
        return StrategyResult(
            success=False,
            code=code,
            attempts=1,
            time_seconds=time.time() - start_time,
            errors=errors,
            metadata={"strategy": self.name, "refined": False},
        )

    def _refine_with_llm(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        generated_code: str,
        errors: list[str],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
        start_time: float,
    ) -> StrategyResult:
        """Refine generated code using LLM."""
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

        context = self._build_refinement_prompt(base_context, generated_code, errors)

        expected_subvis = self._get_expected_subvis(vi_context)
        expected_output_count = len(vi_context.get("outputs", []))

        for attempt in range(2, self.max_attempts + 1):
            response = generate_code(context, self.llm_config)
            code = self._extract_code(response)

            validation = self.validator.validate(
                code, vi_name, [], expected_subvis,
                expected_output_count=expected_output_count,
            )

            if validation.is_valid:
                return StrategyResult(
                    success=True,
                    code=code,
                    attempts=attempt,
                    time_seconds=time.time() - start_time,
                    metadata={"strategy": self.name, "refined": True},
                )

            errors = [e.message for e in validation.errors]
            context = ContextBuilder.build_error_context(
                code, validation.errors, base_context
            )

        return StrategyResult(
            success=False,
            code=code,
            attempts=self.max_attempts,
            time_seconds=time.time() - start_time,
            errors=errors,
            metadata={"strategy": self.name, "refined": True},
        )

    def _save_generated(self, vi_name: str, code: str) -> None:
        """Save generated code for review."""
        output_dir = Path("outputs/ast_generated")
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = vi_name.replace(".vi", "").replace(".VI", "")
        base_name = base_name.lower().replace(" ", "_").replace("-", "_")
        base_name = "".join(c for c in base_name if c.isalnum() or c == "_")

        output_path = output_dir / f"{base_name}.py"
        output_path.write_text(code)

    def _build_refinement_prompt(
        self, base_context: str, generated_code: str, errors: list[str]
    ) -> str:
        """Build prompt for LLM refinement."""
        error_text = "\n".join(f"- {e}" for e in errors) if errors else "No errors"

        return f"""{base_context}

## Generated Code

Code generated from VI structure. Syntactically valid but may need fixes:

```python
{generated_code}
```

## Issues to Fix

{error_text}

## Your Task

Fix the issues in the generated code:
1. **Replace `???` placeholders** with correct variable names
2. **Replace `PRIMITIVE_xxx` calls** with correct Python implementations
3. **Fix variable references** that may not be defined
4. **Ensure correct data flow** between operations

Output the COMPLETE corrected Python code.
"""
