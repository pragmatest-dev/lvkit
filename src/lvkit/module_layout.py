"""Where each generated module lives: the project's own folder hierarchy.

A VI's module path is its project-relative file path: every folder is a package,
the file's stem is the module, and one transform (``module_part``) turns each part
into a Python name. The project is the one the generated VI belongs to (its
``.lvproj``, ``.git`` or ``.lvkit`` folder, else the VI's own folder), so the layout
never depends on what else was loaded.

A library file, inside or outside the project, is placed by its library path
(``library_location``): the path its caller recorded for it when that path starts
at a LabVIEW installation folder (``<vilib>/Utility/error.llb/X.vi`` is
``vilib.utility.errorllb.x``), which names the same file for every caller; else,
under a configured vi.lib / user.lib / instr.lib folder, its path in that folder
as if recorded (``<userlib>/...``). Any other file in the project is placed by its
project-relative path; any other outside it under ``external``, by the name of its
own project folder (``outside_place``) and its path below that folder. A VI with no
file is placed by the path its caller
recorded, whatever it starts at; one with neither a file nor a recorded path
cannot be imported and stops generation, naming it. Paths are unique, so modules
are unique, and a module name is never numbered. Two files whose parts transform
to the same name (two outside trees whose project folders share a name and hold
the same file) stop generation, naming both by their paths from the project folder.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lvkit.codegen.ast_utils import module_part
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.node_kinds import NodeType
from lvkit.graph.recorded_paths import walk_relative
from lvkit.library_folders import path_tokens
from lvkit.project_folders import outside_place, project_relative
from lvkit.vi_files import class_stem, strip_vi_extension


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


@dataclass(frozen=True)
class _Location:
    """The tokens that place a file: ``tree``, which names the folder tree it is
    in (``()`` for the project, ``("<userlib>",)`` for a library folder,
    ``("external", <its own project folder>)`` for any other file), then its
    folders and file name ``below`` that tree."""

    tree: tuple[str, ...]
    below: tuple[str, ...]

    @property
    def tokens(self) -> list[str]:
        return [*self.tree, *self.below]


def _outside_project(key: str) -> _Location:
    """``key`` as ``outside_place`` places it: ``external`` and the name of its
    own project folder, then its path below that folder."""
    return _Location(*outside_place(key))


def _packages(folders: Sequence[str]) -> tuple[str, ...]:
    """The package of each folder: the one transform every placement uses."""
    return tuple(module_part(f) for f in folders if f != ".")


def _is_path(key: str) -> bool:
    return os.path.isabs(key) or len(path_tokens(key)) > 1


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

    def placed_beside(
        self, key: str, recorded: Sequence[str], below: Sequence[str] = ()
    ) -> tuple[str, ...] | None:
        """The place in the package (its parts) of a file at ``below`` in the
        folder that holds a file the VI ``key`` records relative to its own file
        (``recorded``, LabVIEW's tokens: leading empty tokens and ``..`` go up):
        the packages the same walk from the VI's own place gives
        (``walk_relative``), then ``below`` under its own names, so a file beside
        a VI is generated beside its module wherever the module is placed; None
        when the walk leaves the VI's tree (above its project, its library folder
        or its own outside project)."""
        location = self._location(key)
        walked = walk_relative(location.tokens, recorded, keep=len(location.tree))
        return None if walked is None else (*_packages(walked[:-1]), *below)

    def _recorded_path(self, key: str) -> str | None:
        """The path a caller recorded for ``key`` (``recorded`` when given, else
        the graph's)."""
        if self._recorded is None:
            return self._graph.recorded_path(key)
        return self._recorded.get(key)

    def _tokens_of(self, key: str) -> list[str]:
        """The folders and the file name that place ``key``."""
        return self._location(key).tokens

    def _location(self, key: str) -> _Location:
        """Where ``key`` is placed: its library path, else its path below the
        project, else (a VI with no file) the path its caller recorded, else
        below ``external``."""
        recorded = self._recorded_path(key)
        if not _is_path(key):
            if recorded is None:
                raise UnplacedVIError(
                    f"The VI {key!r} has neither a file nor a path its caller "
                    "recorded, so there is no module to import it from."
                )
            return _Location((), tuple(path_tokens(recorded)))
        if not os.path.isabs(key):
            return _Location((), tuple(path_tokens(key)))
        library = self._graph.library_location(key, recorded)
        if library is not None:
            return _Location(library[:1], library[1:])
        inside = _below(key, self._root)
        if inside is not None:
            return _Location((), inside)
        if recorded is not None and self._graph.is_stub(key):
            return _Location((), tuple(path_tokens(recorded)))
        return _outside_project(key)

    def _place(self, key: str) -> ModulePlace:
        *folders, name = self._tokens_of(key)
        try:
            node_type = self._graph.node_type(key)
        except KeyError:
            node_type = None
        if node_type is NodeType.CLASS:
            stem = class_stem(name)
        elif node_type in (None, NodeType.VI):
            stem = strip_vi_extension(name)
        else:
            stem = name
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
        """The folders that hold ``key``'s file, as raw tokens: its library path
        (``<vilib>``, ``<userlib>``, ...) for a library file; the path below the
        project for any other project file; ``external``, its own project
        folder's name and the path below it for any other. The same tokens that
        place its module. Raises ``UnplacedVIError`` when nothing says where it
        is."""
        return tuple(self._tokens_of(key)[:-1])

    def display_path(self, key: str) -> str:
        """The path a message shows for ``key``: the one that places it (its
        library path such as ``<userlib>/...``, project-relative, or below
        ``external``), never an absolute path; the file name alone when nothing
        places it."""
        try:
            return "/".join(self._tokens_of(key))
        except UnplacedVIError:
            return Path(key).name

    def packages_of(self, key: str) -> tuple[str, ...]:
        """The packages that hold the file ``key`` (a folder, a library or a
        class file): where its members' modules are looked for."""
        return _packages(self._tokens_of(key)[:-1])

    @property
    def project_folder(self) -> str:
        """The project folder messages name files from."""
        return self._root

    @property
    def root_names(self) -> set[str]:
        """The names at the package root that VI modules and packages take."""
        return {place.parts[0] for place in self.places.values()}

    def _shown(self, key: str) -> str:
        """A key as a message shows it: a file by its path from this layout's
        project folder (which ``key_label`` does not know: it names a file from
        the run's folder), any other as the graph labels it (``key_label``)."""
        if os.path.isabs(key):
            return project_relative(key, self._root)
        return self._graph.key_label(key)

    def _pair(self, first: str, second: str) -> tuple[str, str]:
        """Two keys as a message shows them side by side."""
        return repr(self._shown(first)), repr(self._shown(second))

    def _check_unique(self) -> None:
        for key, place in self.places.items():
            if place.parts[0] in self._reserved:
                raise ModuleCollisionError(
                    f"{self._shown(key)!r} generates {'.'.join(place.parts)}, but "
                    f"{place.parts[0]!r} at the package root is the generated "
                    "shared module of that name."
                )
        owner: dict[tuple[str, ...], str] = {}
        for key, place in self.places.items():
            if place.parts in owner:
                first, second = self._pair(owner[place.parts], key)
                raise ModuleCollisionError(
                    f"{first} and {second} both generate the module "
                    f"{'.'.join(place.parts)}."
                )
            owner[place.parts] = key
        for key, place in self.places.items():
            for depth in range(1, len(place.parts)):
                other = owner.get(place.parts[:depth])
                if other is not None:
                    first, second = self._pair(other, key)
                    raise ModuleCollisionError(
                        f"{first} generates the module "
                        f"{'.'.join(place.parts[:depth])}, which is also the "
                        f"package of {second}."
                    )
