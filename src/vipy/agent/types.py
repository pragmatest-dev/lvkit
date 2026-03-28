"""Shared type discovery and generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..graph import VIGraph
from .codegen.ast_utils import to_module_name


@dataclass
class SharedType:
    """A shared type (cluster/enum) used across VIs."""

    name: str  # Python class name (PascalCase)
    original_name: str  # Original LabVIEW name
    fields: list[tuple[str, str]]  # [(field_name, field_type), ...]
    source_vis: set[str] = field(default_factory=set)  # VIs using this type
    scope: str = "root"  # "root" | "library:name" | "inline"
    is_enum: bool = False
    enum_values: list[str] = field(default_factory=list)


class SharedTypeRegistry:
    """Manages shared types across VIs.

    Discovers types from the graph and determines their scope:
    - root: Used across multiple packages → output/types.py
    - library:name: Used within one library → output/library/types.py
    - inline: Used by single VI → defined inline in that VI's module
    """

    # LabVIEW type to Python type mapping
    TYPE_MAP = {
        "stdString": "str",
        "stdNum": "float",
        "stdDBL": "float",
        "stdSGL": "float",
        "stdI32": "int",
        "stdI16": "int",
        "stdI8": "int",
        "stdU32": "int",
        "stdU16": "int",
        "stdU8": "int",
        "stdBool": "bool",
        "stdPath": "Path",
        "stdEnum": "str",  # Enums become str or Enum class
        "stdRing": "int",
        "stdArray": "list",
        "stdClust": "dict",  # Clusters become dataclasses
    }

    def __init__(self) -> None:
        self._types: dict[str, SharedType] = {}
        self._vi_types: dict[str, set[str]] = {}  # VI name -> type names
        self._library_types: dict[str, set[str]] = {}  # library name -> type names

    def discover_from_graph(self, graph: VIGraph) -> None:
        """Discover shared types from Neo4j graph.

        Queries for Cluster and Enum types that appear in multiple VIs.
        """
        # Query for clusters with their fields and source VIs
        cluster_query = """
        MATCH (v:VI)-[:CONTAINS|PARAMETER_OF|RETURNS*1..3]-(c)
        WHERE c:Cluster OR c:Input:Cluster OR c:Output:Cluster
        WITH c.name AS name, c.id AS id,
             collect(DISTINCT v.name) AS vis
        RETURN name, id, vis
        """

        try:
            clusters = graph.query(cluster_query)

            for cluster in clusters:
                name = cluster.get("name", "UnnamedCluster")
                vis = set(cluster.get("vis", []))

                if not name or len(vis) < 1:
                    continue

                # Determine scope based on usage
                scope = self._determine_scope(name, vis)

                # Create or update type
                py_name = self._to_class_name(name)
                if py_name in self._types:
                    self._types[py_name].source_vis.update(vis)
                else:
                    self._types[py_name] = SharedType(
                        name=py_name,
                        original_name=name,
                        fields=[],  # Fields populated separately
                        source_vis=vis,
                        scope=scope,
                    )

                # Track which VIs use this type
                for vi in vis:
                    self._vi_types.setdefault(vi, set()).add(py_name)

        except Exception:
            # Graph might not be available or query failed
            pass

    def register_type(
        self,
        name: str,
        fields: list[tuple[str, str]],
        source_vi: str,
        is_enum: bool = False,
        enum_values: list[str] | None = None,
    ) -> SharedType:
        """Register a type discovered during VI conversion.

        Args:
            name: Original LabVIEW type name
            fields: List of (field_name, field_type) tuples
            source_vi: VI where this type was found
            is_enum: Whether this is an enum type
            enum_values: Enum value labels if is_enum

        Returns:
            The SharedType (new or existing)
        """
        py_name = self._to_class_name(name)

        if py_name in self._types:
            # Update existing type
            self._types[py_name].source_vis.add(source_vi)
            # Update scope if now used by multiple VIs
            self._types[py_name].scope = self._determine_scope(
                name, self._types[py_name].source_vis
            )
        else:
            # Create new type
            self._types[py_name] = SharedType(
                name=py_name,
                original_name=name,
                fields=[(self._to_field_name(f), self._map_type(t)) for f, t in fields],
                source_vis={source_vi},
                scope="inline",  # Start as inline, promote if reused
                is_enum=is_enum,
                enum_values=enum_values or [],
            )

        self._vi_types.setdefault(source_vi, set()).add(py_name)
        return self._types[py_name]

    def get_types_for_vi(self, vi_name: str) -> list[SharedType]:
        """Get all types needed by a VI."""
        type_names = self._vi_types.get(vi_name, set())
        return [self._types[n] for n in type_names if n in self._types]

    def get_root_types(self) -> list[SharedType]:
        """Get types that belong in root types.py."""
        return [t for t in self._types.values() if t.scope == "root"]

    def get_library_types(self, library_name: str) -> list[SharedType]:
        """Get types that belong in a library's types.py."""
        scope_prefix = f"library:{library_name}"
        return [t for t in self._types.values() if t.scope == scope_prefix]

    def get_inline_types(self, vi_name: str) -> list[SharedType]:
        """Get types that should be defined inline in a VI's module."""
        type_names = self._vi_types.get(vi_name, set())
        return [
            self._types[n]
            for n in type_names
            if n in self._types and self._types[n].scope == "inline"
        ]

    def generate_types_file(self, output_dir: Path, scope: str = "root") -> Path | None:
        """Generate a types.py file for the given scope.

        Args:
            output_dir: Directory to write to
            scope: "root" for main types.py, "library:name" for library-specific

        Returns:
            Path to generated file, or None if no types
        """
        if scope == "root":
            types = self.get_root_types()
            output_path = output_dir / "types.py"
        elif scope.startswith("library:"):
            lib_name = scope.split(":", 1)[1]
            types = self.get_library_types(lib_name)
            lib_dir = output_dir / to_module_name(lib_name)
            lib_dir.mkdir(parents=True, exist_ok=True)
            output_path = lib_dir / "types.py"
        else:
            return None

        if not types:
            return None

        lines = [
            '"""Shared types for LabVIEW conversion."""',
            "",
            "from __future__ import annotations",
            "",
            "from dataclasses import dataclass",
            "from enum import Enum",
            "from pathlib import Path",
            "from typing import Any",
            "",
        ]

        for shared_type in sorted(types, key=lambda t: t.name):
            lines.extend(self._generate_type_code(shared_type))
            lines.append("")

        output_path.write_text("\n".join(lines))
        return output_path

    def generate_all_types_files(self, output_dir: Path) -> list[Path]:
        """Generate all types.py files (root and library-specific)."""
        generated = []

        # Root types
        root_path = self.generate_types_file(output_dir, "root")
        if root_path:
            generated.append(root_path)

        # Library-specific types
        library_scopes = set()
        for t in self._types.values():
            if t.scope.startswith("library:"):
                library_scopes.add(t.scope)

        for scope in library_scopes:
            lib_path = self.generate_types_file(output_dir, scope)
            if lib_path:
                generated.append(lib_path)

        return generated

    def get_import_for_type(self, type_name: str, from_module: str) -> str:
        """Get import statement for a type from a given module.

        Args:
            type_name: Python class name of the type
            from_module: Module requesting the import

        Returns:
            Import statement like 'from types import MyType'
        """
        if type_name not in self._types:
            return ""

        shared_type = self._types[type_name]

        if shared_type.scope == "root":
            return f"from types import {type_name}"
        elif shared_type.scope.startswith("library:"):
            lib_name = shared_type.scope.split(":", 1)[1]
            lib_module = to_module_name(lib_name)
            # Check if from_module is in the same library
            if from_module.startswith(lib_module):
                return f"from types import {type_name}"
            else:
                return f"from {lib_module}.types import {type_name}"
        else:
            # Inline type - no import needed
            return ""

    def _generate_type_code(self, shared_type: SharedType) -> list[str]:
        """Generate Python code for a SharedType."""
        lines = []

        if shared_type.is_enum and shared_type.enum_values:
            # Generate Enum class
            lines.append(f"class {shared_type.name}(str, Enum):")
            lines.append(f'    """Enum from LabVIEW: {shared_type.original_name}."""')
            lines.append("")
            for i, value in enumerate(shared_type.enum_values):
                enum_name = self._to_enum_name(value)
                lines.append(f'    {enum_name} = "{value}"')
        else:
            # Generate dataclass
            lines.append("@dataclass")
            lines.append(f"class {shared_type.name}:")
            lines.append(
                f'    """Cluster from LabVIEW: {shared_type.original_name}."""'
            )
            lines.append("")
            if shared_type.fields:
                for field_name, field_type in shared_type.fields:
                    lines.append(f"    {field_name}: {field_type}")
            else:
                lines.append("    pass  # Fields to be populated")

        return lines

    def _determine_scope(self, name: str, source_vis: set[str]) -> str:
        """Determine the appropriate scope for a type.

        Rules:
        - Used by 1 VI → inline
        - Used by 2+ VIs in same library → library:name
        - Used across libraries/packages → root
        """
        if len(source_vis) <= 1:
            return "inline"

        # Check if all VIs are in the same library
        # (This is a heuristic - proper implementation would check library membership)
        # For now, assume VIs with common prefix are in same library
        prefixes = set()
        for vi in source_vis:
            parts = vi.split(".")
            if len(parts) > 1:
                prefixes.add(parts[0])
            else:
                prefixes.add("")

        if len(prefixes) == 1 and "" not in prefixes:
            return f"library:{list(prefixes)[0]}"

        return "root"

    def _to_class_name(self, name: str) -> str:
        """Convert to PascalCase class name."""
        # Remove common suffixes
        name = name.replace(".ctl", "").replace(".CTL", "")
        words = name.replace("-", " ").replace("_", " ").split()
        result = "".join(word.capitalize() for word in words)
        # Ensure valid Python identifier
        result = "".join(c for c in result if c.isalnum())
        if result and not result[0].isalpha():
            result = "Type" + result
        return result or "UnknownType"

    def _to_field_name(self, name: str) -> str:
        """Convert to snake_case field name."""
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "field_" + result
        return result or "value"

    def _to_enum_name(self, value: str) -> str:
        """Convert enum value to valid Python enum name."""
        result = value.upper().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "VALUE_" + result
        return result or "UNKNOWN"

    def _map_type(self, lv_type: str) -> str:
        """Map LabVIEW type to Python type."""
        return self.TYPE_MAP.get(lv_type, "Any")
