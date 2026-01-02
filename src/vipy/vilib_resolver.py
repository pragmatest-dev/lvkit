"""Resolver for LabVIEW vilib VIs (standard SubVIs from vi.lib)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vipy.graph_types import ClusterField, EnumValue, LVType, TypeDef


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


class VITerminal(BaseModel):
    """A terminal on a vilib VI."""
    name: str = ""
    index: int | None = None
    direction: str | None = None  # None = unknown, must come from observation
    type: str | None = None
    enum: str | None = None
    enum_values: list[tuple[int, str]] | None = None
    python_param: str | None = None


class VIEntry(BaseModel):
    """A vilib/openg VI entry from JSON."""
    name: str = ""
    vi_path: str | None = None
    category: str | None = None
    description: str | None = None
    terminals: list[VITerminal] = Field(default_factory=list)
    python: str = ""
    python_code: str | None = None
    inline: bool = False
    imports: list[str] = Field(default_factory=list)
    status: str = "needs_review"
    page: int | None = None


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

        self._vis: dict[str, VIEntry] = {}
        self._by_name: dict[str, VIEntry] = {}  # Lookup by VI name only
        self._pdf_entries: dict[str, dict] = {}  # Raw PDF data for context
        self._types: dict[str, TypeDef] = {}  # Type definitions indexed by typedef path

        # Load vilib data from category files
        vilib_dir = data_dir / "vilib"
        if vilib_dir.exists():
            self._load_vilib_data(vilib_dir)

        # Load OpenG data (same format as vilib)
        openg_dir = data_dir / "openg"
        if openg_dir.exists():
            self._load_vilib_data(openg_dir)

        # Load type definitions (indexed by typedef path)
        types_path = vilib_dir / "_types.json"
        if types_path.exists():
            self._load_types(types_path)

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

            for entry_data in data.get("entries", []):
                # Parse JSON into typed Pydantic model
                entry = VIEntry.model_validate(entry_data)
                if not entry.name:
                    continue

                # Apply default category if not set
                if not entry.category:
                    entry.category = category

                # Store raw data for context in exceptions
                self._pdf_entries[entry.name] = entry_data

                # Create VI name with .vi extension for lookup
                vi_name = f"{entry.name}.vi" if not entry.name.endswith(".vi") else entry.name

                # Only add if not already present (legacy data takes priority)
                if vi_name not in self._by_name:
                    self._by_name[vi_name] = entry
                    if entry.vi_path:
                        self._vis[entry.vi_path] = entry

    def _load_types(self, types_path: Path) -> None:
        """Load type definitions from _types.json into TypeDef dataclasses."""
        with open(types_path) as f:
            raw_types = json.load(f)

        for typedef_path, type_data in raw_types.items():
            # Parse enum values if present
            values: dict[str, EnumValue] | None = None
            if "values" in type_data:
                values = {}
                for name, val_data in type_data["values"].items():
                    values[name] = EnumValue(
                        value=val_data["value"],
                        description=val_data.get("description"),
                    )

            # Parse cluster fields if present
            fields: list[ClusterField] | None = None
            if "fields" in type_data:
                fields = [
                    ClusterField(
                        name=f["name"],
                        type=self._parse_field_type(f["type"])
                    )
                    for f in type_data["fields"]
                ]

            # Parse array element type if present
            element_type: LVType | None = None
            if "element_type" in type_data:
                element_type = self._parse_field_type(type_data["element_type"])

            # Create the LVType structure
            lv_type = LVType(
                kind=type_data["kind"],
                underlying_type=type_data["underlying_type"],
                values=values,
                fields=fields,
                element_type=element_type,
                dimensions=type_data.get("dimensions"),
            )

            # Wrap in TypeDef with path metadata
            self._types[typedef_path] = TypeDef(
                type=lv_type,
                typedef_path=typedef_path,
                name=type_data["name"],
                description=type_data.get("description"),
            )

    def _parse_field_type(self, type_spec: str) -> LVType:
        """Parse a field type specification into an LVType.

        Args:
            type_spec: Either a primitive type name (e.g., "NumInt32") or
                      a typedef path (e.g., "vi.lib/Utility/sysdir.llb/Type.ctl")

        Returns:
            LVType - either a primitive or a typedef_ref
        """
        if type_spec.endswith(".ctl"):
            # It's a typedef reference - lazy resolution
            return LVType(kind="typedef_ref", typedef_path=type_spec)
        else:
            # It's a primitive type
            return LVType(kind="primitive", underlying_type=type_spec)

    def resolve_type(self, typedef_path: str) -> TypeDef | None:
        """Resolve a typedef path to its TypeDef dataclass.

        Args:
            typedef_path: Full typedef path like "vi.lib/Utility/sysdir.llb/System Directory Type.ctl"

        Returns:
            TypeDef dataclass if found, None otherwise.
        """
        return self._types.get(typedef_path)

    def resolve(self, vilib_path: str) -> VIEntry | None:
        """Resolve a vilib path to its VI mapping.

        Args:
            vilib_path: Full vilib path like "Utility/sysdir.llb/Get System Directory.vi"

        Returns:
            VIEntry if found, None otherwise
        """
        return self._vis.get(vilib_path)

    def resolve_by_name(self, vi_name: str) -> VIEntry | None:
        """Resolve a VI by its filename only.

        Args:
            vi_name: VI filename like "Get System Directory.vi"

        Returns:
            VIEntry if found, None otherwise
        """
        return self._by_name.get(vi_name)

    def has_implementation(self, vi_name: str) -> bool:
        """Check if we have a full Python implementation (module) for a VI."""
        vi = self.resolve_by_name(vi_name)
        return vi is not None and vi.python_code is not None and not vi.inline

    def has_inline(self, vi_name: str) -> bool:
        """Check if we have inline Python code for a VI (inlined at call sites)."""
        vi = self.resolve_by_name(vi_name)
        return vi is not None and vi.python_code is not None and vi.inline

    def get_implementation(self, vi_name: str) -> str | None:
        """Get the Python implementation for a vilib VI (non-inline only).

        Args:
            vi_name: VI filename like "Get System Directory.vi"

        Returns:
            Python code string if available, None otherwise
        """
        vi = self.resolve_by_name(vi_name)
        if not vi or not vi.python_code or vi.inline:
            return None

        lines = ['"""Generated from vilib VI."""', "", "from __future__ import annotations", ""]

        # Collect enum typedefs used by this VI's terminals
        enum_typedefs: set[str] = set()
        needs_intenum = False
        for terminal in vi.terminals:
            if terminal.type and terminal.type.endswith('.ctl'):
                typedef = self.resolve_type(terminal.type)
                if typedef and typedef.type.kind == 'enum':
                    enum_typedefs.add(terminal.type)
                    needs_intenum = True

        # Add IntEnum import if needed
        if needs_intenum:
            lines.append("from enum import IntEnum")

        lines.extend(vi.imports)
        if vi.imports or needs_intenum:
            lines.append("")

        # Generate IntEnum classes for typedef enums
        for typedef_path in sorted(enum_typedefs):
            typedef = self.resolve_type(typedef_path)
            if typedef and typedef.type.values:
                lines.append("")
                lines.append(f"class {typedef.name}(IntEnum):")
                lines.append(f'    """{typedef.description or typedef.name}"""')
                for name, enum_val in typedef.type.values.items():
                    if enum_val.description:
                        lines.append(f"    {name} = {enum_val.value}  # {enum_val.description}")
                    else:
                        lines.append(f"    {name} = {enum_val.value}")

        lines.append("")
        lines.append(vi.python_code)
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

        terminals = []
        for t in vi.terminals:
            # Start with terminal's own values
            enum_values = t.enum_values
            type_name = t.enum  # Python type name (e.g., "SystemDirectoryType")
            underlying_type = None
            typedef: TypeDef | None = None

            # If terminal has a typedef path, resolve it for full type info
            if t.type and t.type.endswith(".ctl"):
                typedef = self.resolve_type(t.type)
                if typedef:
                    # Get Python type name from resolved typedef
                    type_name = typedef.name or type_name
                    underlying_type = typedef.type.underlying_type
                    # Get enum values if not already set and typedef has them
                    if enum_values is None and typedef.type.values:
                        enum_values = [(ev.value, name) for name, ev in typedef.type.values.items()]

            terminals.append({
                "index": t.index,
                "direction": t.direction,
                "name": t.name,
                "type": t.type,  # Typedef path
                "underlying_type": underlying_type,  # Base type (UInt16, etc.)
                "type_name": type_name,  # Python type name
                "enum_values": enum_values,
                "python_param": t.python_param,
                "typedef": typedef,  # Full TypeDef dataclass if resolved
            })

        return {
            "name": vi.name,
            "vi_path": vi.vi_path,
            "terminals": terminals,
            "python": vi.python,
            "python_code": vi.python_code,
            "inline": vi.inline,
            "has_implementation": vi.python_code is not None and not vi.inline,
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
