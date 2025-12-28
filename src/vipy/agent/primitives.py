"""Primitive discovery and package generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..primitive_resolver import PrimitiveResolver, ResolvedPrimitive, get_resolver

if TYPE_CHECKING:
    from ..graph import VIGraph
    from ..llm import LLMConfig


def lookup_primitive(prim_id: int | str) -> dict | None:
    """Look up primitive by ID using the unified resolver."""
    resolved = get_resolver().resolve(prim_id=prim_id)
    if resolved and resolved.confidence != "unknown":
        return {
            "name": resolved.name,
            "python_hint": resolved.python_hint,
            "description": resolved.description,
            "terminals": resolved.terminals,
            "confidence": resolved.confidence,
        }
    return None


def lookup_primitive_by_types(input_types: list[str], output_types: list[str]) -> dict | None:
    """Fallback lookup by type signature."""
    resolved = get_resolver().resolve(input_types=input_types, output_types=output_types)
    if resolved and resolved.confidence != "unknown":
        return {
            "name": resolved.name,
            "python_hint": resolved.python_hint,
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
    python_hint: str | None = None
    terminals: list[dict] = field(default_factory=list)
    confidence: str = "unknown"  # exact_id, exact_name, exact_type, compatible_type, unknown


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
                usage.python_hint = resolved.python_hint
                usage.terminals = resolved.terminals
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
        llm_config: "LLMConfig | None" = None,
    ) -> Path:
        """Generate the primitives/ package with all implementations.

        All primitives are generated by LLM based on context (terminal types,
        connections, etc.). The LLM infers primitive behavior from the graph.

        Args:
            output_dir: Output directory
            llm_config: LLM config for generating primitives

        Returns:
            Path to primitives/ directory
        """
        primitives_dir = output_dir / "primitives"
        primitives_dir.mkdir(parents=True, exist_ok=True)

        generated_names: list[str] = []

        # Generate all primitives with LLM
        if llm_config:
            for prim_id, usage in self._primitives.items():
                func_name, impl = self._generate_primitive_with_llm(
                    usage, llm_config
                )
                if func_name and impl:
                    self._write_primitive_file(primitives_dir, func_name, impl)
                    usage.generated_name = func_name
                    generated_names.append(func_name)

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

        # Import all primitives
        for name in sorted(set(func_names)):
            lines.append(f"from .{name} import {name}")

        lines.append("")
        lines.append(f"__all__ = {sorted(set(func_names))!r}")

        (primitives_dir / "__init__.py").write_text("\n".join(lines))

    def _generate_primitive_with_llm(
        self,
        usage: PrimitiveUsage,
        llm_config: "LLMConfig",
    ) -> tuple[str | None, str | None]:
        """Generate a primitive implementation using LLM.

        Uses the resolved primitive info from our database:
        1. If we have a known Python hint, use it directly
        2. If we have a known name but no hint, ask LLM to implement it
        3. If unknown, fall back to LLM inference from types

        Args:
            usage: The primitive usage info
            llm_config: LLM configuration

        Returns:
            Tuple of (function_name, implementation) or (None, None)
        """
        from ..llm import generate_code

        # Filter out Void types
        input_types = [t for t in usage.input_types if t and t != "Void"]
        output_types = [t for t in usage.output_types if t and t != "Void"]
        input_str = ", ".join(input_types) if input_types else "None"
        output_str = ", ".join(output_types) if output_types else "None"

        # Use resolved info from our database
        known_name = usage.resolved_name
        python_hint = usage.python_hint
        confidence = usage.confidence
        terminals = usage.terminals

        # Generate function name
        if known_name:
            func_name = self._to_python_name(known_name)
        else:
            func_name = f"primitive_{usage.prim_res_id}"

        # Build terminal info for context
        terminal_info = ""
        if terminals:
            term_lines = []
            for t in terminals:
                direction = t.get("direction", "?")
                name = t.get("name", f"term_{t.get('index', 0)}")
                term_lines.append(f"  - {name} ({direction})")
            terminal_info = "\n".join(term_lines)

        # Strategy 1: We have a Python hint - use it to guide the LLM
        if python_hint and python_hint.strip() and not python_hint.startswith("#"):
            prompt = f"""Generate a Python function implementing LabVIEW's "{known_name or f'primitive {usage.prim_res_id}'}".

