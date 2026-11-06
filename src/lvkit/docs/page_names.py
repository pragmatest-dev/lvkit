"""Where each VI's documentation page goes.

A page is named from where its VI lives, never from the absolute path of the file
on this machine: a library VI, inside or outside the project, by its library path
(``library_location``: ``<vilib>/...`` in folder ``vi.lib``, likewise ``user.lib``
and ``instr.lib``), any other project VI by its project-relative path (folder
``Project``), any other VI outside the project (found through a search path) by
its path below its own project folder (``own_project_folder``), under that
folder's name. A VI inside an ``.llb`` is named by the archive's path plus its own
name. A VI owned by a library or class keeps its qualified name and that library's
or class's folder. Names are fixed by the VIs given (sorted), short enough for
Windows paths, and unique on a case-insensitive file system.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.llb_origin import vi_file_path
from lvkit.project_folders import EXTERNAL_PACKAGE, own_project_folder
from lvkit.vi_files import strip_vi_extension

PROJECT_GROUP = "Project"

# A page name longer than this many bytes is cut and given a hash of the whole name.
_MAX_STEM_BYTES = 100

LIBRARY_GROUPS = {
    "<vilib>": "vi.lib",
    "<userlib>": "user.lib",
    "<instrlib>": "instr.lib",
}


def library_group_name(token: str) -> str:
    """The folder and index section of an installation folder's pages: ``vi.lib``,
    ``user.lib``, ``instr.lib``; any other ``<name>`` (``<resource>``,
    ``<extravilib>``) by its own name."""
    return LIBRARY_GROUPS.get(token, token[1:-1])


@dataclass(frozen=True)
class PageName:
    """A page's group (its folder, and the index heading) and file stem."""

    group: str
    stem: str


def _safe(text: str) -> str:
    """``text`` with every character a file system forbids (and spaces) turned into
    ``_``; letters of any script are kept."""
    return re.sub(r"_+", "_", re.sub(r"[^\w.\-]+", "_", text)).strip("_")


def _bounded(stem: str) -> str:
    if len(stem.encode()) <= _MAX_STEM_BYTES:
        return stem
    digest = hashlib.sha1(stem.encode()).hexdigest()[:8]
    cut = stem.encode()[: _MAX_STEM_BYTES - 9].decode(errors="ignore")
    return f"{cut}-{digest}"


def _path_name(
    graph: InMemoryVIGraph, vi_name: str, source: Path, root: Path
) -> PageName:
    """The page of a VI that is a file: by its library path (the installation
    path its callers recorded, else its path below a library folder), else
    project-relative, else below its own project folder. A VI inside an ``.llb``
    is named by the archive's path and its own file name, never by where it was
    extracted."""
    own = vi_file_path(source)

    def named(group: str, parts: tuple[str, ...]) -> PageName:
        rel = "/".join(parts)
        return PageName(group, _bounded(_safe(strip_vi_extension(rel))))

    library = graph.library_location(own, graph.recorded_path(vi_name))
    if library is not None:
        token, *below = library
        return named(library_group_name(token), tuple(below))
    if own.is_relative_to(root.resolve()):
        return named(PROJECT_GROUP, own.relative_to(root.resolve()).parts)
    outside = own_project_folder(own)
    group = outside.name or EXTERNAL_PACKAGE
    return named(group, own.relative_to(outside).parts)


def page_names(
    graph: InMemoryVIGraph, vi_names: list[str], root: Path
) -> dict[str, PageName]:
    """The page of every VI in ``vi_names`` that is named by its file (a VI named by
    its library or class, or not a file, is left to ``NamingMixin``). A page whose
    name is taken (compared without regard to case) gets the next free rank
    (``-2``, ``-3``), in sorted order."""
    named: dict[str, PageName] = {}
    for vi_name in sorted(vi_names):
        if ".lvlib:" in vi_name or ".lvclass:" in vi_name:
            continue
        source = graph.get_vi_source_path(vi_name)
        if source is not None and Path(vi_name).is_absolute():
            named[vi_name] = _path_name(graph, vi_name, source, root)
    taken: set[tuple[str, str]] = set()
    unique: dict[str, PageName] = {}
    for vi_name, name in named.items():
        rank, stem = 1, name.stem
        while (name.group.casefold(), stem.casefold()) in taken:
            rank += 1
            stem = f"{name.stem}-{rank}"
        taken.add((name.group.casefold(), stem.casefold()))
        unique[vi_name] = PageName(name.group, stem)
    return unique
