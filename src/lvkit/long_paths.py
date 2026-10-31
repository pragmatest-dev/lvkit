"""Windows paths over 260 characters.

Windows refuses a path longer than 260 characters unless the path is written in
its extended-length form (``\\\\?\\`` followed by the absolute path) or the machine
has long paths enabled. Generation writes a package whose folders mirror a
project's, which can pass that limit, so the output folder is taken once in the
extended form and every file under it is written, read and listed through it.
Python cannot import a module over the limit while long paths are off, so
``generate`` warns when it wrote one.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows' limit on a path, counting the drive and the terminating character.
WINDOWS_MAX_PATH = 260

_EXTENDED_PREFIX = "\\\\?\\"


def extended_length(path: Path) -> Path:
    """``path`` made absolute and, on Windows, in the extended-length form that
    has no 260-character limit; unchanged elsewhere."""
    absolute = path.absolute()
    text = str(absolute)
    if sys.platform != "win32" or text.startswith(_EXTENDED_PREFIX):
        return absolute
    if text.startswith("\\\\"):
        return Path(_EXTENDED_PREFIX + "UNC\\" + text[2:])
    return Path(_EXTENDED_PREFIX + text)


def display_path(path: Path) -> Path:
    """``path`` without the extended-length prefix, for showing to a person."""
    text = str(path)
    if text.startswith(_EXTENDED_PREFIX + "UNC\\"):
        return Path("\\\\" + text[len(_EXTENDED_PREFIX) + 4 :])
    if text.startswith(_EXTENDED_PREFIX):
        return Path(text[len(_EXTENDED_PREFIX) :])
    return path


def windows_long_paths_enabled() -> bool:
    """Whether Windows lets programs (Python's import among them) use paths over
    260 characters; True on every other platform."""
    if sys.platform != "win32":
        return True
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return False


def too_long_to_import(paths: list[Path]) -> list[Path]:
    """The paths Python cannot import from on this machine: those over the Windows
    limit while long paths are off. Longest first."""
    if windows_long_paths_enabled():
        return []
    over = [p for p in paths if len(str(display_path(p))) >= WINDOWS_MAX_PATH]
    return sorted(over, key=lambda p: len(str(p)), reverse=True)
