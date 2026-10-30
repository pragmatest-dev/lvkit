"""Where each generated module lives: the project's own folder hierarchy.

A VI's module path is its project-relative file path: every folder is a package,
the file's stem is the module, and one transform (``module_part``) turns each part
into a Python name. The project is the one the generated VI belongs to (its
``.lvproj``, ``.git`` or ``.lvkit`` folder, else the VI's own folder), so the layout
never depends on what else was loaded.

A file outside the project is placed by the path its caller recorded for it when
that path starts at a LabVIEW installation folder (``<vilib>/Utility/error.llb/X.vi``
is ``vilib.utility.errorllb.x``), which names the same file for every caller; one
found through a search path, under a package named for that search path; any other
under ``external``, by its path from the folder it shares with the project. A VI
with no file is placed by the path its caller recorded, whatever it starts at; one
with neither a file nor a recorded path cannot be imported and stops
generation, naming it. Paths are unique, so modules are unique: no collision
numbering. Two files whose parts transform to the same name stop generation, naming
both.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lvkit.codegen.ast_utils import module_part
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.node_kinds import NodeType
from lvkit.parser.models import is_symbolic_root
from lvkit.project_store import PROJECT_STORE_DIR, global_home
from lvkit.vi_files import strip_vi_extension

# The package a file outside the project and every search path is placed in.
EXTERNAL_PACKAGE = "external"


class ModuleCollisionError(Exception):
    """Two files that would generate the same module (or a module and a package of
    the same name)."""


class UnplacedVIError(Exception):
    """A VI with neither a file nor a recorded path: nothing says where its module
    is."""


@dataclass(frozen=True)
class ModulePlace:
    """The packages (outermost first) and the module a file is generated as."""

    packages: tuple[str, ...]
    module: str

    @property
    def parts(self) -> tuple[str, ...]:
        return (*self.packages, self.module)


def project_root(input_path: Path) -> Path:
    """The project folder of the VI (or folder) being generated: the nearest folder
    above it holding a ``.lvproj`` file, a ``.git`` folder or a ``.lvkit`` project
    store; the VI's own folder when there is none."""
    start = input_path.resolve()
    own = start if start.is_dir() else start.parent
    home = global_home().resolve()
    for folder in [own, *own.parents]:
        store = folder / PROJECT_STORE_DIR
        if (
            any(folder.glob("*.lvproj"))
            or (folder / ".git").exists()
            or (store.is_dir() and store.resolve() != home)
        ):
            return folder
    return own


def _tokens(path: str) -> list[str]:
    return [t for t in re.split(r"[\\/]", path) if t]


def _beside_project(key: str, root: str) -> tuple[str, ...]:
    """The parts of ``key`` below the folder it shares with the project (all of
    them, drive aside, when it shares none)."""
    own, project = Path(key).parent.parts, Path(root).parts
    shared = 0
    while shared < min(len(own), len(project)) and own[shared] == project[shared]:
        shared += 1
    parts = Path(key).parts
    return (
        parts[shared:] if shared else tuple(p for p in parts if p != Path(key).anchor)
    )


def _packages(folders: Sequence[str]) -> tuple[str, ...]:
    """The package of each folder: the one transform every placement uses."""
    return tuple(module_part(f) for f in folders if f != ".")


def _in_installation_folder(recorded: str) -> bool:
    """Whether a recorded path starts at a LabVIEW installation folder
    (``<vilib>/...``), which names the same file for every caller; any other
    recorded path is relative to the caller that recorded it."""
    return is_symbolic_root(_tokens(recorded)[0]) if _tokens(recorded) else False


def _is_path(key: str) -> bool:
    return os.path.isabs(key) or len(_tokens(key)) > 1


def _below(key: str, folder: str) -> tuple[str, ...] | None:
    """The parts of ``key`` below ``folder``; None when it is not inside it (or on
    another drive)."""
    try:
        return Path(key).relative_to(folder).parts
    except ValueError:
        return None


