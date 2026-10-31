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
``<topvi>\\*;<foundvi>\\;<vilib>\\*;<userlib>\\*;<instrlib>\\*``)."""

from __future__ import annotations

from collections.abc import Sequence
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
