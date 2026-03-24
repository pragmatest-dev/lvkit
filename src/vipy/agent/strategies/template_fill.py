"""Template fill strategy: Generate skeleton, LLM fills in logic.

1. Analyze VI structure to generate a code skeleton
2. Mark blanks (___) where logic needs to be filled
3. Ask LLM to fill in only the blanks

This constrains the output structure and reduces room for error.
"""

from __future__ import annotations

import time
from typing import Any

from vipy.graph_types import VIContext

from ...llm import generate_code
from ..context_builder import ContextBuilder
from . import register_strategy
from .base import ConversionStrategy, StrategyResult


@register_strategy
class TemplateFillStrategy(ConversionStrategy):
    """Generate skeleton, LLM fills blanks."""

    name = "template_fill"
    description = "Generate code skeleton with blanks, LLM fills in logic"

    def convert(
        self,
        vi_name: str,
        vi_context: VIContext,
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> StrategyResult:
        """Generate code by filling in a template."""
        start_time = time.time()

        # Generate template from VI structure
        template = self._generate_template(
            vi_name, vi_context, converted_deps, primitive_names, primitive_context
        )

        expected_subvis = self._get_expected_subvis(vi_context)
        expected_output_count = len(vi_context.outputs)

        # Build context for LLM (with library-aware imports)
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

        code = ""
        errors: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            if attempt == 1:
                prompt = f"""{base_context}

## Code Template

I've generated a code skeleton for this VI. Fill in the blanks marked with `___`:

```python
{template}
```

## Instructions

Replace each `___` with the correct code based on:
1. The VI context above (inputs, outputs, data flow)
2. The available primitives and SubVIs
3. The comments next to each blank

Output the COMPLETE Python code with all blanks filled in.
"""
            else:
                error_text = "\n".join(f"- {e}" for e in errors)
                prompt = f"""{base_context}

## Previous Attempt Failed

Your code had these errors:
{error_text}

Original template:
```python
{template}
```

Fix the errors and output the complete corrected code.
"""

            response = generate_code(prompt, self.llm_config)
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
                        "template": template,
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
                "template": template,
            },
        )

    def _generate_template(
        self,
        vi_name: str,
        vi_context: VIContext,
        converted_deps: dict[str, Any],
        primitive_names: list[str],
        primitive_context: dict[int, dict[str, Any]],
    ) -> str:
        """Generate code skeleton from VI structure."""
        # Function name
        func_name = self._to_function_name(vi_name)

        # Gather inputs
        inputs = vi_context.inputs
        outputs = vi_context.outputs
        operations = vi_context.operations

        # Build imports section
        imports = [
            "from __future__ import annotations",
            "from typing import Any",
            "from pathlib import Path",
        ]

        # Add SubVI imports
        for dep_name, dep_info in converted_deps.items():
            if hasattr(dep_info, 'import_statement'):
                imports.append(dep_info.import_statement)

        # Add primitive imports
        if primitive_names:
            imports.append(f"from primitives import {', '.join(primitive_names)}")

        # Build parameter list
        params = []
        for inp in inputs:
            name = self._to_var_name(inp.get("name", "input"))
            typ = self._map_type(inp.get("type", "Any"))
            params.append(f"{name}: {typ}")

        param_str = ", ".join(params) if params else ""

        # Build return type
        if len(outputs) == 0:
            return_type = "None"
        elif len(outputs) == 1:
            return_type = self._map_type(outputs[0].get("type", "Any"))
        else:
            return_type = "dict[str, Any]"

        # Build operation steps
        steps = []
        step_num = 1
        for op in operations:
            labels = op.get("labels", [])
            name = op.get("name", "")

            if "SubVI" in labels and name:
                subvi_func = self._to_function_name(name)
                steps.append(f"    # Step {step_num}: Call SubVI {name}")
                steps.append(f"    result_{step_num} = ___  # Call {subvi_func}(...)")
                step_num += 1
            elif "Primitive" in labels:
                prim_id = op.get("primResID")
                pctx = primitive_context.get(prim_id, {})
                prim_func = pctx.get("python_function", f"primitive_{prim_id}")
                hint = pctx.get("python_hint", "")
                steps.append(f"    # Step {step_num}: {prim_func}")
                if hint:
                    steps.append(f"    # Hint: {hint}")
                steps.append(f"    result_{step_num} = ___  # Call {prim_func}(...)")
                step_num += 1

        # Build return section
        if len(outputs) == 0:
            return_section = "    return None"
        elif len(outputs) == 1:
            out_name = self._to_var_name(outputs[0].get("name", "output"))
            return_section = f"    {out_name} = ___  # Set from operation results\n    return {out_name}"
        else:
            return_section = "    return ___  # Return dict with output values"

        # Assemble template
        imports_str = "\n".join(imports)
        steps_str = "\n".join(steps) if steps else "    # No operations detected"

        return f"""{imports_str}


def {func_name}({param_str}) -> {return_type}:
    \"\"\"Converted from {vi_name}.\"\"\"

{steps_str}

{return_section}
"""

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

    def _to_var_name(self, name: str) -> str:
        """Convert control name to Python variable name."""
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "var_" + result
        return result or "value"

    def _map_type(self, lv_type: str) -> str:
        """Map LabVIEW type to Python type."""
        type_map = {
            "stdString": "str",
            "stdNum": "float",
            "stdBool": "bool",
            "stdPath": "Path",
            "stdClust": "dict",
            "indArr": "list",
        }
        return type_map.get(lv_type, "Any")
