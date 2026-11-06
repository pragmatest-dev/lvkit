"""A dependency's path written as ONE string, the way LabVIEW stores it.

LabVIEW records a path as tokens plus a type (0 absolute, 1 relative to the file
that records it). ``to_stored`` writes them, ``from_stored`` reads them back:

- relative (first token empty, type not absolute): the first empty token is the
  calling file's own folder, each further leading empty token is one folder up.
  One empty token is ``./`` and the rest (``['', 'B.vi']`` is ``./B.vi``); n > 1
  are ``../`` n - 1 times and the rest (``['', '', 'shared', 'X.vi']`` is
  ``../shared/X.vi``). A literal ``..`` or ``.`` token stays as written.
- pseudo (first token ``<vilib>``, ``<userlib>``, ``<instrlib>``, ...): the
  tokens joined by ``/``.
- absolute: an empty first token (the current drive's root) is ``/`` and the
  rest; a drive letter token (``C``) is ``C:/`` and the rest; any other first
  token (a network share) is ``//`` and all the tokens.
- anything else: the tokens joined by ``/``.

``from_stored`` returns the tokens and the path type (0, 1, or None for a pseudo
or bare path). A relative record with no path type (``path_type is None``) is
written as relative and reads back as type 1. Reading ``../../x`` gives two
further empty tokens where the record may have had a literal ``..``: the same
folder, and writing it again gives the same string.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from lvkit.library_folders import is_symbolic_root

ABSOLUTE = 0
RELATIVE = 1
_DRIVE = re.compile(r"^[A-Za-z]:/")


def to_stored(tokens: Sequence[str], path_type: int | None) -> str:
    """``tokens`` and their path type as one string (see the module docstring)."""
    if not tokens:
        return ""
    head = tokens[0]
    if is_symbolic_root(head):
        return "/".join(tokens)
    if path_type == ABSOLUTE:
        if head == "":
            return "/" + "/".join(tokens[1:])
        if len(head) == 1 and head.isalpha():
            return f"{head}:/" + "/".join(tokens[1:])
        return "//" + "/".join(tokens)
    if head == "":
        empties = 0
        while empties < len(tokens) and tokens[empties] == "":
            empties += 1
        rest = "/".join(tokens[empties:])
        return ("./" if empties == 1 else "../" * (empties - 1)) + rest
    return "/".join(tokens)


def from_stored(text: str) -> tuple[list[str], int | None]:
    """The tokens and path type ``text`` stands for (see the module docstring)."""
    if text.startswith("//"):
        return text[2:].split("/"), ABSOLUTE
    if text.startswith("/"):
        return ["", *text[1:].split("/")], ABSOLUTE
    if _DRIVE.match(text):
        return [text[0], *text[3:].split("/")], ABSOLUTE
    if text.startswith("./"):
        return ["", *text[2:].split("/")], RELATIVE
    if text.startswith("../"):
        ups = 0
        rest = text
        while rest.startswith("../"):
            ups += 1
            rest = rest[3:]
        return ["", *[""] * ups, *rest.split("/")], RELATIVE
    return text.split("/"), None
