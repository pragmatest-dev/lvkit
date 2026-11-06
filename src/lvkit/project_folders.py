"""Where the LabVIEW project of a file is, and how a file outside it is placed.

``project_root`` is the project a generated VI belongs to (the base of every
project-relative name a user sees); ``own_project_folder`` is the project a file
outside it belongs to, which places it under ``external`` (``outside_place``).
"""

from __future__ import annotations

import os
from pathlib import Path

from lvkit import project_store

# The package of a file outside the project and every library folder.
EXTERNAL_PACKAGE = "external"


def project_root(path: Path | str) -> Path:
    """WHERE THE LABVIEW PROJECT IS for a file or folder: the NEAREST folder above
    it holding a ``.lvproj`` file, a ``.git`` repository or a ``.lvkit`` project
    store; the folder itself when there is none.
    The base of every project-relative name a user sees: a generated module's
    path, a documentation page, a warning's VI. (``store_root`` answers a
    different question: where lvkit keeps what it caches.)"""
    own = _own_folder(path)
    return next((f for f in [own, *own.parents] if _project_marker(f)), own)


def own_project_folder(path: Path | str) -> Path:
    """The project a file OUTSIDE the generated project belongs to, which places
    it under ``external`` and names its documentation page: the nearest git
    repository above it (one ``.lvproj`` folder inside it is not the whole tree a
    file belongs to), else the nearest folder holding a ``.lvproj`` file or a
    ``.lvkit`` project store, else the file's own folder."""
    own = _own_folder(path)
    folders = [own, *own.parents]
    repository = next((f for f in folders if (f / ".git").exists()), None)
    return repository or next((f for f in folders if _project_marker(f)), own)


def outside_place(path: Path | str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """How a file outside the generated project and every library folder is
    placed: its tree, ``external`` and the name of its own project folder
    (``own_project_folder``; no name for a drive's root), then its folders and
    name below that folder."""
    folder = own_project_folder(path)
    below = Path(path).resolve().relative_to(folder).parts
    name = folder.name
    return (EXTERNAL_PACKAGE, *((name,) if name else ())), below


def project_relative(path: Path | str, root: Path | str) -> str:
    """``path`` as a message names it: its path from the project folder ``root``
    (posix, ``..`` where it lies outside), or as it is on another drive."""
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:
        return str(path)


def _own_folder(path: Path | str) -> Path:
    resolved = Path(path).resolve()
    return resolved if resolved.is_dir() else resolved.parent


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
