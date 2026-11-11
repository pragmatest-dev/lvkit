"""What the file ops that take an absolute path (Delete, Create Folder, Write to
Text File) share: the two failures NI gives a bad path, raised as classes of the
node's own that its catalog entry names (``base`` of the ``errors`` entries), so no
other ``ValueError`` is taken for them."""

from __future__ import annotations

# NI, General LabVIEW Error Codes, 1430, and 1 ("An input parameter is invalid.
# For example if the input is a path, the path might contain a character not
# allowed by the OS").
EMPTY_OR_RELATIVE = "The path is empty or relative. You must use an absolute path."
INVALID_PATH = "An input parameter is invalid: the path contains a null character."


def absolute_path_check(path: str) -> str:
    """Statements that raise ``EmptyOrRelativePathError`` for an empty or relative
    ``path`` and ``InvalidPathError`` for one with a null character."""
    return (
        f"if {path} is None or not os.path.isabs({path}):\n"
        f"    raise EmptyOrRelativePathError({EMPTY_OR_RELATIVE!r})\n"
        f"if '\\0' in os.fspath({path}):\n"
        f"    raise InvalidPathError({INVALID_PATH!r})\n"
    )
