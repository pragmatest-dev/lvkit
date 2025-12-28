"""Two-phase strategy: Plan first, then generate code.

Phase 1: Ask LLM to describe the data flow step by step
Phase 2: Ask LLM to write Python implementing its plan

This encourages the LLM to reason about the problem before coding.
"""

from __future__ import annotations

import time
from typing import Any

from ...llm import generate_code
from ..context import ContextBuilder
from . import register_strategy
from .base import ConversionStrategy, StrategyResult


@register_strategy
class TwoPhaseStrategy(ConversionStrategy):
    """Plan-then-code approach for better reasoning."""

    name = "two_phase"
    description = "Phase 1: describe data flow, Phase 2: write code"

    def convert(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code in two phases: plan then implement."""
        start_time = time.time()

        # Build base context
        base_context = ContextBuilder.build_vi_context(
            vi_context=vi_context,
            vi_name=vi_name,
            converted_deps=converted_deps,
            shared_types=[],
            primitives_available=primitive_names,
            primitive_context=primitive_context,
        )

        expected_subvis = self._get_expected_subvis(vi_context)

        # Phase 1: Generate plan
        plan_prompt = f"""{base_context}

## Phase 1: Planning

Before writing code, describe step by step what this VI does:

1. What are the inputs and their types?
2. What operations are performed and in what order?
3. What SubVIs are called and what do they do?
4. What is the output?

Write a clear, numbered plan. Do NOT write any code yet.
"""

        plan_response = generate_code(plan_prompt, self.llm_config)
        plan = plan_response.strip()

        # Phase 2: Generate code from plan
        code = ""
        errors: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            if attempt == 1:
                code_prompt = f"""{base_context}

## Your Plan

{plan}

## Phase 2: Implementation

Now implement this plan as Python code. Follow your plan exactly.

Requirements:
- Function name should match the VI name
- Include all necessary imports
- Call SubVIs and primitives as described in your plan
- Handle the data flow you identified

Output ONLY the Python code, no explanations.
"""
            else:
                # Retry with errors
                error_text = "\n".join(f"- {e}" for e in errors)
                code_prompt = f"""{base_context}

## Your Plan

{plan}

## Previous Attempt Failed

Your code had these errors:
{error_text}

Please fix the errors while still following your plan.
Output ONLY the corrected Python code.
"""

            response = generate_code(code_prompt, self.llm_config)
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
                    metadata={
                        "strategy": self.name,
                        "plan": plan,
                        "phases": ["plan", "code"],
                    },
                )

            errors = [e.message for e in validation.errors]

        # Max attempts exceeded
        return StrategyResult(
            success=False,
            code=code,
            attempts=self.max_attempts,
            time_seconds=time.time() - start_time,
            errors=errors,
            metadata={
                "strategy": self.name,
                "plan": plan,
                "phases": ["plan", "code"],
            },
        )
