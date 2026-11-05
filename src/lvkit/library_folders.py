"""The folders LabVIEW's pseudo paths name, and the VI Search Path's entries, as
the command line and the APIs take them (stdlib only: the CLI imports it at
start-up). How a load uses them is ``graph/search_path.py``.

A pseudo path (``<vilib>``, ``<userlib>``, ``<instrlib>``) names one or more
folders, tried in order. NI: "the <vilib> symbolic path represents any <vi.lib>
folder within these locations"; "you can expand the set of locations that a
supported symbolic path resolves to"
(https://docs-be.ni.com/api/bundle/labview/page/symbolic-paths.html).

The VI Search Path is an ordered list of folders and pseudo paths searched for a
file that is not at its recorded path (LabVIEW.ini ``viSearchPath``; default
``<topvi>\\*;<foundvi>\\;<vilib>\\*;<userlib>\\*;<instrlib>\\*``).

Where a library file is (``library_location``) is decided here, once, for every
view: module paths, docs pages, messages and lvnet ``uses`` paths."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

TOP_VI = "<topvi>"
FOUND_VI = "<foundvi>"
PSEUDO_PATHS = ("<vilib>", "<userlib>", "<instrlib>")
DEFAULT_SEARCH_PATH: tuple[str, ...] = (TOP_VI, FOUND_VI, *PSEUDO_PATHS)

# A folder argument as given: one folder, several, or none.
Folders = Path | str | Sequence[Path | str] | None
# The VI Search Path as given: its entries in order, or None for the default.
SearchEntries = Sequence[str] | None


def folder_list(folders: Folders) -> tuple[Path, ...]:
    """``folders`` as an ordered tuple, without repeats."""
    if folders is None:
        return ()
    if isinstance(folders, (str, Path)):
        folders = [folders]
    seen: dict[Path, None] = {}
    for folder in folders:
        seen.setdefault(Path(folder), None)
    return tuple(seen)


def search_entry(text: str) -> str:
    """One VI Search Path entry as written: a folder or a pseudo path. A trailing
    ``\\*`` or ``/*`` (LabVIEW's "and its subfolders") or separator is dropped:
    every entry is searched with its subfolders."""
    entry = text.strip()
    if entry.endswith("*"):
        entry = entry[:-1]
    return entry.rstrip("\\/") or entry


def search_path_entries(entries: SearchEntries) -> tuple[str, ...]:
    """The VI Search Path: the given entries in order, else LabVIEW's default."""
    if not entries:
        return DEFAULT_SEARCH_PATH
    return tuple(search_entry(e) for e in entries)


def is_symbolic_root(token: str) -> bool:
    """A LinkSavePathRef's leading ``<name>`` token: a path relative to a
    LabVIEW installation folder (``<vilib>``, ``<resource>``, …), not to the
    calling file."""
    return len(token) > 2 and token.startswith("<") and token.endswith(">")


def path_tokens(path: str) -> list[str]:
    """The folders and file name of a recorded or on-disk path, split at either
    separator."""
    return [t for t in re.split(r"[\\/]", path) if t]


def library_location(
    path: Path | str | None,
    roots: Iterable[tuple[str, Path]],
    recorded: str | None = None,
) -> tuple[str, ...] | None:
    """Where a library file is, the same for every view and every machine: the
    path its caller recorded when that path starts at an installation folder,
    any ``<…>`` token (``is_symbolic_root``: ``<vilib>/Utility/error.llb/X.vi``,
    ``<extravilib>/...``, ``<resource>/...``), whether or not the file is here; else,
    for a file ``path`` under one of the library folders ``roots`` (pseudo path,
    folder; tried in order), that pseudo path and the parts below the folder
    (``<userlib>/_OpenG.lib/...``); None for any other file, and for a ``path``
    that is not absolute (no folder to compare). A library file inside a project
    is still a library file."""
    if recorded is not None:
        tokens = path_tokens(recorded)
        if tokens and is_symbolic_root(tokens[0]):
            return tuple(tokens)
    if path is None or not Path(path).is_absolute():
        return None
    resolved = Path(path).resolve()
    for token, root in roots:
        folder = root.resolve()
        if resolved.is_relative_to(folder):
            return (token, *resolved.relative_to(folder).parts)
    return None
