"""Rich feedback strategy: Auto-include relevant code on errors.

On validation failure, automatically include:
- SubVI source code for "not defined" errors
- Primitive implementations for missing primitives
- Additional context based on error type

No tool parsing needed - we proactively provide what the LLM needs.
"""

from __future__ import annotations

import re
import time
from typing import Any

from ...llm import generate_code
from ..context_builder import ContextBuilder
from . import register_strategy
from .base import ConversionStrategy, StrategyResult


@register_strategy
class RichFeedbackStrategy(ConversionStrategy):
    """Enhanced error feedback with automatic context inclusion."""

    name = "rich_feedback"
    description = "Auto-include SubVI/primitive code when errors reference them"

    def convert(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code with rich error context."""
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
        expected_output_count = len(vi_context.get("outputs", []))
        original_context = context

        code = ""
        errors: list[str] = []
        enrichments: list[str] = []

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
                    metadata={
                        "strategy": self.name,
                        "enrichments": enrichments,
                    },
                )

            # Build enriched error context
            errors = [e.message for e in validation.errors]
            enriched_context = self._build_enriched_context(
                code, errors, original_context, converted_deps, primitive_names
            )

            if enriched_context != original_context:
                enrichments.append(f"attempt_{attempt}")

            context = enriched_context

        # Max attempts exceeded
        return StrategyResult(
            success=False,
            code=code,
            attempts=self.max_attempts,
            time_seconds=time.time() - start_time,
            errors=errors,
            metadata={
                "strategy": self.name,
                "enrichments": enrichments,
            },
        )

    def _build_enriched_context(
        self,
        code: str,
        errors: list[str],
        original_context: str,
        converted_deps: dict[str, Any],
        primitive_names: list[str],
    ) -> str:
        """Build error context with additional helpful information."""
        enrichments: list[str] = []

        # Check for "not defined" errors and include relevant code
        for error in errors:
            # Look for undefined name errors
            match = re.search(r'Name "(\w+)" is not defined', error)
            if match:
                name = match.group(1)

                # Check if it's a SubVI
                for dep_name, dep_info in converted_deps.items():
                    func_name = getattr(dep_info, 'function_name', '')
                    if func_name == name:
                        # Include the SubVI code
                        subvi_code = self._read_subvi_code(dep_info)
                        if subvi_code:
                            enrichments.append(f"""
## SubVI Implementation: {dep_name}

The function `{name}` is defined in this already-converted SubVI:

```python
{subvi_code}
```

Make sure to import it: `from {dep_info.module_name} import {name}`
""")
                        break

                # Check if it's a primitive
                if name in primitive_names:
                    enrichments.append(f"""
## Primitive: {name}

The function `{name}` is available from primitives.
Import it: `from primitives import {name}`
""")

            # Look for import errors
            if "Cannot resolve import" in error or "Cannot resolve: from" in error:
                enrichments.append("""
## Import Help

Standard imports available:
- `from pathlib import Path`
- `from typing import Any`
- `from primitives import <function_name>`
- `from <subvi_module> import <function_name>`
""")

        # Build final context
        error_text = "\n".join(f"- {e}" for e in errors)
        enrichment_text = "\n".join(enrichments)

        return f"""{original_context}

---

## Previous Attempt Failed

Your code had these errors:
{error_text}

{enrichment_text}

Please fix the errors. Output ONLY the corrected Python code.
"""

    def _read_subvi_code(self, dep_info: Any) -> str | None:
        """Try to read SubVI source code."""
        try:
            if hasattr(dep_info, 'module_name'):
                path = self.output_dir / f"{dep_info.module_name}.py"
                if path.exists():
                    return path.read_text()
        except Exception:
            pass
        return None
