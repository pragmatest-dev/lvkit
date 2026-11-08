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


# The suffix a module takes from the file kind of its VI, so ``x.vi``, ``x.vim``
# and ``x.vit`` in one folder are three modules.
_MODULE_SUFFIX = {".vim": "_vim", ".vit": "_vit"}


def vi_module_stem(name: str) -> str:
    """The stem a VI file's module is named from: the file name without its
    extension, with ``_vim`` after a ``.vim`` and ``_vit`` after a ``.vit``
    (a ``.vi`` keeps its stem). ``codegen/nodes/vi_reference.py``
    ``_IMPORT_MODULE`` applies the same rule to a name computed at run time."""
    if is_vi_file(name):
        dot = name.rindex(".")
        return name[:dot] + _MODULE_SUFFIX.get(name[dot:].lower(), "")
    return name


def class_stem(file_name: str) -> str:
    """A class file's name without its extension: what its module is named from."""
    if file_name.lower().endswith(".lvclass"):
        return file_name[: -len(".lvclass")]
    return file_name


def rglob_vi_files(root: Path) -> list[Path]:
    """Every VI file under ``root`` (recursive), sorted so load order never
    depends on the filesystem."""
    return sorted(p for ext in VI_EXTENSIONS for p in root.rglob(f"*{ext}"))


def glob_vi_files(root: Path) -> list[Path]:
    """Every VI file directly in ``root``, sorted."""
    return sorted(p for ext in VI_EXTENSIONS for p in root.glob(f"*{ext}"))
