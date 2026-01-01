"""Resolver for LabVIEW vilib VIs (standard SubVIs from vi.lib)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class VILibResolutionNeeded(Exception):
    """Raised when vi.lib terminal info is missing.

    Claude should use the VI dependencies in the files being processed
    to figure out terminal information and add Python hints based on context.
    """

    def __init__(self, vi_name: str, context: dict[str, Any] | None = None):
        self.vi_name = vi_name
        self.context = context or {}
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"VILib resolution needed for '{self.vi_name}'.\n"

        if self.context.get("caller_vi"):
            msg += f"\nCaller VI: {self.context['caller_vi']}"

        if self.context.get("terminal_names"):
            msg += "\n\nTerminal names from XML:\n"
            for name in self.context["terminal_names"]:
                msg += f"  - {name}\n"

        if self.context.get("wire_types"):
            msg += "\n\nWire types from dataflow:\n"
            for wt in self.context["wire_types"]:
                msg += f"  - {wt}\n"

        if self.context.get("pdf_data"):
            pdf = self.context["pdf_data"]
            msg += f"\n\nPDF documentation (page {pdf.get('page', '?')}):\n"
            msg += f"  Description: {pdf.get('description', 'N/A')[:200]}...\n"
            if pdf.get("terminals"):
                msg += "  Known terminals:\n"
                for t in pdf["terminals"]:
                    msg += f"    - {t.get('name', '?')} ({t.get('direction', '?')})\n"

        msg += "\nPlease add terminal info to data/vilib/<category>.json"
        return msg


@dataclass
class VILibTerminal:
    """A terminal on a vilib VI."""
    index: int | None  # None if not yet resolved
    direction: str  # "in" or "out"
    name: str
    type: str | None = None
    enum: str | None = None  # Enum type name if this terminal uses an enum
    enum_values: list[tuple[int, str]] | None = None  # Enum values if extracted from PDF
    python_param: str | None = None  # Python parameter name if different from name


@dataclass
class VILibVI:
    """A vilib VI with its Python mapping."""
    name: str
    vilib_path: str  # e.g., "Utility/sysdir.llb/Get System Directory.vi"
    terminals: list[VILibTerminal] = field(default_factory=list)
    python: str = ""  # Usage hint like "get_system_directory(directory_type)"
    python_impl: str | None = None  # Full implementation if available
    python_inline: str | None = None  # Inline template like "os.makedirs({path}, exist_ok=True)"
    imports: list[str] = field(default_factory=list)
    inline_imports: list[str] = field(default_factory=list)  # Imports for inline code
    doc_url: str | None = None
    category: str | None = None  # Category like "openg/file" - used as output folder
    status: str = "needs_review"  # needs_review, needs_terminals, complete
    pdf_page: int | None = None  # Page number in PDF reference


class VILibResolver:
    """Resolve vilib VIs to Python equivalents.

    vilib VIs are standard SubVIs that ship with LabVIEW in the vi.lib folder.
    They are identified by their path (e.g., "Utility/sysdir.llb/Get System Directory.vi").

    Loads from two sources:
    1. data/vilib-vis.json - Hand-curated VIs with complete Python implementations
    2. data/vilib/*.json - PDF-extracted VIs with terminal info (fallback)
    """

    def __init__(self, data_dir: Path | None = None):
        """Initialize resolver with vilib VI mappings.

        Args:
            data_dir: Path to data directory. If None, uses default location.
        """
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent / "data"

        self._vis: dict[str, VILibVI] = {}
        self._by_name: dict[str, VILibVI] = {}  # Lookup by VI name only
        self._pdf_entries: dict[str, dict] = {}  # Raw PDF data for context
        self._enums: dict[str, dict] = {}  # Enum definitions

        # Load vilib data from category files
        vilib_dir = data_dir / "vilib"
        if vilib_dir.exists():
            self._load_vilib_data(vilib_dir)

        # Load OpenG data (same format as vilib)
        openg_dir = data_dir / "openg"
        if openg_dir.exists():
            self._load_vilib_data(openg_dir)

        # Load enums
        enums_path = vilib_dir / "_enums.json"
        if enums_path.exists():
            with open(enums_path) as f:
                self._enums = json.load(f)

    def _load_vilib_data(self, vilib_dir: Path) -> None:
        """Load VI mappings from category files in data/vilib/."""
        index_path = vilib_dir / "_index.json"
        if not index_path.exists():
            return

        with open(index_path) as f:
            index = json.load(f)

        for category, filename in index.get("categories", {}).items():
            category_path = vilib_dir / filename
            if not category_path.exists():
                continue

            with open(category_path) as f:
                data = json.load(f)

            for entry in data.get("entries", []):
                name = entry.get("name", "")
                if not name:
                    continue

                # Store raw PDF data for context in exceptions
                self._pdf_entries[name] = entry

                terminals = []
                for t in entry.get("terminals", []):
                    terminals.append(
                        VILibTerminal(
                            index=t.get("index"),  # None if not resolved yet
                            direction=t.get("direction", "in"),
                            name=t.get("name", ""),
                            type=t.get("type"),
                            enum=t.get("enum"),
                            enum_values=t.get("enum_values"),
                            python_param=t.get("python_param"),
                        )
                    )

                # Create VI name with .vi extension for lookup
                vi_name = f"{name}.vi" if not name.endswith(".vi") else name

                vi = VILibVI(
                    name=name,
                    vilib_path=entry.get("vi_path") or f"vi.lib/{category}/{name}.vi",
                    terminals=terminals,
                    python=entry.get("python", ""),
                    python_impl=entry.get("python_impl"),
                    python_inline=entry.get("python_inline"),
                    imports=entry.get("imports", []),
                    inline_imports=entry.get("inline_imports", []),
                    category=entry.get("category") or category,  # Entry can override
                    status=entry.get("status", "needs_review"),
                    pdf_page=entry.get("page"),
                )

                # Only add if not already present (legacy data takes priority)
                if vi_name not in self._by_name:
                    self._by_name[vi_name] = vi
                    if vi.vilib_path:
                        self._vis[vi.vilib_path] = vi

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
