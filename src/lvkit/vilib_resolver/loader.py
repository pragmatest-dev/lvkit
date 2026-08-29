"""Data-loading mixin: builds the vilib/openg/drivers VI and type catalog.

Loads from:
1. data/vilib-vis.json - Hand-curated VIs with complete Python implementations
2. data/vilib/*.json - PDF-extracted VIs with terminal info (fallback)
"""

from __future__ import annotations

import json
from pathlib import Path

from lvkit._data import data_dir as _bundled_data_dir
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind

from .models import VIEntry


class _VILibLoaderMixin:
    """Loads vilib VI mappings and type definitions from JSON."""

    def __init__(
        self,
        data_dir: Path | None = None,
        project_data_dir: Path | None = None,
    ):
        """Initialize resolver with vilib VI mappings.

        Args:
            data_dir: Path to shipped data directory. If None, uses default
                location relative to this package.
            project_data_dir: Optional project-local .lvkit/ directory.
                Loaded BEFORE shipped data so project entries take priority
                via the existing "first wins" semantics in _load_vilib_data.
        """
        if data_dir is None:
            data_dir = _bundled_data_dir()

        self.data_dir = data_dir
        self._vis: dict[str, VIEntry] = {}
        self._by_name: dict[str, VIEntry] = {}  # Lookup by VI name only
        self._pdf_entries: dict[str, dict] = {}  # Raw PDF data for context
        self._types: dict[str, LVType] = {}  # Indexed by qualified name
        self._category_files: dict[str, Path] = {}  # VI name → category file
        self._variants: dict[str, list[VIEntry]] = {}  # VI name → variants
        self._by_poly_selector: dict[tuple[str, str], VIEntry] = {}  # (base, sel)

        # Project data wins: load project subdirs first. _load_vilib_data
        # has "only add if not present" semantics, so first-loaded entries
        # take priority over shipped equivalents.
        if project_data_dir is not None:
            for subdir in ("vilib", "openg", "drivers"):
                project_sub = project_data_dir / subdir
                if project_sub.exists():
                    self._load_vilib_data(project_sub)

        # Load shipped vilib data from category files
        vilib_dir = data_dir / "vilib"
        if vilib_dir.exists():
            self._load_vilib_data(vilib_dir)

        # Load shipped OpenG data (same format as vilib)
        openg_dir = data_dir / "openg"
        if openg_dir.exists():
            self._load_vilib_data(openg_dir)

        # Load shipped driver data (same VIEntry schema)
        drivers_dir = data_dir / "drivers"
        if drivers_dir.exists():
            self._load_vilib_data(drivers_dir)

        # Load type definitions (indexed by typedef path).
        # Project types win — load project _types.json before shipped.
        if project_data_dir is not None:
            project_types = project_data_dir / "vilib" / "_types.json"
            if project_types.exists():
                self._load_types(project_types)
        types_path = vilib_dir / "_types.json"
        if types_path.exists():
            self._load_types(types_path)

    def clear(self) -> None:
        """Empty the data-bearing lookup tables.

        Used by tests to simulate a resolver with no mappings. Clears
        the seven caches populated from data/vilib, data/openg, and
        data/drivers JSON files: ``_vis``, ``_by_name``, ``_pdf_entries``,
        ``_types``, ``_category_files``, ``_variants``,
        ``_by_poly_selector``.

        ``data_dir`` is intentionally preserved — it's a configured
        path, not a cache.

        If a new data cache is added later, clear it here too.
        """
        self._vis.clear()
        self._by_name.clear()
        self._pdf_entries.clear()
        self._types.clear()
        self._category_files.clear()
        self._variants.clear()
        self._by_poly_selector.clear()

    def _load_vilib_data(self, vilib_dir: Path) -> None:
        """Load VI mappings from category files in data/vilib/."""
        index_path = vilib_dir / "_index.json"
        if not index_path.exists():
            return

        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)

        for category, filename in index.get("categories", {}).items():
            category_path = vilib_dir / filename
            if not category_path.exists():
                continue

            with open(category_path, encoding="utf-8") as f:
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
        """Load type definitions from _types.json into LVType dataclasses.

        First-loaded wins: if a type with this qualified name is already
        loaded (e.g., from a project _types.json loaded first), the new
        definition is skipped. This keeps project overrides authoritative.
        """
        with open(types_path, encoding="utf-8") as f:
            raw_types = json.load(f)

        for typedef_path, type_data in raw_types.items():
            if typedef_path in self._types:
                continue
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
                    ClusterField(name=f["name"], type=self._parse_field_type(f["type"]))
                    for f in type_data["fields"]
                ]

            # Parse array element type if present
            element_type: LVType | None = None
            if "element_type" in type_data:
                element_type = self._parse_field_type(type_data["element_type"])

            # Create the LVType structure with typedef metadata
            lv_type = LVType(
                kind=LVTypeKind(type_data["kind"]),
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
            return LVType(kind=LVTypeKind.TYPEDEF_REF, typedef_path=type_spec)
        else:
            # It's a primitive type
            return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=type_spec)
