r"""A saved LabVIEW path as text.

A path is a type (absolute, relative or Not a Path) and its components. The text
is one spelling everywhere a path value is shown (lvnet, describe, JSON) and is
what the parser hands on as the value:

- absolute: ``Path("/C/Users")``; with no component ``Path("/")``
- relative: ``Path("test.xml")``; with no component ``Path("")``
- Not a Path: ``NotAPath``

Components are joined by ``/``. An empty component, which goes up a level, is
``..``. A ``/`` or ``\`` inside a component gets a backslash, and so does a ``"``;
a control character is ``\n``, ``\r``, ``\t`` or ``\xHH`` like any string.
"""

from __future__ import annotations

import re
from enum import IntEnum

from .conversion_gap import ConversionGap
from .string_literal import STRING_ESCAPES

NOT_A_PATH = "NotAPath"
_PREFIX = 'Path("'
_SUFFIX = '")'
_PARENT = ".."
_SYMBOLIC = re.compile(r"<[^<>]+>")


class PathType(IntEnum):
    """The type a saved path records (NI's Path Type: absolute, relative, Not A
    Path)."""

    ABSOLUTE = 0
    RELATIVE = 1
    NOT_A_PATH = 2


def format_path(path_type: int, components: list[str]) -> str | None:
    """The text of a saved path, or None when ``path_type`` is not one of
    ``PathType``."""
    if path_type == PathType.NOT_A_PATH:
        return NOT_A_PATH
    if path_type not in (PathType.ABSOLUTE, PathType.RELATIVE):
        return None
    body = "/".join(_component(c) for c in components)
    lead = "/" if path_type == PathType.ABSOLUTE else ""
    return f"{_PREFIX}{lead}{body}{_SUFFIX}"


def _component(text: str) -> str:
    if text == "":
        return _PARENT
    out: list[str] = []
    for ch in text:
        escaped = STRING_ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
        elif ch == "/":
            out.append("\\/")
        elif ord(ch) < 0x20:
            out.append(f"\\x{ord(ch):02X}")
        else:
            out.append(ch)
    return "".join(out)


_ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{2}|.)")
_UNESCAPES = {"n": "\n", "r": "\r", "t": "\t"}


def _unescape(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        code = match.group(1)
        if len(code) == 3:
            return chr(int(code[1:], 16))
        return _UNESCAPES.get(code, code)

    return _ESCAPE.sub(replace, text)


def parse_path(text: str) -> tuple[PathType, list[str]] | None:
    """The type and components a path text says, or None when ``text`` is not one."""
    if text == NOT_A_PATH:
        return PathType.NOT_A_PATH, []
    if not (text.startswith(_PREFIX) and text.endswith(_SUFFIX)):
        return None
    body = text[len(_PREFIX) : -len(_SUFFIX)]
    path_type = PathType.RELATIVE
    if body.startswith("/"):
        path_type = PathType.ABSOLUTE
        body = body[1:]
    if body == "":
        return path_type, []
    return path_type, ["" if p == _PARENT else _unescape(p) for p in _split(body)]


def _split(body: str) -> list[str]:
    """``body`` split at each ``/`` that is not escaped."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in body:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            current.append(ch)
            escaped = True
        elif ch == "/":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def is_path_text(text: str) -> bool:
    return parse_path(text) is not None


def path_text(text: str) -> str | None:
    """The ``/``-separated text of a path (a leading ``/`` when absolute, ``..``
    for a level up), or None for Not a Path or text that is no path."""
    parsed = parse_path(text)
    if parsed is None or parsed[0] == PathType.NOT_A_PATH:
        return None
    path_type, components = parsed
    joined = "/".join(_PARENT if c == "" else c for c in components)
    return "/" + joined if path_type == PathType.ABSOLUTE else joined


def python_path(text: str) -> str | None:
    """The Python expression for a path text: ``None`` for Not a Path, else a
    ``Path(...)`` call of the components (pathlib joins them with the OS
    separator); None when ``text`` is no path.

    An absolute path's first component is its volume. LabVIEW writes it with a
    colon on Windows (``C`` is ``C:\\``), so the volume is spelled for the OS the
    code runs on: ``Path("C:/" if os.name == "nt" else "/C", "Users")``. A path with
    no component, absolute or relative, is the empty ``Path('')`` (Python has no
    absolute path without a component). A path that starts at a symbolic LabVIEW
    folder (``<vilib>``, ``<userlib>``) has no Python meaning and is a gap.
    """
    parsed = parse_path(text)
    if parsed is None:
        return None
    path_type, components = parsed
    if path_type == PathType.NOT_A_PATH:
        return "None"
    if not components:
        return "Path('')"
    parts = [_PARENT if c == "" else c for c in components]
    if path_type == PathType.RELATIVE:
        return f"Path({', '.join(repr(p) for p in parts)})"
    volume, rest = components[0], parts[1:]
    if _SYMBOLIC.fullmatch(volume):
        raise ConversionGap(
            f"The path {text} starts at the symbolic LabVIEW folder {volume}, "
            "which has no Python path; write the folder's real path."
        )
    if volume == "":
        raise ConversionGap(f"The absolute path {text} has no volume name.")
    root = f"{volume}:/"
    posix = f"/{volume}"
    head = f'{root!r} if os.name == "nt" else {posix!r}'
    return f"Path({', '.join([head, *(repr(p) for p in rest)])})"
