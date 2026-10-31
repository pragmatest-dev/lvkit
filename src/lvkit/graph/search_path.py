"""Where LabVIEW looks for a dependency, and in what order.

- A pseudo path (``<vilib>``, ``<userlib>``, ``<instrlib>``) names one or more
  folders, tried in order: a recorded ``<userlib>/_OpenG.lib/x.vi`` is the first
  of those folders that holds ``_OpenG.lib/x.vi``. NI: "the <vilib> symbolic
  path represents any <vi.lib> folder within these locations"
  (https://docs-be.ni.com/api/bundle/labview/page/symbolic-paths.html).
- The VI Search Path is an ordered list of folders and pseudo paths LabVIEW
  searches for a file that is not at its recorded path (LabVIEW.ini
  ``viSearchPath``, default ``<topvi>\\*;<foundvi>\\;<vilib>\\*;<userlib>\\*;
  <instrlib>\\*``). Every entry is searched with its subfolders, except
  ``<foundvi>`` (the folders this load has found files in), which LabVIEW's
  default searches without them.

A file is found in a search folder by the recorded path's tail, the longest
first: for ``<userlib>/_OpenG.lib/array/array.llb/x.vi`` the file whose path
ends in ``_OpenG.lib/array/array.llb/x.vi``, else ``array/array.llb/x.vi``, down
to the bare ``x.vi``. The first tail exactly one file has wins; two files with
the same longest tail are ambiguous. Names compare case-insensitively, as
LabVIEW's do."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..library_folders import FOUND_VI, TOP_VI
from .errors import AmbiguousVIReferenceError


@dataclass
class SearchPath:
    """The VI Search Path of one load: the entries, the folders each pseudo path
    names (``pseudo``), and an index of each searched folder's files by name."""

    entries: tuple[str, ...]
    pseudo: dict[str, tuple[Path, ...]]
    top_vi_folder: Path | None = None
    found_folders: dict[Path, None] = field(default_factory=dict)
    _files: dict[tuple[Path, bool], dict[str, list[Path]]] = field(
        default_factory=dict, repr=False
    )

    def folders(self) -> list[tuple[Path, bool]]:
        """The folders to search, in order, each with whether its subfolders
        are searched too."""
        result: list[tuple[Path, bool]] = []
        for entry in self.entries:
            if entry == TOP_VI:
                if self.top_vi_folder is not None:
                    result.append((self.top_vi_folder, True))
            elif entry == FOUND_VI:
                result.extend((f, False) for f in self.found_folders)
            elif entry in self.pseudo:
                result.extend((f, True) for f in self.pseudo[entry])
            elif not entry.startswith("<"):
                result.append((Path(entry), True))
        return result

    def find(
        self, tail: Sequence[str], is_it: Callable[[Path], bool]
    ) -> Path | None:
        """The file a recorded path's ``tail`` (its folders and file name, the
        root dropped) names in the first search folder that has it: among the
        files of its name there that ``is_it`` accepts (the one the caller
        records, by qualified name), the one with the longest tail. None when
        no search folder has such a file."""
        if not tail:
            return None
        leaf = tail[-1].lower()
        for folder, below in self.folders():
            candidates = [
                c for c in self._index(folder, below).get(leaf, []) if is_it(c)
            ]
            if candidates:
                return _longest_tail(candidates, tail)
        return None

    def _index(self, folder: Path, below: bool) -> dict[str, list[Path]]:
        key = (folder, below)
        index = self._files.get(key)
        if index is None:
            index = {}
            for path in _files_in(folder, below):
                index.setdefault(path.name.lower(), []).append(path)
            self._files[key] = index
        return index


def _files_in(folder: Path, below: bool) -> Iterable[Path]:
    if not folder.is_dir():
        return []
    if not below:
        return sorted(p for p in folder.iterdir() if p.is_file())
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        found.extend(Path(dirpath) / name for name in sorted(filenames))
    return found


def _longest_tail(candidates: list[Path], tail: Sequence[str]) -> Path:
    """The one candidate whose path ends in the longest piece of ``tail``."""
    wanted = [t.lower() for t in tail]
    for length in range(len(wanted), 0, -1):
        piece = wanted[-length:]
        matches = [
            c for c in candidates if [p.lower() for p in c.parts[-length:]] == piece
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousVIReferenceError(
                "Ambiguous reference -- several files on the VI search path end "
                f"in {'/'.join(tail[-length:])!r}. Keep the one it names, or put "
                "its folder first in --search-path.",
                sorted(matches),
            )
    raise AssertionError("a candidate always matches its own file name")
