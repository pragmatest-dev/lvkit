"""Terminal-index resolution and implementation lookup for vilib VIs."""

from __future__ import annotations

from typing import Any

from lvkit.models import LVType, LVTypeKind

from .models import VIEntry
from .naming import derive_python_name


class _VILibLookupMixin:
    """Resolve vilib VIs/typedefs by path or name, and read their content."""

    # Instance attributes populated by _VILibLoaderMixin.__init__. Declared
    # here (annotation only, no assignment — zero runtime effect) so pyright
    # can type-check attribute access within this mixin.
    _types: dict[str, LVType]
    _vis: dict[str, VIEntry]
    _by_name: dict[str, VIEntry]
    _by_poly_selector: dict[tuple[str, str], VIEntry]

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
        self,
        base_name: str,
        menu_index: int,
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
        return [entry for entry in self._by_name.values() if entry.base_vi == base_name]

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
            if terminal.type and terminal.type.endswith(".ctl"):
                lv_type = self.resolve_type(terminal.type)
                if lv_type and lv_type.kind == LVTypeKind.ENUM:
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
                            f"    {name} = {enum_val.value}  # {enum_val.description}"
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
                            (ev.value, name) for name, ev in lv_type.values.items()
                        ]

            terminals.append(
                {
                    "index": t.index,
                    "direction": t.direction,
                    "name": t.name,
                    "type": t.type,  # Typedef path
                    "underlying_type": underlying_type,  # Base type (UInt16, etc.)
                    "type_name": type_name,  # Python type name
                    "enum_values": enum_values,
                    "python_param": t.python_param,
                    "lv_type": lv_type,  # Full LVType if resolved
                }
            )

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
