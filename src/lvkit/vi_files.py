"""The on-disk file kinds that hold a VI.

LabVIEW stores a VI in three file types that share one binary layout: ``.vi``,
``.vim`` (malleable VI) and ``.vit`` (VI template). Every "is this a VI file?"
question routes through here so the three are never listed twice.
"""

from __future__ import annotations

from pathlib import Path, PurePath

VI_EXTENSIONS: tuple[str, ...] = (".vi", ".vim", ".vit")


def is_vi_file(name_or_path: str | PurePath) -> bool:
    """True when the name or path ends in a VI file extension (any case)."""
    return str(name_or_path).lower().endswith(VI_EXTENSIONS)


def strip_vi_extension(name: str) -> str:
    """``name`` without a trailing VI file extension (any case); unchanged when
    it has none."""
    if is_vi_file(name):
        return name[: name.rindex(".")]
    return name


def rglob_vi_files(root: Path) -> list[Path]:
    """Every VI file under ``root`` (recursive), sorted so load order never
    depends on the filesystem."""
    return sorted(p for ext in VI_EXTENSIONS for p in root.rglob(f"*{ext}"))


def glob_vi_files(root: Path) -> list[Path]:
    """Every VI file directly in ``root``, sorted."""
    return sorted(p for ext in VI_EXTENSIONS for p in root.glob(f"*{ext}"))