Function name: {func_name}
Python equivalent: {python_hint}

Input types: {input_str}
Output types: {output_str}
{f"Terminal names:{chr(10)}{terminal_info}" if terminal_info else ""}

Requirements:
- Function must be named `{func_name}`
- The Python hint shows the core operation - wrap it in a proper function
- Use type hints matching the LabVIEW types
- Include a docstring
- Use standard library only (pathlib, os, etc.)

Output ONLY the function definition, no explanations.
"""
        # Strategy 2: We have a name but no usable hint
        elif known_name and confidence in ("exact_id", "exact_name"):
            prompt = f"""Generate a Python function implementing LabVIEW's "{known_name}".

Function name: {func_name}
primResID: {usage.prim_res_id}

Input types: {input_str}
Output types: {output_str}
{f"Terminal names:{chr(10)}{terminal_info}" if terminal_info else ""}

Requirements:
- Function must be named `{func_name}`
- This is the LabVIEW "{known_name}" function
- Use type hints
- Include a docstring
- Use standard library only (pathlib, os, etc.)

Output ONLY the function definition, no explanations.
"""
        # Strategy 3: Unknown primitive - LLM must infer
        else:
            prompt = f"""Generate a Python function for LabVIEW primitive #{usage.prim_res_id}.

Input types: {input_str}
Output types: {output_str}
{f"Terminal names:{chr(10)}{terminal_info}" if terminal_info else ""}

Context: This is a LabVIEW built-in operation. Based on the primitive ID and types,
infer what operation this performs and implement it in Python.

Requirements:
- Use type hints
- Include a docstring describing the operation
- Handle edge cases appropriately
- Use standard library only (pathlib, os, etc.)

Output ONLY the function definition, no explanations.
"""

        try:
            code = generate_code(prompt, llm_config)

            # Extract function name from generated code
            import ast
            tree = ast.parse(code)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef):
                    # Prefer our chosen name
                    return func_name, code

            return func_name, code

        except Exception:
            # LLM generation failed - create stub with any known info
            desc = f'LabVIEW "{known_name}"' if known_name else f"primitive #{usage.prim_res_id}"
            hint_comment = f"\n    # Hint: {python_hint}" if python_hint else ""
            stub = f'''def {func_name}(*args, **kwargs):
    """{desc} - implementation needed."""{hint_comment}
    raise NotImplementedError("{desc} not implemented")
'''
            return func_name, stub

    @staticmethod
    def _to_python_name(name: str) -> str:
        """Convert primitive name to Python function name."""
        # Remove common suffixes
        name = name.replace(" Function", "").replace(" VI", "")
        # Convert to snake_case
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or "unknown_primitive"

    def get_import_statement(self) -> str:
        """Get the import statement for primitives package."""
        names = []
        for usage in self._primitives.values():
            if usage.generated_name:
                names.append(usage.generated_name)

        if not names:
            return ""

        return f"from .primitives import {', '.join(sorted(set(names)))}"

    def get_import_for_vi(self, vi_name: str) -> str:
        """Get import statement for primitives used by a specific VI."""
        names = self.get_primitive_names(vi_name)
        if not names:
            return ""
        return f"from .primitives import {', '.join(sorted(set(names)))}"

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
        including names, Python hints, terminal info, and confidence.

        Args:
            vi_name: Name of the VI

        Returns:
            Dict mapping primResID -> {name, python_function, python_hint, terminals, confidence}
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
                "python_hint": prim.python_hint or "",
                "terminals": prim.terminals,
                "confidence": prim.confidence,
                "input_types": prim.input_types,
                "output_types": prim.output_types,
            }
        return context
