"""Constraint enforcement strategy: Post-process to fix common issues.

1. Generate code normally
2. Post-process to inject missing imports
3. Post-process to ensure SubVI calls are present
4. Only fail on logic errors that can't be auto-fixed

This reduces the burden on the LLM for mechanical correctness.
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
class ConstraintFixStrategy(ConversionStrategy):
    """Post-process generated code to fix common issues."""

    name = "constraint_fix"
    description = "Post-process to fix missing imports and ensure SubVI calls"

    def convert(
        self,
        vi_name: str,
        vi_context: dict[str, Any],
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code with post-processing fixes."""
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

        code = ""
        errors: list[str] = []
        fixes_applied: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            # Generate code
            response = generate_code(context, self.llm_config)
            code = self._extract_code(response)

            # Apply fixes
            code, fixes = self._apply_fixes(
                code, converted_deps, primitive_names, expected_subvis
            )
            fixes_applied.extend(fixes)

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
                        "fixes_applied": fixes_applied,
                    },
                )

            # Filter out errors we tried to fix
            remaining_errors = [
                e for e in validation.errors
                if not self._is_fixable_error(e.message)
            ]

            if remaining_errors:
                errors = [e.message for e in remaining_errors]
                context = ContextBuilder.build_error_context(
                    code, remaining_errors, context
                )
            else:
                # All errors were "fixed" but validation still failed
                errors = [e.message for e in validation.errors]
                context = ContextBuilder.build_error_context(
                    code, validation.errors, context
                )

        # Max attempts exceeded
        return StrategyResult(
            success=False,
            code=code,
            attempts=self.max_attempts,
            time_seconds=time.time() - start_time,
            errors=errors,
            metadata={
                "strategy": self.name,
                "fixes_applied": fixes_applied,
            },
        )

    def _apply_fixes(
        self,
        code: str,
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        expected_subvis: list[str],
    ) -> tuple[str, list[str]]:
        """Apply automatic fixes to the code."""
        fixes: list[str] = []

        # Fix 1: Ensure future annotations import
        if "from __future__ import annotations" not in code:
            code = "from __future__ import annotations\n" + code
            fixes.append("added future annotations")

        # Fix 2: Add missing SubVI imports
        for dep_name, dep_info in converted_deps.items():
            func_name = getattr(dep_info, 'function_name', '')
            module_name = getattr(dep_info, 'module_name', '')

            if func_name and module_name:
                # Check if function is used but not imported
                if func_name in code and f"import {func_name}" not in code:
                    import_stmt = f"from {module_name} import {func_name}"
                    if import_stmt not in code:
                        # Add import after the first import block
                        code = self._add_import(code, import_stmt)
                        fixes.append(f"added import for {func_name}")

        # Fix 3: Add missing primitive imports
        for prim_name in primitive_names:
            if prim_name in code and f"import {prim_name}" not in code:
                # Check if primitives import exists
                if "from primitives import" in code:
                    # Add to existing import
                    code = self._add_to_primitives_import(code, prim_name)
                    fixes.append(f"added {prim_name} to primitives import")
                else:
                    code = self._add_import(code, f"from primitives import {prim_name}")
                    fixes.append(f"added primitives import for {prim_name}")

        # Fix 4: Add missing standard library imports
        if "Path" in code and "from pathlib import Path" not in code:
            code = self._add_import(code, "from pathlib import Path")
            fixes.append("added pathlib import")

        if "Any" in code and "from typing import Any" not in code:
            code = self._add_import(code, "from typing import Any")
            fixes.append("added typing import")

        # Fix 5: Convert relative imports to absolute imports
        # LLM sometimes generates "from .module import func" instead of "from module import func"
        for dep_name, dep_info in converted_deps.items():
            func_name = getattr(dep_info, 'function_name', '')
            module_name = getattr(dep_info, 'module_name', '')

            if func_name and module_name:
                # Pattern: from .module_name import ... (with leading dot)
                rel_pattern = f"from .{module_name} import"
                abs_import = f"from {module_name} import"

                if rel_pattern in code:
                    code = code.replace(rel_pattern, abs_import)
                    fixes.append(f"fixed absolute import for {module_name}")

        # Fix 6: Fix expected SubVIs that aren't in converted_deps
        # These might be stub VIs that the LLM referenced
        for subvi_name in expected_subvis:
            # Convert SubVI name to module/function name
            func_name = self._to_function_name(subvi_name)

            # Pattern: from .func_name import ... (with leading dot)
            rel_pattern = f"from .{func_name} import"
            abs_import = f"from {func_name} import"

            if rel_pattern in code:
                code = code.replace(rel_pattern, abs_import)
                fixes.append(f"fixed absolute import for {func_name}")

        return code, fixes

    def _to_function_name(self, name: str) -> str:
        """Convert VI name to Python function name."""
        name = name.replace(".vi", "").replace(".VI", "")
        if ":" in name:
            name = name.split(":")[-1]
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "vi_" + result
        return result or "vi_function"

    def _add_import(self, code: str, import_stmt: str) -> str:
        """Add an import statement to code."""
        lines = code.split("\n")

        # Find the last import line
        last_import_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("import ") or line.strip().startswith("from "):
                last_import_idx = i

        if last_import_idx >= 0:
            lines.insert(last_import_idx + 1, import_stmt)
        else:
            # No imports found, add at the beginning (after future imports)
            if lines and lines[0].startswith("from __future__"):
                lines.insert(1, import_stmt)
            else:
                lines.insert(0, import_stmt)

        return "\n".join(lines)

    def _add_to_primitives_import(self, code: str, name: str) -> str:
        """Add a name to existing primitives import."""
        # Find and modify the primitives import line (handles both with/without leading dot)
        pattern = r"from \.?primitives import ([^\n]+)"
        match = re.search(pattern, code)
        if match:
            current_imports = match.group(1)
            if name not in current_imports:
                new_imports = f"{current_imports}, {name}"
                code = code.replace(match.group(0), f"from primitives import {new_imports}")
        return code

    def _is_fixable_error(self, error: str) -> bool:
        """Check if an error is one we can auto-fix."""
        fixable_patterns = [
            r'Name "\w+" is not defined',
            r'Cannot resolve import',
            r'Cannot resolve: from',
        ]
        for pattern in fixable_patterns:
            if re.search(pattern, error):
                return True
        return False
