"""Where each VI's documentation page goes.

A page is named from where its VI lives, never from the absolute path of the file
on this machine: a project VI by its project-relative path (folder ``Project``), a
VI in a LabVIEW library folder by its recorded ``<vilib>/...`` path (folder
``vi.lib``, ``user.lib`` or ``instr.lib``), any other VI outside the project (found
through a search path) by its path below its own project folder, under that
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

from lvkit.cache_paths import project_root
from lvkit.graph import InMemoryVIGraph
from lvkit.llb_origin import llb_container_of
from lvkit.vi_files import strip_vi_extension

PROJECT_GROUP = "Project"

# A page name longer than this many bytes is cut and given a hash of the whole name.
_MAX_STEM_BYTES = 100

LIBRARY_GROUPS = {
    "<vilib>": "vi.lib",
    "<userlib>": "user.lib",
    "<instrlib>": "instr.lib",
}


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


def _path_name(graph: InMemoryVIGraph, source: Path, root: Path) -> PageName:
    """The page of a VI that is a file. A VI inside an ``.llb`` is named by the
    archive's path and its own file name, never by where it was extracted."""
    container = llb_container_of(source)
    origin = (container or source).resolve()
    member = f"/{source.name}" if container is not None else ""

    def named(group: str, base: Path, path: Path) -> PageName:
        rel = path.relative_to(base.resolve()).as_posix()
        return PageName(group, _bounded(_safe(strip_vi_extension(rel + member))))

    if origin.is_relative_to(root.resolve()):
        return named(PROJECT_GROUP, root, origin)
    library = graph.library_root_of(origin)
    if library is not None:
        token, library_root = library
        return named(LIBRARY_GROUPS[token], library_root, origin)
    outside = project_root(origin)
    return named(outside.name, outside, origin)


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
            named[vi_name] = _path_name(graph, source, root)
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
