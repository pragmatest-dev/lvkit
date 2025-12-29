"""Baseline strategy: Single-shot generation with error retry.

This is the original approach:
1. Build context from VI graph
2. Generate code with LLM
3. Validate (syntax, imports, types, completeness)
4. On failure, feed errors back and retry
"""

from __future__ import annotations

import time
from typing import Any

from ...llm import generate_code
from ..context import ContextBuilder
from . import register_strategy
from .base import ConversionStrategy, StrategyResult


@register_strategy
class BaselineStrategy(ConversionStrategy):
    """Single-shot code generation with error-based retry."""

    name = "baseline"
    description = "Single-shot generation with error retry (current approach)"

    def convert(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code with retry on validation errors."""
        start_time = time.time()

        # Build initial context (with library-aware imports)
        from_library = self._get_library_name(vi_name)
        context = ContextBuilder.build_vi_context(
            vi_context=vi_context,
            vi_name=vi_name,
            converted_deps=converted_deps,
            shared_types=[],
            primitives_available=primitive_names,
            primitive_context=primitive_context,
            from_library=from_library,
        )

        expected_subvis = self._get_expected_subvis(vi_context)
        original_context = context

        code = ""
        errors: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            # Generate code
            response = generate_code(context, self.llm_config)
            code = self._extract_code(response)

            # Validate
            validation = self.validator.validate(
                code, vi_name, [], expected_subvis
            )

            if validation.is_valid:
                return StrategyResult(
                    success=True,
                    code=code,
                    attempts=attempt,
                    time_seconds=time.time() - start_time,
                    metadata={"strategy": self.name},
                )

            # Build error context for retry
            errors = [e.message for e in validation.errors]
            context = ContextBuilder.build_error_context(
                code, validation.errors, original_context
            )

        # Max attempts exceeded
        return StrategyResult(
            success=False,
            code=code,
            attempts=self.max_attempts,
            time_seconds=time.time() - start_time,
            errors=errors,
            metadata={"strategy": self.name},
        )