class ModuleLayout:
    """The module of every key in ``keys``."""

    def __init__(
        self,
        graph: InMemoryVIGraph,
        keys: list[str],
        root: Path,
        search_roots: Sequence[Path] = (),
        shared: frozenset[str] = frozenset(),
        recorded: Mapping[str, str] | None = None,
        reserved: Collection[str] = (),
    ) -> None:
        """``root`` is the project folder (``project_root``). ``shared`` keys (the
        variants of a polymorphic VI) are generated in their wrapper's module and
        take no module of their own. ``recorded`` overrides the path a caller
        recorded for an absent VI (``graph.recorded_path`` otherwise). ``reserved``
        are names at the package root that belong to generated shared modules
        (``errors``): no VI module or package may take one."""
        self._graph = graph
        self._root = str(root.resolve())
        self._search_roots = [str(p.resolve()) for p in search_roots]
        self._recorded = None if recorded is None else dict(recorded)
        self.unplaced: dict[str, UnplacedVIError] = {}
        self._reserved = frozenset(reserved)
        self.places: dict[str, ModulePlace] = {}
        for key in keys:
            if key in shared:
                continue
            try:
                self.places[key] = self._place(key)
            except UnplacedVIError as unplaced:
                self.unplaced[key] = unplaced
        self._check_unique()

    def placed_folder(self, folder: Path) -> tuple[str, ...] | None:
        """The packages a project ``folder`` is generated as (the ones its VI
        modules sit in); None when it is not inside the project."""
        inside = _below(str(folder.resolve()), self._root)
        return None if inside is None else _packages(inside)

    def _recorded_path(self, key: str) -> str | None:
        """The path a caller recorded for ``key`` (``recorded`` when given, else
        the graph's)."""
        if self._recorded is None:
            return self._graph.recorded_path(key)
        return self._recorded.get(key)

    def _tokens_of(self, key: str) -> list[str]:
        """The folders and the file name that place ``key``."""
        recorded = self._recorded_path(key)
        if not _is_path(key):
            if recorded is None:
                raise UnplacedVIError(
                    f"The VI {key!r} has neither a file nor a path its caller "
                    "recorded, so there is no module to import it from."
                )
            return _tokens(recorded)
        if not os.path.isabs(key):
            return _tokens(key)
        inside = _below(key, self._root)
        if inside is not None:
            return list(inside)
        if recorded is not None and (
            self._graph.is_stub(key) or _in_installation_folder(recorded)
        ):
            return _tokens(recorded)
        for search_root in self._search_roots:
            inside = _below(key, search_root)
            if inside is not None:
                return [Path(search_root).name, *inside]
        return [EXTERNAL_PACKAGE, *_beside_project(key, self._root)]

    def _place(self, key: str) -> ModulePlace:
        *folders, name = self._tokens_of(key)
        try:
            node_type = self._graph.node_type(key)
        except KeyError:
            node_type = None
        stem = strip_vi_extension(name) if node_type in (None, NodeType.VI) else name
        return ModulePlace(_packages(folders), module_part(stem))

    def place_beside(self, key: str, sibling_keys: list[str]) -> ModulePlace:
        """``key`` (a polymorphic wrapper with no file) in the package of the first
        of its variants ``sibling_keys`` that can be placed."""
        stem = strip_vi_extension(self._graph.vi_display_name(key).split(":")[-1])
        error: UnplacedVIError | None = None
        for sibling in sibling_keys:
            try:
                packages = (self.places.get(sibling) or self._place(sibling)).packages
            except UnplacedVIError as caught:
                error = caught
                continue
            place = ModulePlace(packages, module_part(stem))
            self.places[key] = place
            return place
        raise error or UnplacedVIError(f"The VI {key!r} has no variants to place.")

    def place_of_name(self, name: str) -> ModulePlace:
        """The place of a VI known only by its name, from the path a call records
        for it."""
        return self._place(name)

    def folders_of(self, key: str) -> tuple[str, ...]:
        """The folders that hold ``key``'s file, as raw tokens: the path below the
        project for a project file; the path its caller recorded (``<vilib>``,
        ``<userlib>``, ...) for a file outside it that was recorded from an
        installation folder, else the search folder's name or ``external`` and the
        path below it. The same tokens that place its module. Raises
        ``UnplacedVIError`` when nothing says where it is."""
        return tuple(self._tokens_of(key)[:-1])

    def packages_of(self, key: str) -> tuple[str, ...]:
        """The packages that hold the file ``key`` (a folder, a library or a
        class file): where its members' modules are looked for."""
        return _packages(self._tokens_of(key)[:-1])

    @property
    def root_names(self) -> set[str]:
        """The names at the package root that VI modules and packages take."""
        return {place.parts[0] for place in self.places.values()}

    def _check_unique(self) -> None:
        for key, place in self.places.items():
            if place.parts[0] in self._reserved:
                raise ModuleCollisionError(
                    f"{key!r} generates {'.'.join(place.parts)}, but "
                    f"{place.parts[0]!r} at the package root is the generated "
                    "shared module of that name."
                )
        owner: dict[tuple[str, ...], str] = {}
        for key, place in self.places.items():
            if place.parts in owner:
                raise ModuleCollisionError(
                    f"{owner[place.parts]!r} and {key!r} both generate the module "
                    f"{'.'.join(place.parts)}."
                )
            owner[place.parts] = key
        for key, place in self.places.items():
            for depth in range(1, len(place.parts)):
                other = owner.get(place.parts[:depth])
                if other is not None:
                    raise ModuleCollisionError(
                        f"{other!r} generates the module "
                        f"{'.'.join(place.parts[:depth])}, which is also the "
                        f"package of {key!r}."
                    )
