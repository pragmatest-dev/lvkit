"""Enum discovery and context generation for agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..enum_resolver import EnumResolver, ResolvedEnum, get_enum_resolver

if TYPE_CHECKING:
    from ..graph import VIGraph


@dataclass
class EnumUsage:
    """An enum/typedef used in VIs."""

    control_file: str  # e.g., "System Directory Type.ctl"
    vilib_path: str  # e.g., "Utility/sysdir.llb"
    resolved: ResolvedEnum | None = None
    source_vis: set[str] = field(default_factory=set)
    used_values: set[int] = field(default_factory=set)  # Which enum values are used


class EnumRegistry:
    """Manages enum/typedef usage across VIs.

    Discovers enums from VI constants and provides context
    for code generation.
    """

    def __init__(self, resolver: EnumResolver | None = None):
        """Initialize the registry.

        Args:
            resolver: Optional custom resolver, uses global if not provided
        """
        self._resolver = resolver or get_enum_resolver()
        self._enums: dict[str, EnumUsage] = {}  # control_file -> usage
        self._vi_enums: dict[str, set[str]] = {}  # vi_name -> set of control_files

    def discover_from_graph(self, graph: "VIGraph") -> None:
        """Discover enums used in the graph from constants.

        Queries the graph for constants that might be enum values,
        based on their labels and types.
        """
        # Query for constants with labels that might indicate enums
        constants = graph.query("""
            MATCH (v:VI)-[:CONTAINS]->(c:Constant)
            WHERE c.label IS NOT NULL AND c.type IS NOT NULL
            RETURN v.name AS vi_name, c.value AS value, c.label AS label,
                   c.type AS type, c.python AS python
        """)

        for const in constants:
            vi_name = const.get("vi_name", "")
            label = const.get("label", "") or ""
            value = const.get("value", "")
            python = const.get("python", "")

            # Check if this looks like a known enum type
            enum = self._detect_enum(label, python)
            if enum:
                self._register_enum(enum, vi_name, value)

    def _detect_enum(self, label: str, python: str) -> ResolvedEnum | None:
        """Detect if a constant is an enum value based on its label."""
        label_lower = label.lower()

        # Check for known patterns
        patterns = [
            ("system directory", "System Directory Type"),
            ("file operation", "File Operation"),
            ("file access", "File Access"),
            ("line ending", "Line Ending"),
            ("format string", "Format String Type"),
            ("button type", "Button Type"),
            ("error action", "Error Action"),
            ("comparison mode", "Comparison Mode"),
        ]

        for pattern, enum_name in patterns:
            if pattern in label_lower:
                return self._resolver.resolve(name=enum_name)

        # Check if the label ends with known control file patterns
        if label.endswith(".ctl"):
            return self._resolver.resolve(control_file=label)

        return None

    def _register_enum(
        self,
        enum: ResolvedEnum,
        vi_name: str,
        value: str,
    ) -> None:
        """Register an enum usage."""
        key = enum.control_file

        if key not in self._enums:
            self._enums[key] = EnumUsage(
                control_file=enum.control_file,
                vilib_path=enum.vilib_path,
                resolved=enum,
            )

        self._enums[key].source_vis.add(vi_name)

        # Try to extract the enum value index
        try:
            if len(value) == 8:
                int_val = int(value, 16)
                self._enums[key].used_values.add(int_val)
        except (ValueError, TypeError):
            pass

        self._vi_enums.setdefault(vi_name, set()).add(key)

    def register_enum(
        self,
        control_file: str,
        vi_name: str,
        value_index: int | None = None,
    ) -> EnumUsage | None:
        """Register an enum usage manually.

        Args:
            control_file: The control file name (e.g., "System Directory Type.ctl")
            vi_name: VI where this enum is used
            value_index: The enum value index used (optional)

        Returns:
            EnumUsage if the enum was resolved, None otherwise
        """
        enum = self._resolver.resolve(control_file=control_file)
        if not enum:
            enum = self._resolver.resolve(name=control_file.replace(".ctl", ""))
        if not enum:
            return None

        self._register_enum(enum, vi_name, f"{value_index:08x}" if value_index else "")
        return self._enums.get(enum.control_file)

    def get_enums_for_vi(self, vi_name: str) -> list[EnumUsage]:
        """Get all enums used by a VI."""
        control_files = self._vi_enums.get(vi_name, set())
        return [self._enums[cf] for cf in control_files if cf in self._enums]

    def get_enum_context(self, vi_name: str) -> dict[str, dict]:
        """Get rich enum context for VI conversion.

        Returns all known info about enums used in this VI,
        including names, values, and Python equivalents.

        Args:
            vi_name: Name of the VI

        Returns:
            Dict mapping control_file -> {name, values: [{index, name, python}]}
        """
        context: dict[str, dict] = {}

        for usage in self.get_enums_for_vi(vi_name):
            if not usage.resolved:
                continue

            values = []
            for idx, val in sorted(usage.resolved.values.items()):
                val_info: dict = {
                    "index": idx,
                    "name": val.name,
                }
                if val.python_hint:
                    val_info["python"] = val.python_hint
                if val.windows_path or val.unix_path:
                    val_info["windows_path"] = val.windows_path
                    val_info["unix_path"] = val.unix_path
                values.append(val_info)

            context[usage.control_file] = {
                "name": usage.resolved.name,
                "control_file": usage.control_file,
                "vilib_path": usage.vilib_path,
                "values": values,
                "used_values": list(usage.used_values),
            }

        return context

    def get_all_enums(self) -> list[EnumUsage]:
        """Get all registered enums."""
        return list(self._enums.values())

    def stats(self) -> dict:
        """Get registry statistics."""
        return {
            "enum_count": len(self._enums),
            "vis_with_enums": len(self._vi_enums),
            "total_values_used": sum(len(e.used_values) for e in self._enums.values()),
        }
