"""LabVIEW enum/typedef resolver.

Resolves LabVIEW ring constants and typedef enums to Python equivalents.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from ._data import data_dir as _bundled_data_dir


@dataclass
class EnumValue:
    """A single enum value with OS-specific paths or Python equivalents."""

    index: int
    name: str
    description: str = ""
    windows_path: str = ""
    unix_path: str = ""
    python_hint: str = ""  # Python equivalent code/value

    def get_path(self) -> Path:
        """Get the path for the current OS (for path-based enums)."""
        if platform.system() == "Windows":
            return Path(os.path.expandvars(self.windows_path))
        else:
            return Path(os.path.expandvars(self.unix_path))

    def get_python(self) -> str:
        """Get the Python equivalent for this value."""
        return self.python_hint


@dataclass
class ResolvedEnum:
    """A resolved LabVIEW enum/typedef."""

    name: str
    vilib_path: str  # e.g., "Utility/sysdir.llb"
    control_file: str  # e.g., "System Directory Type.ctl"
    values: dict[int, EnumValue]
    source: str = ""

    def get_value(self, index: int) -> EnumValue | None:
        """Get enum value by index."""
        return self.values.get(index)

    def get_path(self, index: int) -> Path | None:
        """Get OS-specific path for index (for path-based enums)."""
        val = self.values.get(index)
        if val:
            return val.get_path()
        return None

    def to_python_enum(self) -> type[IntEnum]:
        """Generate a Python IntEnum class for this typedef."""
        members = {
            self._to_python_name(v.name): v.index
            for v in self.values.values()
        }
        enum_cls: type[IntEnum] = IntEnum(self._to_python_name(self.name), members)  # type: ignore[assignment]
        return enum_cls

    @staticmethod
    def _to_python_name(name: str) -> str:
        """Convert LabVIEW name to Python identifier."""
        result = name.upper().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and result[0].isdigit():
            result = "_" + result
        return result or "UNKNOWN"


class EnumResolver:
    """Resolves LabVIEW enums/typedefs."""

    def __init__(self, enums_path: Path | str | None = None):
        """Load enum definitions.

        Args:
            enums_path: Path to labview-enums.json
        """
        if enums_path is None:
            enums_path = (
                _bundled_data_dir() / "labview-enums.json"
            )

        self._by_full_path: dict[str, ResolvedEnum] = {}  # vilib_path:control_file
        self._by_name: dict[str, ResolvedEnum] = {}  # normalized name
        self._by_control: dict[str, ResolvedEnum] = {}  # control_file only

        self._load(Path(enums_path))

    def _load(self, path: Path) -> None:
        """Load and index enums."""
        if not path.exists():
            return

        with open(path) as f:
            data = json.load(f)

        for full_path, typedef_data in data.get("typedefs", {}).items():
            values = {}
            for idx_str, val_data in typedef_data.get("values", {}).items():
                idx = int(idx_str)
                values[idx] = EnumValue(
                    index=idx,
                    name=val_data.get("name", f"Value_{idx}"),
                    description=val_data.get("description", ""),
                    windows_path=val_data.get("windows", ""),
                    unix_path=val_data.get("unix", ""),
                    python_hint=val_data.get("python", ""),
                )

            resolved = ResolvedEnum(
                name=typedef_data.get("name", ""),
                vilib_path=typedef_data.get("vilib_path", ""),
                control_file=typedef_data.get("control_file", ""),
                values=values,
                source=typedef_data.get("source", ""),
            )

            # Index by multiple keys
            self._by_full_path[full_path] = resolved
            self._by_name[self._normalize_name(resolved.name)] = resolved
            self._by_control[resolved.control_file] = resolved

    def _normalize_name(self, name: str) -> str:
        """Normalize enum name for lookup."""
        return name.lower().replace(" ", "_").replace("-", "_")

    def resolve(
        self,
        name: str | None = None,
        control_file: str | None = None,
        vilib_path: str | None = None,
    ) -> ResolvedEnum | None:
        """Resolve an enum by name, control file, or vilib path.

        Args:
            name: Enum name (e.g., "System Directory Type")
            control_file: Control file name (e.g., "System Directory Type.ctl")
            vilib_path: Full vilib path (e.g., "Utility/sysdir.llb:SystemDirType.ctl")

        Returns:
            ResolvedEnum or None
        """
        # Try full path first
        if vilib_path:
            if vilib_path in self._by_full_path:
                return self._by_full_path[vilib_path]
            # Try with .ctl extension
            if not vilib_path.endswith(".ctl"):
                vilib_path_ctl = vilib_path + ".ctl"
                if vilib_path_ctl in self._by_full_path:
                    return self._by_full_path[vilib_path_ctl]

        # Try control file
        if control_file:
            if control_file in self._by_control:
                return self._by_control[control_file]
            # Try adding .ctl
            if not control_file.endswith(".ctl"):
                if control_file + ".ctl" in self._by_control:
                    return self._by_control[control_file + ".ctl"]

        # Try name
        if name:
            norm_name = self._normalize_name(name)
            if norm_name in self._by_name:
                return self._by_name[norm_name]

        return None

    def get_all_enums(self) -> list[ResolvedEnum]:
        """Get all known enums."""
        return list(self._by_full_path.values())

    def stats(self) -> dict:
        """Get resolver statistics."""
        return {
            "enum_count": len(self._by_full_path),
            "by_name": len(self._by_name),
            "by_control": len(self._by_control),
        }


# Global instance
_resolver: EnumResolver | None = None


def get_enum_resolver() -> EnumResolver:
    """Get global enum resolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = EnumResolver()
    return _resolver


def resolve_enum(
    name: str | None = None,
    control_file: str | None = None,
    vilib_path: str | None = None,
) -> ResolvedEnum | None:
    """Convenience function for resolving enums."""
    return get_enum_resolver().resolve(name, control_file, vilib_path)


def get_system_directory(index: int) -> Path:
    """Get system directory path by index.

    This is a convenience function for the common System Directory Type enum.

    Args:
        index: Directory type index (0-13)

    Returns:
        Path to the directory for the current OS
    """
    enum = resolve_enum(name="System Directory Type")
    if enum:
        path = enum.get_path(index)
        if path:
            return path
    # Fallback
    return Path.home()


# Generate Python enum for System Directory Type
def _generate_system_directory_enum() -> type[IntEnum]:
    """Generate SystemDirectoryType enum."""
    enum = resolve_enum(name="System Directory Type")
    if enum:
        return enum.to_python_enum()
    # Fallback
    return IntEnum("SystemDirectoryType", {"USER_HOME": 0})


# Export the enum class
SystemDirectoryType = _generate_system_directory_enum()
