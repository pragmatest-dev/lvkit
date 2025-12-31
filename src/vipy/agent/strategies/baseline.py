"""Baseline strategy: Skeleton-based generation with error retry.

This approach:
1. Generate deterministic skeleton from VI graph (topological order, known primitives)
2. Ask LLM to fix/complete the skeleton (types, wiring, unknowns)
3. Validate (syntax, imports, types, completeness)
4. On failure, feed errors back and retry
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...llm import generate_code
from ..context import ContextBuilder
from ..skeleton import generate_skeleton
from . import register_strategy
from .base import ConversionStrategy, StrategyResult


@register_strategy
class BaselineStrategy(ConversionStrategy):
    """Skeleton-based code generation with error-based retry."""

    name = "baseline"
    description = "Skeleton-based generation with error retry"

    def convert(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code from skeleton with retry on validation errors."""
        start_time = time.time()

        # Generate deterministic skeleton from VI graph
        skeleton = generate_skeleton(vi_context, vi_name, converted_deps)

        # Save skeleton for review
        self._save_skeleton(vi_name, skeleton)

        # Build JSON context for reference (with library-aware imports)
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

        # Build skeleton-based prompt
        context = self._build_skeleton_prompt(base_context, skeleton)

        expected_subvis = self._get_expected_subvis(vi_context)
        expected_output_count = len(vi_context.get("outputs", []))

        code = ""
        errors: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            # Generate code
            response = generate_code(context, self.llm_config)
            code = self._extract_code(response)

            # Validate
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
                    metadata={"strategy": self.name, "skeleton": skeleton},
                )

            # Build error context for retry
            errors = [e.message for e in validation.errors]
            context = ContextBuilder.build_error_context(
                code, validation.errors, self._build_skeleton_prompt(base_context, skeleton)
            )

        # Max attempts exceeded
        return StrategyResult(
            success=False,
            code=code,
            attempts=self.max_attempts,
            time_seconds=time.time() - start_time,
            errors=errors,
            metadata={"strategy": self.name, "skeleton": skeleton},
        )

    def _save_skeleton(self, vi_name: str, skeleton: str) -> None:
        """Save skeleton to outputs/skeletons for review."""
        skeleton_dir = Path("outputs/skeletons")
        skeleton_dir.mkdir(parents=True, exist_ok=True)

        # Convert VI name to filename
        base_name = vi_name.replace(".vi", "").replace(".VI", "")
        base_name = base_name.lower().replace(" ", "_").replace("-", "_")
        base_name = "".join(c for c in base_name if c.isalnum() or c == "_")

        skeleton_path = skeleton_dir / f"{base_name}.skeleton.py"
        skeleton_path.write_text(skeleton)

    def _build_skeleton_prompt(self, base_context: str, skeleton: str) -> str:
        """Build prompt that includes skeleton for LLM to fix/complete."""
        return f"""{base_context}

## Code Skeleton

Here's a starting skeleton generated from the VI structure with operations in data-flow order:

```python
{skeleton}
```

## Your Task

Fix and complete this skeleton:
1. **Fix constant types** - strings that should be ints (e.g., enum values), paths, etc.
2. **Access NamedTuple fields** - SubVI calls return NamedTuples, access `.field_name`
3. **Fix argument order/wiring** - verify inputs match the VI's data flow
4. **Replace `???` placeholders** - fill in correct variable names and values
5. **Implement `PRIMITIVE_xxx` calls** - replace with correct Python code

Output the COMPLETE corrected Python code.
"""
