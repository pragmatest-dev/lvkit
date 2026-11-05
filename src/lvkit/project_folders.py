"""Where the LabVIEW project of a file is, and how a file is shown by its place.

``project_root`` is the project a generated VI belongs to (the base of every
project-relative name a user sees); ``own_project_folder`` is the project a file
outside it belongs to. Neither search reaches a home folder or a folder above one
(``is_home``), and such a folder is never shown by its name (``folder_name``):
its name is a user's or this machine's layout. ``shown_path`` is the one rule for
the path a view shows for a file: its library path, ``./`` and its path in the
project, or ``external/<its own project folder>/<path below it>``, as its module
is placed (``outside_place``).
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath
from typing import TypeVar

from lvkit import project_store
from lvkit.llb_origin import vi_file_path

P = TypeVar("P", bound=PurePath)

# The package (and the shown path's first part) of a file outside the project and
# every library folder.
EXTERNAL_PACKAGE = "external"


def project_root(path: Path | str) -> Path:
    """WHERE THE LABVIEW PROJECT IS for a file or folder: the NEAREST folder above
    it holding a ``.lvproj`` file, a ``.git`` repository or a ``.lvkit`` project
    store; the folder itself when there is none. The search never reaches a home
    folder or a folder above it (``searched``): a home folder kept in git, or
    holding a ``.lvproj``, is not a project, and its name is a user's.
    The base of every project-relative name a user sees: a generated module's
    path, a documentation page, a warning's VI. (``store_root`` answers a
    different question: where lvkit keeps what it caches.)"""
    own = _own_folder(path)
    folders = searched(own, Path.home().resolve())
    return next((f for f in folders if _project_marker(f)), own)


def own_project_folder(path: Path | str) -> Path:
    """The project a file OUTSIDE the generated project belongs to, which places
    it under ``external`` and names its documentation page: the nearest git
    repository above it (one ``.lvproj`` folder inside it is not the whole tree a
    file belongs to), else the nearest folder holding a ``.lvproj`` file or a
    ``.lvkit`` project store, else the file's own folder; searched as for
    ``project_root``, never at a home folder or above it. Its name is shown
    only by ``folder_name``."""
    own = _own_folder(path)
    folders = searched(own, Path.home().resolve())
    repository = next((f for f in folders if (f / ".git").exists()), None)
    return repository or next((f for f in folders if _project_marker(f)), own)


def folder_name(folder: Path) -> str | None:
    """The name ``folder`` is shown by (a module's package, a documentation page's
    folder, a message): its own name; none for a home folder (``is_home``) or a
    folder above one, whose name is a user's or this machine's layout, nor for a
    drive's root."""
    resolved = folder.resolve()
    if is_home(resolved, Path.home().resolve()):
        return None
    return resolved.name or None


def outside_place(path: Path | str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """How a file outside the generated project and every library folder is
    placed and shown: its tree, ``external`` and the name of its own project
    folder (``own_project_folder``, by ``folder_name``: none for a home folder),
    then its folders and name below that folder. One form for the module path,
    the messages and the lvnet ``; path`` of such a file (``outside_shown``)."""
    folder = own_project_folder(path)
    below = Path(path).resolve().relative_to(folder).parts
    name = folder_name(folder)
    return (EXTERNAL_PACKAGE, *((name,) if name else ())), below


def outside_shown(path: Path | str) -> str:
    """A file outside the project as every view shows it:
    ``external/<its project folder>/<path below it>`` (``outside_place``)."""
    tree, below = outside_place(path)
    return "/".join((*tree, *below))


def shown_path(path: Path, base: Path, library: tuple[str, ...] | None) -> str:
    """The path a view shows for the file at ``path``, seen from the folder
    ``base``: its library path (``library``, from ``library_location``:
    ``<userlib>/...``) for a library file; for a file in ``base``'s project
    (``project_root``), ``./`` and its path from ``base``; for any other,
    ``outside_shown``. The same on every machine: never an absolute path, and no
    folder above a file's own tree. A VI inside an ``.llb`` is shown by the
    archive's path and its own name, never by where it was extracted."""
    if library is not None:
        return "/".join(library)
    resolved, folder = vi_file_path(path), base.resolve()
    if resolved.is_relative_to(project_root(folder)):
        return "./" + Path(os.path.relpath(resolved, folder)).as_posix()
    return outside_shown(resolved)


def _own_folder(path: Path | str) -> Path:
    resolved = Path(path).resolve()
    return resolved if resolved.is_dir() else resolved.parent


def is_home(folder: PurePath, home: PurePath) -> bool:
    """Whether ``folder`` is a home folder or above one, for the user whose home
    folder is ``home`` (both resolved): ``home``, a folder above it, or a folder
    beside it (another user's home: ``/home/*``, ``C:\\Users\\*``), found by its
    place, never its name. Folders beside ``home`` count only when ``home`` is
    not directly in a drive's root (``/root``, ``D:\\x``): there they are the
    drive's top folders (``/app``, ``/workspace``), not homes."""
    if home.is_relative_to(folder):
        return True
    return home.parent != type(home)(home.anchor) and folder.parent == home.parent


def searched(own: P, home: PurePath) -> list[P]:
    """The folders a project is looked for in, nearest first: ``own`` and the
    folders above it, never a home folder or a folder above one (``is_home``)."""
    return [f for f in [own, *own.parents] if not is_home(f, home)]


def _project_marker(folder: Path) -> bool:
    """Whether ``folder`` holds a ``.lvproj`` file, a ``.git`` repository or a
    ``.lvkit`` project store (never the global ``~/.lvkit``)."""
    store = folder / project_store.PROJECT_STORE_DIR
    global_store = project_store.global_home().resolve()
    return (
        any(folder.glob("*.lvproj"))
        or (folder / ".git").exists()
        or (store.is_dir() and store.resolve() != global_store)
    )
