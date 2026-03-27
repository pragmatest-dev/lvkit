"""Primitive discovery and package generation."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..primitive_resolver import get_resolver

if TYPE_CHECKING:
    from ..graph import VIGraph
    from ..llm import LLMConfig
    from .strategies.base import ConversionStrategy


def lookup_primitive(prim_id: int | str) -> dict | None:
    """Look up primitive by ID using the unified resolver."""
    resolved = get_resolver().resolve(prim_id=prim_id)
    if resolved and resolved.confidence != "unknown":
        return {
            "name": resolved.name,
            "python_code": resolved.python_code,
            "description": resolved.description,
            "terminals": resolved.terminals,
            "confidence": resolved.confidence,
        }
    return None


def lookup_primitive_by_types(
    input_types: list[str], output_types: list[str]
) -> dict | None:
    """Fallback lookup by type signature."""
    resolved = get_resolver().resolve(
        input_types=input_types, output_types=output_types
    )
    if resolved and resolved.confidence != "unknown":
        return {
            "name": resolved.name,
            "python_code": resolved.python_code,
            "description": resolved.description,
            "terminals": resolved.terminals,
            "confidence": resolved.confidence,
        }
    return None


@dataclass
class PrimitiveUsage:
    """A primitive operation used in VIs."""

    prim_res_id: int
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)
    source_vis: set[str] = field(default_factory=set)
    generated_name: str | None = None  # Python function name once generated
    # Resolved info from PrimitiveResolver
    resolved_name: str | None = None
    python_code: str | None = None  # Python code (inline template or full function)
    inline: bool = True  # True = inline at call sites, False = generate module
    terminals: list[dict] = field(default_factory=list)
    confidence: str = "unknown"  # exact_id, exact_name, exact_type, compatible_type

    def is_inline(self) -> bool:
        """Check if this primitive can be used inline (no wrapper needed)."""
        return self.python_code is not None and self.inline


class PrimitiveRegistry:
    """Manages primitive operations used across VIs.

    Discovers primitives from the graph, groups by signature,
    and generates a primitives/ package with implementations.
    """

    def __init__(self) -> None:
        self._primitives: dict[int, PrimitiveUsage] = {}
        self._vi_primitives: dict[str, set[int]] = {}  # VI -> primResIDs

    def discover_from_graph(self, graph: VIGraph) -> None:
        """Discover all primitives used in the graph.

        Queries for Primitive nodes and extracts their primResID and terminal types.
        """
        query = """
        MATCH (v:VI)-[:CONTAINS]->(p:Primitive)
        OPTIONAL MATCH (p)-[:HAS_TERMINAL]->(t:Terminal)
        WITH v.name AS vi_name, p.primResID AS prim_id,
             collect(CASE WHEN 'Input' IN labels(t) THEN t.type END) AS inputs,
             collect(CASE WHEN 'Output' IN labels(t) THEN t.type END) AS outputs
        RETURN vi_name, prim_id,
               [x IN inputs WHERE x IS NOT NULL] AS input_types,
               [x IN outputs WHERE x IS NOT NULL] AS output_types
        """

        try:
            results = graph.query(query)

            for row in results:
                vi_name = row.get("vi_name", "")
                prim_id = row.get("prim_id")
                input_types = row.get("input_types", [])
                output_types = row.get("output_types", [])

                if prim_id is None:
                    continue

                self.register_primitive(
                    prim_res_id=prim_id,
                    input_types=input_types,
                    output_types=output_types,
                    source_vi=vi_name,
                )

        except Exception:
            # Graph might not be available
            pass

    def register_primitive(
        self,
        prim_res_id: int,
        input_types: list[str],
        output_types: list[str],
        source_vi: str,
    ) -> PrimitiveUsage:
        """Register a primitive usage.

        Args:
            prim_res_id: The primitive resource ID
            input_types: Input terminal types
            output_types: Output terminal types
            source_vi: VI where this primitive was found

        Returns:
            The PrimitiveUsage (new or existing)
        """
        if prim_res_id in self._primitives:
            self._primitives[prim_res_id].source_vis.add(source_vi)
        else:
            # Resolve primitive using our database
            resolver = get_resolver()
            resolved = resolver.resolve(
                prim_id=prim_res_id,
                input_types=input_types,
                output_types=output_types,
            )

            usage = PrimitiveUsage(
                prim_res_id=prim_res_id,
                input_types=input_types,
                output_types=output_types,
                source_vis={source_vi},
            )

            if resolved:
                usage.resolved_name = resolved.name
                usage.python_code = resolved.python_code
                usage.inline = resolved.inline
                usage.terminals = [t.model_dump() for t in resolved.terminals]
                usage.confidence = resolved.confidence

            self._primitives[prim_res_id] = usage

        self._vi_primitives.setdefault(source_vi, set()).add(prim_res_id)
        return self._primitives[prim_res_id]

    def get_primitives_for_vi(self, vi_name: str) -> list[PrimitiveUsage]:
        """Get all primitives used by a VI."""
        prim_ids = self._vi_primitives.get(vi_name, set())
        return [self._primitives[pid] for pid in prim_ids if pid in self._primitives]

    def get_all_primitives(self) -> list[PrimitiveUsage]:
        """Get all discovered primitives."""
        return list(self._primitives.values())

    def get_primitive_names(self, vi_name: str) -> list[str]:
        """Get function names for primitives used by a VI.

        Only returns names for primitives that have been generated.
        """
        names = []
        for prim in self.get_primitives_for_vi(vi_name):
            if prim.generated_name:
                names.append(prim.generated_name)
        return names

    def generate_primitives_package(
        self,
        output_dir: Path,
        llm_config: LLMConfig | None = None,
        strategy: ConversionStrategy | None = None,
    ) -> Path:
        """Generate the primitives/ package with implementations.

        Only generates wrapper functions for COMPLEX primitives that can't
        be used inline. Simple primitives (like array[index], len(arr))
        are used inline in VI code via their python_hint.

        Args:
            output_dir: Output directory
            llm_config: LLM config for generating primitives
            strategy: Strategy to use (for validation/retry). Falls back to baseline.

        Returns:
            Path to primitives/ directory
        """
        primitives_dir = output_dir / "primitives"
        primitives_dir.mkdir(parents=True, exist_ok=True)

        generated_names: list[str] = []
        inline_count = 0
        complex_count = 0

        # Only generate functions for complex primitives
        if llm_config:
            for prim_id, usage in self._primitives.items():
                # Skip inline primitives - LLM uses python_hint directly
                if usage.is_inline():
                    inline_count += 1
                    continue

                complex_count += 1
                func_name, impl = self._generate_primitive_with_strategy(
                    usage, llm_config, strategy
                )
                if func_name and impl:
                    self._write_primitive_file(primitives_dir, func_name, impl)
                    usage.generated_name = func_name
                    generated_names.append(func_name)

        if inline_count > 0 or complex_count > 0:
            print(f"  Primitives: {inline_count} inline, {complex_count} need wrappers")

        # Generate __init__.py
        self._write_init_file(primitives_dir, generated_names)

        return primitives_dir

    def _write_primitive_file(
        self,
        primitives_dir: Path,
        func_name: str,
        implementation: str,
    ) -> None:
        """Write a single primitive implementation file."""
        from .validator import deduplicate_imports

        file_path = primitives_dir / f"{func_name}.py"

        content = f'''"""Primitive: {func_name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any

{implementation}
'''
        # LLM may include duplicate imports - deduplicate them
        content = deduplicate_imports(content)
        file_path.write_text(content)

    def _write_init_file(
        self,
        primitives_dir: Path,
        func_names: list[str],
    ) -> None:
        """Write the primitives/__init__.py with all exports."""
        lines = [
            '"""LabVIEW primitive implementations."""',
            "",
            "from __future__ import annotations",
            "",
        ]

        # Import all primitives using relative imports
        for name in sorted(set(func_names)):
            lines.append(f"from .{name} import {name}")

        lines.append("")
        lines.append(f"__all__ = {sorted(set(func_names))!r}")

        (primitives_dir / "__init__.py").write_text("\n".join(lines))

    def _generate_primitive_with_strategy(
        self,
        usage: PrimitiveUsage,
        llm_config: LLMConfig,
        strategy: ConversionStrategy | None = None,
    ) -> tuple[str | None, str | None]:
        """Generate a primitive using the same validation/retry loop as VI conversion.

        Uses the strategy system for validation and error feedback, ensuring
        primitives go through the same code quality checks as VIs.

        Args:
            usage: The primitive usage info
            llm_config: LLM configuration
            strategy: Optional strategy to use. Falls back to baseline.

        Returns:
            Tuple of (function_name, implementation) or (None, None)
        """
        from ..llm import generate_code
        from .context_builder import ContextBuilder
        from .validator import CodeValidator

        # Build primitive context as a simplified VI context
        # This lets us use the same strategy/validation system
        func_name = (
            self._to_python_name(usage.resolved_name)
            if usage.resolved_name
            else f"primitive_{usage.prim_res_id}"
        )

        # Build prompt for primitive
        prompt = self._build_primitive_prompt(usage, func_name)
        original_prompt = prompt

        # Use validator for proper validation
        validator = CodeValidator()

        # Expected output count from terminal info
        output_terminals = [t for t in usage.terminals if t.get("direction") == "out"]
        expected_output_count = len(output_terminals) if output_terminals else 1

        max_retries = 3
        code = ""
        errors: list[str] = []

        for attempt in range(1, max_retries + 1):
            # Generate code
            response = generate_code(prompt, llm_config)
            code = self._extract_code(response)

            # Use the same validator as VIs
            validation = validator.validate(
                code,
                module_name=func_name,
                expected_output_count=expected_output_count,
            )

            if validation.is_valid:
                return func_name, code

            # Build error context for retry (same as strategies do)
            errors = [e.message for e in validation.errors]
            prompt = ContextBuilder.build_error_context(
                code, validation.errors, original_prompt
            )

        # All retries failed
        msg = (
            f"  ERROR: Failed to generate {func_name}"
            f" after {max_retries} attempts: {errors}"
        )
        print(msg, file=sys.stderr)
        raise RuntimeError(f"Failed to generate primitive {func_name}: {errors}")

    def _build_primitive_prompt(self, usage: PrimitiveUsage, func_name: str) -> str:
        """Build the prompt for generating a primitive function."""
        # Filter out Void types
        input_types = [t for t in usage.input_types if t and t != "Void"]
        output_types = [t for t in usage.output_types if t and t != "Void"]
        input_str = ", ".join(input_types) if input_types else "None"
        output_str = ", ".join(output_types) if output_types else "None"

        known_name = usage.resolved_name
        python_code = usage.python_code
        confidence = usage.confidence
        terminals = usage.terminals

        # Build terminal info
        terminal_info = ""
        if terminals:
            term_lines = []
            for t in terminals:
                direction = t.get("direction", "?")
                name = t.get("name", f"term_{t.get('index', 0)}")
                term_lines.append(f"  - {name} ({direction})")
            terminal_info = "\n".join(term_lines)

        # Choose prompt based on available info
        if python_code and python_code.strip() and not python_code.startswith("#"):
            vi_label = known_name or f"primitive {usage.prim_res_id}"
            return f"""Generate a Python function implementing LabVIEW's "{vi_label}".

Function name: {func_name}
Python equivalent: {python_code}

Input types: {input_str}
Output types: {output_str}
{f"Terminal names:{chr(10)}{terminal_info}" if terminal_info else ""}

Requirements:
- Function must be named `{func_name}`
- The Python hint shows the core operation - wrap it in a proper function
- Use type hints matching the LabVIEW types
- Include a docstring documenting parameters and return value
- Use standard library only (pathlib, os, etc.)

Output ONLY the function definition, no explanations.
"""
        elif known_name and confidence in ("exact_id", "exact_name"):
            return f"""Generate a Python function implementing LabVIEW's "{known_name}".

Function name: {func_name}
primResID: {usage.prim_res_id}

Input types: {input_str}
Output types: {output_str}
{f"Terminal names:{chr(10)}{terminal_info}" if terminal_info else ""}

Requirements:
- Function must be named `{func_name}`
- This is the LabVIEW "{known_name}" function
- Use type hints
- Include a docstring documenting parameters and return value
- Use standard library only (pathlib, os, etc.)

Output ONLY the function definition, no explanations.
"""
        else:
            prim_id = usage.prim_res_id
            return f"""Generate a Python function for LabVIEW primitive #{prim_id}.

Input types: {input_str}
Output types: {output_str}
{f"Terminal names:{chr(10)}{terminal_info}" if terminal_info else ""}

Context: This is a LabVIEW built-in operation. Based on the primitive ID and types,
infer what operation this performs and implement it in Python.

Requirements:
- Use type hints
- Include a docstring describing the operation and return value
- Handle edge cases appropriately
- Use standard library only (pathlib, os, etc.)

Output ONLY the function definition, no explanations.
"""

    @staticmethod
    def _extract_code(response: str) -> str:
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

    @staticmethod
    def _to_python_name(name: str) -> str:
        """Convert primitive name to Python function name."""
        # Remove common suffixes
        name = name.replace(" Function", "").replace(" VI", "")
        # Convert to snake_case
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or "unknown_primitive"

    @staticmethod
    def _validate_imports(code: str) -> str | None:
        """Validate imports in generated code.

        Returns error message if invalid, None if valid.
        """
        # Known invalid import patterns
        errors = []

        # Check for Path imported from typing
        if re.search(r'from typing import[^#\n]*\bPath\b', code):
            errors.append("Invalid import: Path should be from pathlib, not typing")

        # Check for typing names imported from pathlib
        typing_names = {'Optional', 'Any', 'List', 'Dict', 'Tuple', 'Union', 'Callable'}
        for name in typing_names:
            if re.search(rf'from pathlib import[^#\n]*\b{name}\b', code):
                errors.append(
                    f"Invalid import: {name} should be from typing, not pathlib"
                )

        return "; ".join(errors) if errors else None

    def get_import_statement(self, from_library: str | None = None) -> str:
        """Get the import statement for primitives package.

        Args:
            from_library: Library of the importing module (None for root-level)
        """
        names = []
        for usage in self._primitives.values():
            if usage.generated_name:
                names.append(usage.generated_name)

        if not names:
            return ""

        # Use relative import depth based on whether in library
        prefix = ".." if from_library else "."
        return f"from {prefix}primitives import {', '.join(sorted(set(names)))}"

    def get_import_for_vi(self, vi_name: str, from_library: str | None = None) -> str:
        """Get import statement for primitives used by a specific VI.

        Args:
            vi_name: Name of the VI
            from_library: Library of the importing module (None for root-level)
        """
        names = self.get_primitive_names(vi_name)
        if not names:
            return ""
        # Use relative import depth based on whether in library
        prefix = ".." if from_library else "."
        return f"from {prefix}primitives import {', '.join(sorted(set(names)))}"

    def get_primitive_id_mapping(self, vi_name: str) -> dict[int, str]:
        """Get mapping of primResID -> generated function name for a VI.

        Args:
            vi_name: Name of the VI

        Returns:
            Dict mapping primitive resource IDs to their generated function names
        """
        mapping: dict[int, str] = {}
        for prim in self.get_primitives_for_vi(vi_name):
            if prim.generated_name:
                mapping[prim.prim_res_id] = prim.generated_name
        return mapping

    def get_primitive_context(self, vi_name: str) -> dict[int, dict]:
        """Get rich primitive context for VI conversion.

        Returns all known info about primitives used in this VI,
        including names, Python code, terminal info, and confidence.

        Args:
            vi_name: Name of the VI

        Returns:
            Dict mapping primResID -> primitive context info
        """
        context: dict[int, dict] = {}
        for prim in self.get_primitives_for_vi(vi_name):
            func_name = prim.generated_name or (
                self._to_python_name(prim.resolved_name)
                if prim.resolved_name
                else f"primitive_{prim.prim_res_id}"
            )
            context[prim.prim_res_id] = {
                "name": prim.resolved_name or f"primitive_{prim.prim_res_id}",
                "python_function": func_name,
                "python_code": prim.python_code or "",
                "inline": prim.inline,
                "terminals": prim.terminals,
                "confidence": prim.confidence,
                "input_types": prim.input_types,
                "output_types": prim.output_types,
            }
        return context
