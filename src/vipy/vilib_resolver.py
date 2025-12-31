"""Resolver for LabVIEW vilib VIs (standard SubVIs from vi.lib)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VILibTerminal:
    """A terminal on a vilib VI."""
    index: int
    direction: str  # "in" or "out"
    name: str
    type: str | None = None
    enum: str | None = None  # Enum type name if this terminal uses an enum
    python_param: str | None = None  # Python parameter name if different from name


@dataclass
class VILibVI:
    """A vilib VI with its Python mapping."""
    name: str
    vilib_path: str  # e.g., "Utility/sysdir.llb/Get System Directory.vi"
    terminals: list[VILibTerminal] = field(default_factory=list)
    python: str = ""  # Usage hint like "get_system_directory(directory_type)"
    python_impl: str | None = None  # Full implementation if available
    imports: list[str] = field(default_factory=list)
    doc_url: str | None = None


class VILibResolver:
    """Resolve vilib VIs to Python equivalents.

    vilib VIs are standard SubVIs that ship with LabVIEW in the vi.lib folder.
    They are identified by their path (e.g., "Utility/sysdir.llb/Get System Directory.vi").
    """

    def __init__(self, data_path: Path | None = None):
        """Initialize resolver with vilib VI mappings.

        Args:
            data_path: Path to vilib-vis.json file. If None, uses default location.
        """
        if data_path is None:
            data_path = Path(__file__).parent.parent.parent / "data" / "vilib-vis.json"

        self._vis: dict[str, VILibVI] = {}
        self._by_name: dict[str, VILibVI] = {}  # Lookup by VI name only
        self._enums: dict[str, dict] = {}  # Enum definitions

        if data_path.exists():
            self._load(data_path)

    def _load(self, path: Path) -> None:
        """Load VI mappings from JSON file."""
        with open(path) as f:
            data = json.load(f)

        # Load enum definitions
        self._enums = data.get("enums", {})

        for vilib_path, info in data.get("vis", {}).items():
            terminals = [
                VILibTerminal(
                    index=t["index"],
                    direction=t["direction"],
                    name=t["name"],
                    type=t.get("type"),
                    enum=t.get("enum"),  # Load enum reference
                    python_param=t.get("python_param"),  # Python parameter name
                )
                for t in info.get("terminals", [])
            ]

            vi = VILibVI(
                name=info["name"],
                vilib_path=vilib_path,
                terminals=terminals,
                python=info.get("python", ""),
                python_impl=info.get("python_impl"),
                imports=info.get("imports", []),
                doc_url=info.get("doc_url"),
            )
            self._vis[vilib_path] = vi

            # Also index by VI name for easier lookup
            vi_name = vilib_path.split("/")[-1]
            self._by_name[vi_name] = vi

    def get_enums(self) -> dict[str, dict]:
        """Get all enum definitions.

        Returns:
            Dict mapping enum name -> {description, values: {name: {value, description}}}
        """
        return self._enums

    def resolve(self, vilib_path: str) -> VILibVI | None:
        """Resolve a vilib path to its VI mapping.

        Args:
            vilib_path: Full vilib path like "Utility/sysdir.llb/Get System Directory.vi"

        Returns:
            VILibVI if found, None otherwise
        """
        return self._vis.get(vilib_path)

    def resolve_by_name(self, vi_name: str) -> VILibVI | None:
        """Resolve a VI by its filename only.

        Args:
            vi_name: VI filename like "Get System Directory.vi"

        Returns:
            VILibVI if found, None otherwise
        """
        return self._by_name.get(vi_name)

    def has_implementation(self, vi_name: str) -> bool:
        """Check if we have a Python implementation for a VI."""
        vi = self.resolve_by_name(vi_name)
        return vi is not None and vi.python_impl is not None

    def get_implementation(self, vi_name: str) -> str | None:
        """Get the Python implementation for a vilib VI.

        Args:
            vi_name: VI filename like "Get System Directory.vi"

        Returns:
            Python code string if available, None otherwise
        """
        vi = self.resolve_by_name(vi_name)
        if not vi or not vi.python_impl:
            return None

        lines = ['"""Generated from vilib VI."""', "", "from __future__ import annotations", ""]
        lines.extend(vi.imports)
        if vi.imports:
            lines.append("")
        lines.append("")
        lines.append(vi.python_impl)
        return "\n".join(lines)

    def get_context(self, vi_name: str) -> dict[str, Any] | None:
        """Get context for LLM code generation.

        Args:
            vi_name: VI filename like "Get System Directory.vi"

        Returns:
            Dict with name, terminals, python hint, etc.
        """
        vi = self.resolve_by_name(vi_name)
        if not vi:
            return None

        return {
            "name": vi.name,
            "vilib_path": vi.vilib_path,
            "terminals": [
                {
                    "index": t.index,
                    "direction": t.direction,
                    "name": t.name,
                    "type": t.type,
                    "enum": t.enum,
                }
                for t in vi.terminals
            ],
            "python": vi.python,
            "has_implementation": vi.python_impl is not None,
            "imports": vi.imports,
        }

    def list_vis(self) -> list[str]:
        """List all known vilib VI names."""
        return list(self._by_name.keys())


# Module-level singleton
_resolver: VILibResolver | None = None


def get_resolver() -> VILibResolver:
    """Get the global VILibResolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = VILibResolver()
    return _resolver
