"""Primitive discovery and package generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..graph import VIGraph
    from ..llm import LLMConfig


@dataclass
class PrimitiveUsage:
    """A primitive operation used in VIs."""

    prim_res_id: int
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)
    source_vis: set[str] = field(default_factory=set)
    generated_name: str | None = None  # Python function name once generated


# Known primitives with Python implementations
# These don't need LLM generation
KNOWN_PRIMITIVES: dict[int, tuple[str, str]] = {
    # (primResID): (function_name, implementation)
    1419: ("build_path", '''def build_path(base: Path | str, *parts: str) -> Path:
    """LabVIEW Build Path primitive."""
    return Path(base).joinpath(*parts)
'''),
    1420: ("strip_path", '''def strip_path(path: Path | str) -> tuple[Path, str]:
    """LabVIEW Strip Path primitive."""
    p = Path(path)
    return p.parent, p.name
'''),
    1001: ("add", '''def add(a: float, b: float) -> float:
    """LabVIEW Add primitive."""
    return a + b
'''),
    1002: ("subtract", '''def subtract(a: float, b: float) -> float:
    """LabVIEW Subtract primitive."""
    return a - b
'''),
    1003: ("multiply", '''def multiply(a: float, b: float) -> float:
    """LabVIEW Multiply primitive."""
    return a * b
'''),
    1004: ("divide", '''def divide(a: float, b: float) -> float:
    """LabVIEW Divide primitive."""
    return a / b if b != 0 else float('inf')
'''),
}


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

        Queries for Primitive nodes and extracts their primResID.
        """
        query = """
        MATCH (v:VI)-[:CONTAINS]->(p:Primitive)
        RETURN v.name AS vi_name, p.primResID AS prim_id
        """

        try:
            results = graph.query(query)

            for row in results:
                vi_name = row.get("vi_name", "")
                prim_id = row.get("prim_id")

                if prim_id is None:
                    continue

                self.register_primitive(
                    prim_res_id=prim_id,
                    input_types=[],
                    output_types=[],
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
            self._primitives[prim_res_id] = PrimitiveUsage(
                prim_res_id=prim_res_id,
                input_types=input_types,
                output_types=output_types,
                source_vis={source_vi},
            )

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
            elif prim.prim_res_id in KNOWN_PRIMITIVES:
                names.append(KNOWN_PRIMITIVES[prim.prim_res_id][0])
        return names

    def generate_primitives_package(
        self,
        output_dir: Path,
        llm_config: "LLMConfig | None" = None,
    ) -> Path:
        """Generate the primitives/ package with all implementations.

        Known primitives use predefined implementations.
        Unknown primitives are generated by LLM if config provided.

        Args:
            output_dir: Output directory
            llm_config: LLM config for generating unknown primitives

        Returns:
            Path to primitives/ directory
        """
        primitives_dir = output_dir / "primitives"
        primitives_dir.mkdir(parents=True, exist_ok=True)

        generated_names: list[str] = []

        # Generate known primitives
        for prim_id, (func_name, impl) in KNOWN_PRIMITIVES.items():
            if prim_id in self._primitives:
                self._write_primitive_file(primitives_dir, func_name, impl)
                self._primitives[prim_id].generated_name = func_name
                generated_names.append(func_name)

        # Generate unknown primitives with LLM
        if llm_config:
            for prim_id, usage in self._primitives.items():
                if prim_id not in KNOWN_PRIMITIVES:
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
        file_path = primitives_dir / f"{func_name}.py"

        content = f'''"""Primitive: {func_name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any

{implementation}
'''
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

        Args:
            usage: The primitive usage info
            llm_config: LLM configuration

        Returns:
            Tuple of (function_name, implementation) or (None, None)
        """
        from ..llm import generate_code

        # Build prompt for LLM
        input_types = ", ".join(usage.input_types) if usage.input_types else "unknown"
        output_types = ", ".join(usage.output_types) if usage.output_types else "unknown"

        prompt = f"""Generate a Python function for LabVIEW primitive #{usage.prim_res_id}.

Input types: {input_types}
Output types: {output_types}

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
                    return node.name, code

            # Fallback: generate generic name
            func_name = f"primitive_{usage.prim_res_id}"
            return func_name, code

        except Exception:
            # LLM generation failed - create stub
            func_name = f"primitive_{usage.prim_res_id}"
            stub = f'''def {func_name}(*args, **kwargs):
    """LabVIEW primitive #{usage.prim_res_id} - implementation needed."""
    raise NotImplementedError("Primitive #{usage.prim_res_id} not implemented")
'''
            return func_name, stub

    def get_import_statement(self) -> str:
        """Get the import statement for primitives package."""
        names = []
        for usage in self._primitives.values():
            if usage.generated_name:
                names.append(usage.generated_name)
            elif usage.prim_res_id in KNOWN_PRIMITIVES:
                names.append(KNOWN_PRIMITIVES[usage.prim_res_id][0])

        if not names:
            return ""

        return f"from .primitives import {', '.join(sorted(set(names)))}"

    def get_import_for_vi(self, vi_name: str) -> str:
        """Get import statement for primitives used by a specific VI."""
        names = self.get_primitive_names(vi_name)
        if not names:
            return ""
        return f"from .primitives import {', '.join(sorted(set(names)))}"
