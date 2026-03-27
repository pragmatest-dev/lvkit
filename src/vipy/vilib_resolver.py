"""Resolver for LabVIEW vilib VIs (standard SubVIs from vi.lib)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vipy.graph_types import ClusterField, EnumValue, LVType


@dataclass
class ResolutionContext:
    """Context passed to VILibResolutionNeeded for diagnostics."""

    caller_vi: str | None = None
    poly_selector: str | None = None
    wire_types: list[str] = field(default_factory=list)
    terminal_names: list[str] = field(default_factory=list)


def derive_python_name(typedef_name: str) -> str:
    """Derive Python class name from typedef qualified name.

    Args:
        typedef_name: Qualified name like "sysdir.llb:System Directory Type.ctl"

    Returns:
        Python class name like "SystemDirectoryType"
    """
    # Extract filename from qualified name
    if ":" in typedef_name:
        filename = typedef_name.split(":")[-1]
    else:
        filename = typedef_name

    # Remove .ctl extension
    name = filename.replace(".ctl", "")

    # Convert to CamelCase: "System Directory Type" -> "SystemDirectoryType"
    # Replace hyphens and underscores with spaces for splitting
    name = name.replace("-", " ").replace("_", " ")
    result = "".join(word.capitalize() for word in name.split())
    # Ensure the result is a valid Python identifier
    result = "".join(c for c in result if c.isalnum() or c == "_")
    return result or "UnknownType"


def derive_python_location(typedef_name: str) -> tuple[str, str]:
    """Derive Python package and class name from qualified name.

    The qualified name determines the package structure - types belong to
    their containing library, just like VIs do.

    Args:
        typedef_name: Qualified name like "sysdir.llb:System Directory Type.ctl"

    Returns:
        Tuple of (package_name, class_name) like ("sysdir", "SystemDirectoryType")
    """
    if ":" in typedef_name:
        container, filename = typedef_name.split(":", 1)
    else:
        container = ""
        filename = typedef_name

    # Container becomes package: "sysdir.llb" -> "sysdir"
    package = container.replace(".llb", "").replace(".lvlib", "")
    package = package.replace(".lvclass", "").lower()
    package = package.replace(" ", "_").replace("-", "_")

    # Filename becomes class name
    class_name = derive_python_name(filename)

    return (package, class_name)


class VILibResolutionNeeded(Exception):
    """Raised when vi.lib terminal info is missing.

    Claude should use the VI dependencies in the files being processed
    to figure out terminal information and add Python hints based on context.
    """

    def __init__(self, vi_name: str, context: ResolutionContext | None = None):
        self.vi_name = vi_name
        self.context = context or ResolutionContext()
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"VILib resolution needed for '{self.vi_name}'.\n"

        if self.context.poly_selector:
            msg += f"\nPolymorphic selector: {self.context.poly_selector}"
            msg += "\n  (Add this to poly_selector_names in the variant's JSON entry)"

        if self.context.caller_vi:
            msg += f"\nCaller VI: {self.context.caller_vi}"

        if self.context.terminal_names:
            msg += "\n\nTerminal names from XML:\n"
            for name in self.context.terminal_names:
                msg += f"  - {name}\n"

        if self.context.wire_types:
            msg += "\n\nWire types from dataflow:\n"
            for wt in self.context.wire_types:
                msg += f"  - {wt}\n"

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
    # Polymorphic variant support
    variant_signature: str | None = None  # Signature key for this variant
    is_variant: bool = False  # True if this is a variant entry
    # Reference terminal passthrough (output_param -> "passthrough_from:input_param")
    ref_terminals: dict[str, str] | None = None
    # Alternate names for matching (e.g., polymorphic instance names)
    match_names: list[str] = Field(default_factory=list)
    # polySelector dropdown names from VI XML (exact strings)
    poly_selector_names: list[str] = Field(default_factory=list)
    # Wrapper VI name for polymorphic variants (explicit, not derived)
    base_vi: str | None = None


class VILibResolver:
    """Resolve vilib VIs to Python equivalents.

    vilib VIs are standard SubVIs that ship with LabVIEW in the vi.lib folder.
    They are identified by their path
    (e.g., "Utility/sysdir.llb/Get System Directory.vi").

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

        self.data_dir = data_dir
        self._vis: dict[str, VIEntry] = {}
        self._by_name: dict[str, VIEntry] = {}  # Lookup by VI name only
        self._pdf_entries: dict[str, dict] = {}  # Raw PDF data for context
        self._types: dict[str, LVType] = {}  # Indexed by qualified name
        self._category_files: dict[str, Path] = {}  # VI name → category file
        self._variants: dict[str, list[VIEntry]] = {}  # VI name → variants
        self._by_poly_selector: dict[tuple[str, str], VIEntry] = {}  # (base, sel)

        # Load vilib data from category files
        vilib_dir = data_dir / "vilib"
        if vilib_dir.exists():
            self._load_vilib_data(vilib_dir)

        # Load OpenG data (same format as vilib)
        openg_dir = data_dir / "openg"
        if openg_dir.exists():
            self._load_vilib_data(openg_dir)

        # Load driver data (same VIEntry schema)
        drivers_dir = data_dir / "drivers"
        if drivers_dir.exists():
            self._load_vilib_data(drivers_dir)

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
                if not entry.name.endswith(".vi"):
                    vi_name = f"{entry.name}.vi"
                else:
                    vi_name = entry.name

                # Track which file this VI came from
                self._category_files[vi_name] = category_path

                # Only add if not already present (legacy data takes priority)
                if vi_name not in self._by_name:
                    self._by_name[vi_name] = entry
                    if entry.vi_path:
                        self._vis[entry.vi_path] = entry

                # Register alternate match names
                for alt_name in entry.match_names:
                    if not alt_name.endswith(".vi"):
                        alt_name = f"{alt_name}.vi"
                    if alt_name not in self._by_name:
                        self._by_name[alt_name] = entry

                # Register polySelector name lookups
                # Key: (base_vi_name, poly_selector_name) → entry
                if entry.poly_selector_names and entry.base_vi:
                    for ps_name in entry.poly_selector_names:
                        self._by_poly_selector[(entry.base_vi, ps_name)] = entry

    def _load_types(self, types_path: Path) -> None:
        """Load type definitions from _types.json into LVType dataclasses."""
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

            # Create the LVType structure with typedef metadata
            lv_type = LVType(
                kind=type_data["kind"],
                underlying_type=type_data["underlying_type"],
                values=values,
                fields=fields,
                element_type=element_type,
                dimensions=type_data.get("dimensions"),
                typedef_path=typedef_path,
                typedef_name=typedef_path,  # Qualified name = key
                description=type_data.get("description"),
            )

            # Store LVType directly (indexed by qualified name)
            self._types[typedef_path] = lv_type

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

    def resolve_type(self, typedef_path: str) -> LVType | None:
        """Resolve a typedef path to its LVType.

        Args:
            typedef_path: Qualified name like
                "sysdir.llb:System Directory Type.ctl"

        Returns:
            LVType if found, None otherwise.
        """
        return self._types.get(typedef_path)

    def resolve(self, vilib_path: str) -> VIEntry | None:
        """Resolve a vilib path to its VI mapping.

        Args:
            vilib_path: Full vilib path like
                "Utility/sysdir.llb/Get System Directory.vi"

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

    def resolve_poly_variant(
        self, base_name: str, selector_name: str
    ) -> VIEntry | None:
        """Resolve a polymorphic VI variant by its polySelector name.

        Args:
            base_name: Base VI name like "DAQmx Create Virtual Channel.vi"
            selector_name: polySelector dropdown value from XML.
                For index-based: "poly_index:N" where N is menuInstanceUsed.

        Returns:
            VIEntry for the matching variant, or None
        """
        # Index-based: "poly_index:23" → look up by position in selector list
        if selector_name.startswith("poly_index:"):
            try:
                menu_index = int(selector_name.split(":")[1])
            except ValueError:
                return None
            return self._resolve_poly_by_index(base_name, menu_index)

        return self._by_poly_selector.get((base_name, selector_name))

    def _resolve_poly_by_index(
        self, base_name: str, menu_index: int,
    ) -> VIEntry | None:
        """Resolve polymorphic variant by menuInstanceUsed index.

        Builds the selector list (Automatic, -, variant1, variant2, ...)
        and returns the variant at the given index.
        """
        variants = self.find_variants(base_name)
        if not variants:
            return None

        # Build flat selector list matching LabVIEW's UI order
        # First 2 entries: "Automatic" and "-" separator
        selector_entries: list[VIEntry | None] = [None, None]
        for v in variants:
            sel_names = v.poly_selector_names or []
            if sel_names:
                for _ in sel_names:
                    selector_entries.append(v)
            else:
                selector_entries.append(v)

        if 0 <= menu_index < len(selector_entries):
            return selector_entries[menu_index]
        return None

    def find_variants(self, base_name: str) -> list[VIEntry]:
        """Find all variant entries for a base/wrapper VI.

        Uses the explicit base_vi field on each entry.
        """
        return [
            entry for entry in self._by_name.values()
            if entry.base_vi == base_name
        ]

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

        lines = [
            '"""Generated from vilib VI."""',
            "",
            "from __future__ import annotations",
            "",
        ]

        # Collect enum LVTypes used by this VI's terminals
        enum_typedefs: set[str] = set()
        needs_intenum = False
        for terminal in vi.terminals:
            if terminal.type and terminal.type.endswith('.ctl'):
                lv_type = self.resolve_type(terminal.type)
                if lv_type and lv_type.kind == 'enum':
                    enum_typedefs.add(terminal.type)
                    needs_intenum = True

        # Add IntEnum import if needed
        if needs_intenum:
            lines.append("from enum import IntEnum")

        lines.extend(vi.imports)
        if vi.imports or needs_intenum:
            lines.append("")

        # Generate IntEnum classes for enum types
        for typedef_path in sorted(enum_typedefs):
            lv_type = self.resolve_type(typedef_path)
            if lv_type and lv_type.values:
                # Derive Python class name from typedef_name
                class_name = (
                    derive_python_name(lv_type.typedef_name)
                    if lv_type.typedef_name
                    else "Unknown"
                )
                lines.append("")
                lines.append(f"class {class_name}(IntEnum):")
                lines.append(f'    """{lv_type.description or class_name}"""')
                for name, enum_val in lv_type.values.items():
                    if enum_val.description:
                        lines.append(
                            f"    {name} = {enum_val.value}"
                            f"  # {enum_val.description}"
                        )
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
            lv_type: LVType | None = None

            # If terminal has a typedef path, resolve it for full type info
            if t.type and t.type.endswith(".ctl"):
                lv_type = self.resolve_type(t.type)
                if lv_type:
                    # Get Python type name from typedef_name
                    if lv_type.typedef_name:
                        type_name = derive_python_name(lv_type.typedef_name)
                    underlying_type = lv_type.underlying_type
                    # Get enum values if not already set and lv_type has them
                    if enum_values is None and lv_type.values:
                        enum_values = [
                            (ev.value, name)
                            for name, ev in lv_type.values.items()
                        ]

            terminals.append({
                "index": t.index,
                "direction": t.direction,
                "name": t.name,
                "type": t.type,  # Typedef path
                "underlying_type": underlying_type,  # Base type (UInt16, etc.)
                "type_name": type_name,  # Python type name
                "enum_values": enum_values,
                "python_param": t.python_param,
                "lv_type": lv_type,  # Full LVType if resolved
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

    def _compute_signature(self, terminals: dict[int, dict[str, Any]]) -> str:
        """Compute a signature from terminal observations.

        The signature captures the terminal types at each index, which
        is what distinguishes polymorphic variants.
        """
        parts = []
        for idx in sorted(terminals.keys()):
            term = terminals[idx]
            type_str = term.get("type") or "any"
            # Simplify typedef paths to just the filename
            if "/" in type_str:
                type_str = type_str.split("/")[-1].replace(".ctl", "")
            direction = term.get("direction", "?")[0] if term.get("direction") else "?"
            parts.append(f"{idx}:{direction}:{type_str}")
        return "|".join(parts)

    def find_matching_variant(
        self,
        vi_name: str,
        observed_terminals: dict[int, dict[str, Any]],
    ) -> VIEntry | None:
        """Find the best matching variant for observed terminals.

        Args:
            vi_name: VI filename like "Get System Directory.vi"
            observed_terminals: Dict of index -> terminal info from caller

        Returns:
            Best matching VIEntry, or None if no match
        """
        # Check base entry first
        base = self.resolve_by_name(vi_name)
        if base:
            # Check if base entry matches all observed terminals
            base_map = {t.index: t for t in base.terminals if t.index is not None}
            all_match = True
            for idx, obs in observed_terminals.items():
                if idx in base_map:
                    existing = base_map[idx]
                    if (existing.name and obs.get("name") and
                            existing.name != obs.get("name")):
                        all_match = False
                        break
                    if (existing.direction and obs.get("direction") and
                            existing.direction != obs.get("direction")):
                        all_match = False
                        break
            if all_match:
                return base

        # Check variants
        if vi_name not in self._variants:
            return base  # No variants, return base even if imperfect

        best_match: VIEntry | None = None
        best_score = -1

        for variant in self._variants[vi_name]:
            variant_map = {t.index: t for t in variant.terminals if t.index is not None}
            score = 0
            mismatch = False

            for idx, obs in observed_terminals.items():
                if idx in variant_map:
                    existing = variant_map[idx]
                    # Check for conflicts
                    if (existing.name and obs.get("name") and
                            existing.name != obs.get("name")):
                        mismatch = True
                        break
                    if (existing.direction and obs.get("direction") and
                            existing.direction != obs.get("direction")):
                        mismatch = True
                        break
                    # Matching terminal adds to score
                    score += 1
                    if existing.type == obs.get("type"):
                        score += 1  # Extra point for type match

            if not mismatch and score > best_score:
                best_score = score
                best_match = variant

        return best_match or base

    def _create_variant(
        self,
        vi_name: str,
        observed_terminals: dict[int, dict[str, Any]],
        base_entry: VIEntry,
        caller_vi: str | None = None,
    ) -> VIEntry:
        """Create a new polymorphic variant from observations.

        Args:
            vi_name: VI filename
            observed_terminals: Terminal observations from caller
            base_entry: The base VI entry to clone from
            caller_vi: Name of calling VI (for tracking)

        Returns:
            Newly created variant entry
        """
        signature = self._compute_signature(observed_terminals)

        # Create variant entry
        variant = VIEntry(
            name=vi_name,
            vi_path=base_entry.vi_path,
            category=base_entry.category,
            description=f"Variant observed from {caller_vi or 'unknown'}",
            terminals=[],
            python=base_entry.python,
            inline=base_entry.inline,
            imports=base_entry.imports.copy(),
            status="auto_variant",
            variant_signature=signature,
            is_variant=True,
        )

        # Copy terminals from observed data
        for idx, obs in observed_terminals.items():
            variant.terminals.append(VITerminal(
                name=obs.get("name", ""),
                index=idx,
                direction=obs.get("direction"),
                type=obs.get("type"),
            ))

        # Store variant
        if vi_name not in self._variants:
            self._variants[vi_name] = []
        self._variants[vi_name].append(variant)

        # Save to pending for review (variants need human verification)
        self._add_variant_to_pending(vi_name, variant, caller_vi)

        return variant

    def _add_variant_to_pending(
        self,
        vi_name: str,
        variant: VIEntry,
        caller_vi: str | None,
    ) -> None:
        """Save discovered variant to _pending_terminals.json for review."""
        pending_file = self.data_dir / "vilib" / "_pending_terminals.json"

        if pending_file.exists():
            with open(pending_file) as f:
                pending_data = json.load(f)
        else:
            pending_data = {"conflicts": {}, "variants": {}}

        if "variants" not in pending_data:
            pending_data["variants"] = {}

        if vi_name not in pending_data["variants"]:
            pending_data["variants"][vi_name] = []

        variant_entry = {
            "signature": variant.variant_signature,
            "caller_vi": caller_vi,
            "terminals": [
                {"index": t.index, "name": t.name, "direction": t.direction,
                 "type": t.type}
                for t in variant.terminals
            ],
        }

        # Don't add duplicate signatures
        existing_sigs = {v.get("signature") for v in pending_data["variants"][vi_name]}
        if variant.variant_signature not in existing_sigs:
            pending_data["variants"][vi_name].append(variant_entry)

            pending_file.parent.mkdir(parents=True, exist_ok=True)
            with open(pending_file, "w") as f:
                json.dump(pending_data, f, indent=2)

    def auto_update_terminals(
        self,
        vi_name: str,
        wired_terminals: list[Any],
        caller_vi: str | None = None,
    ) -> VIEntry:
        """Auto-update VI terminals from caller observations.

        Creates new typedefs in _types.json as needed.
        On conflicts, creates a polymorphic variant instead of failing.
        Observations are always trusted - they come from actual wire connections.
        """
        vi = self.resolve_by_name(vi_name)
        if not vi:
            raise ValueError(f"VI not found: {vi_name}")

        existing_map: dict[int, VITerminal] = {
            t.index: t for t in vi.terminals if t.index is not None
        }

        observed_map: dict[int, dict[str, Any]] = {}
        for wired_term in wired_terminals:
            if wired_term.index < 0:
                continue  # Unresolved — should be resolved during graph construction

            lv_type = getattr(wired_term, 'lv_type', None)
            type_str = None

            if lv_type:
                if lv_type.kind == "typedef_ref" and lv_type.typedef_path:
                    type_str = lv_type.typedef_path
                    # Auto-create typedef if needed
                    self._ensure_typedef(lv_type)
                elif lv_type.underlying_type:
                    type_str = lv_type.underlying_type
            elif hasattr(wired_term, 'type'):
                type_str = wired_term.type

            observed_map[wired_term.index] = {
                "name": wired_term.name or "",
                "direction": wired_term.direction,
                "type": type_str,
            }

        # Check for conflicts with base entry
        has_conflict = False
        for idx, obs_data in observed_map.items():
            if idx in existing_map:
                existing = existing_map[idx]
                if (existing.name and obs_data["name"] and
                        existing.name != obs_data["name"]):
                    has_conflict = True
                    break
                if (existing.direction and obs_data["direction"] and
                        existing.direction != obs_data["direction"]):
                    has_conflict = True
                    break

        if has_conflict:
            # Conflict detected - this is likely a polymorphic variant
            # Check if we already have a matching variant
            matching = self.find_matching_variant(vi_name, observed_map)
            if matching and matching.is_variant:
                # Update existing variant with any new info
                self._update_variant_terminals(matching, observed_map)
                return matching

            # Create a new variant from observation
            return self._create_variant(vi_name, observed_map, vi, caller_vi)

        # No conflict - update base entry
        updated = False
        unmatched_obs: list[tuple[int, dict[str, Any]]] = []
        for idx, obs_data in observed_map.items():
            if idx in existing_map:
                term = existing_map[idx]
                if not term.direction and obs_data["direction"]:
                    term.direction = obs_data["direction"]
                    updated = True
                if not term.type and obs_data["type"]:
                    term.type = obs_data["type"]
                    updated = True
            else:
                # Try name-based matching first
                matched = False
                if obs_data["name"]:
                    for term in vi.terminals:
                        if term.name == obs_data["name"] and term.index is None:
                            term.index = idx
                            term.direction = obs_data["direction"]
                            term.type = obs_data["type"]
                            updated = True
                            matched = True
                            break
                if not matched:
                    unmatched_obs.append((idx, obs_data))

        # Fallback 1: Match by type when exactly one null-index terminal
        # shares the type AND direction (unambiguous type match).
        if unmatched_obs:
            null_terms = [t for t in vi.terminals if t.index is None]
            still_unmatched = []
            for idx, obs_data in unmatched_obs:
                obs_dir = obs_data["direction"]
                obs_type = obs_data["type"]
                # Try type + direction match
                candidates = [
                    t for t in null_terms
                    if t.direction == obs_dir and t.type == obs_type
                ] if obs_type else []
                if len(candidates) == 1:
                    t = candidates[0]
                    t.index = idx
                    if obs_data["direction"]:
                        t.direction = obs_data["direction"]
                    if obs_data["type"]:
                        t.type = obs_data["type"]
                    null_terms.remove(t)
                    updated = True
                else:
                    still_unmatched.append((idx, obs_data))

            # Fallback 2: Match by direction alone when exactly one
            # null-index terminal shares the direction.
            for idx, obs_data in still_unmatched:
                obs_dir = obs_data["direction"]
                candidates = [
                    t for t in null_terms
                    if t.direction == obs_dir
                ]
                if len(candidates) == 1:
                    t = candidates[0]
                    t.index = idx
                    if obs_data["direction"]:
                        t.direction = obs_data["direction"]
                    if obs_data["type"]:
                        t.type = obs_data["type"]
                    null_terms.remove(t)
                    updated = True

        if updated:
            self._save_vi_entry(vi_name, vi)

        return vi

    def _update_variant_terminals(
        self,
        variant: VIEntry,
        observed_map: dict[int, dict[str, Any]],
    ) -> None:
        """Update variant with additional terminal observations."""
        existing_indices = {t.index for t in variant.terminals if t.index is not None}
        updated = False

        for idx, obs in observed_map.items():
            if idx not in existing_indices:
                # New terminal observation
                variant.terminals.append(VITerminal(
                    name=obs.get("name", ""),
                    index=idx,
                    direction=obs.get("direction"),
                    type=obs.get("type"),
                ))
                updated = True

        if updated:
            # Update signature
            new_map = {t.index: {"name": t.name, "direction": t.direction,
                                 "type": t.type}
                       for t in variant.terminals if t.index is not None}
            variant.variant_signature = self._compute_signature(new_map)

    def _ensure_typedef(self, lv_type: LVType) -> None:
        """Create typedef in _types.json if it doesn't exist."""
        if not lv_type.typedef_path:
            return

        if lv_type.typedef_path in self._types:
            return

        # Set typedef metadata on LVType
        if not lv_type.typedef_name:
            lv_type.typedef_name = lv_type.typedef_path
        if not lv_type.description:
            lv_type.description = f"Auto-discovered typedef from {lv_type.typedef_path}"

        self._types[lv_type.typedef_path] = lv_type
        self._save_typedef(lv_type)

    def _save_typedef(self, lv_type: LVType) -> None:
        """Save typedef to _types.json."""
        types_path = self.data_dir / "vilib" / "_types.json"

        if types_path.exists():
            with open(types_path) as f:
                data = json.load(f)
        else:
            data = {}

        # Derive Python name from typedef_name
        python_name = (
            derive_python_name(lv_type.typedef_name)
            if lv_type.typedef_name
            else "Unknown"
        )

        # Serialize LVType
        type_data: dict[str, Any] = {
            "name": python_name,
            "kind": lv_type.kind,
            "underlying_type": lv_type.underlying_type,
        }

        if lv_type.description:
            type_data["description"] = lv_type.description

        if lv_type.values:
            type_data["values"] = {
                name: {
                    "value": ev.value,
                    "description": ev.description,
                }
                for name, ev in lv_type.values.items()
            }

        if lv_type.fields:
            type_data["fields"] = [
                {"name": f.name, "type": f.type.underlying_type or "Any"}
                for f in lv_type.fields
            ]

        data[lv_type.typedef_path] = type_data

        types_path.parent.mkdir(parents=True, exist_ok=True)
        with open(types_path, "w") as f:
            json.dump(data, f, indent=2)

    def _save_vi_entry(self, vi_name: str, vi: VIEntry) -> None:
        """Save updated VI to category JSON."""
        category_file = self._category_files.get(vi_name)
        if not category_file:
            return

        with open(category_file) as f:
            data = json.load(f)

        for i, entry in enumerate(data.get("entries", [])):
            entry_name = entry.get("name", "")
            if not entry_name.endswith(".vi"):
                entry_vi_name = f"{entry_name}.vi"
            else:
                entry_vi_name = entry_name
            if entry_vi_name == vi_name:
                data["entries"][i] = json.loads(
                    vi.model_dump_json(exclude_none=True)
                )
                break

        with open(category_file, "w") as f:
            json.dump(data, f, indent=2)

    def _add_to_pending(
        self,
        vi_name: str,
        caller_vi: str | None,
        conflicts: list[dict[str, Any]],
        observed_map: dict[int, dict[str, Any]],
        existing_map: dict[int, VITerminal],
    ) -> None:
        """Save conflict to _pending_terminals.json."""
        pending_file = self.data_dir / "vilib" / "_pending_terminals.json"

        if pending_file.exists():
            with open(pending_file) as f:
                pending_data = json.load(f)
        else:
            pending_data = {"conflicts": {}}

        if vi_name not in pending_data["conflicts"]:
            pending_data["conflicts"][vi_name] = []

        conflict_entry = {
            "caller_vi": caller_vi,
            "conflicts": conflicts,
            "observed": {k: v for k, v in observed_map.items()},
            "existing": {
                k: {"name": v.name, "direction": v.direction, "type": v.type}
                for k, v in existing_map.items()
            },
        }

        pending_data["conflicts"][vi_name].append(conflict_entry)

        pending_file.parent.mkdir(parents=True, exist_ok=True)
        with open(pending_file, "w") as f:
            json.dump(pending_data, f, indent=2)


class VILibConflict(Exception):
    """Terminal conflict detected across callers."""

    def __init__(self, vi_name: str, conflicts: list[dict[str, Any]]):
        self.vi_name = vi_name
        self.conflicts = conflicts
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"Terminal conflict for '{self.vi_name}'.\n\nConflicts:\n"
        for c in self.conflicts:
            msg += f"  Index {c['index']} ({c['field']}): "
            msg += f"{c['existing']} → {c['observed']}\n"
        msg += "\nSee data/vilib/_pending_terminals.json"
        return msg


# Module-level singleton
_resolver: VILibResolver | None = None


def get_resolver() -> VILibResolver:
    """Get the global VILibResolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = VILibResolver()
    return _resolver


def reset_resolver() -> None:
    """Reset resolver (for testing)."""
    global _resolver
    _resolver = None
