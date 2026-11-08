"""The text a string or path control shows on the panel: what the user sees, not
the literal the parser keeps (a quoted string, a ``Path("...")`` call)."""

from __future__ import annotations

import ast

from ....path_value import PathType, parse_path

_NOT_A_PATH_TEXT = "<Not A Path>"


def string_text(default_value: str | None) -> str:
    """A string control's saved value as shown: the contents of the Python
    string literal the parser keeps (every escape applied), without its quotes.
    A value that is no string literal is shown as it is."""
    if not default_value:
        return ""
    try:
        value = ast.literal_eval(default_value)
    except (ValueError, SyntaxError):
        return default_value
    return value if isinstance(value, str) else default_value


def path_text(default_value: str | None) -> str:
    """A path control's saved value as shown: an absolute path's first
    component is its volume (``C:\\Users``), a relative path is its components
    joined by ``\\``. A symbolic folder such as ``<vilib>`` has no colon. Not a
    Path shows as ``<Not A Path>``."""
    if not default_value:
        return ""
    parsed = parse_path(default_value)
    if parsed is None:
        return default_value
    path_type, components = parsed
    if path_type == PathType.NOT_A_PATH:
        return _NOT_A_PATH_TEXT
    names = [".." if c == "" else c for c in components]
    if path_type == PathType.ABSOLUTE and names and not names[0].startswith("<"):
        return names[0] + ":\\" + "\\".join(names[1:])
    return "\\".join(names)
