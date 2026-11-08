"""The options that shape what a render/diff build produces besides the VI's
own bytes: which library roots recorded paths resolve against, how deep the
load goes, and which project store feeds the resolvers. They are part
of the output cache's key (``output_cache.render_options_tag``) and the single
source of the build keyword arguments, so what is keyed is exactly what is built
with. Stdlib-light like :mod:`lvkit.output_cache`: building a key on a cache hit
imports nothing heavy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cache_paths import LibraryRoots
from .library_folders import folder_list, library_key
from .load_mode import LoadMode


@dataclass(frozen=True)
class BuildOptions:
    """vi.lib / user.lib / instr.lib folders (each in the order they are
    tried), the VI Search Path (None: LabVIEW's default), load mode and project
    store directory of one build."""

    vilib_root: tuple[Path, ...] = ()
    userlib_root: tuple[Path, ...] = ()
    instrlib_root: tuple[Path, ...] = ()
    search_path: tuple[str, ...] | None = None
    mode: LoadMode = LoadMode.MINIMAL
    project_store: Path | None = None

    def tag(self) -> str:
        """The key fragment: every field, spelling-independent (resolved paths)."""

        def _one(p: Path | None) -> str:
            return str(p.resolve()) if p is not None else ""

        def _all(folders: tuple[Path, ...]) -> str:
            return ";".join(_one(p) for p in folder_list(folders))

        search = ";".join(self.search_path) if self.search_path is not None else ""
        return (
            f"vilib={_all(self.vilib_root)}|userlib={_all(self.userlib_root)}"
            f"|instrlib={_all(self.instrlib_root)}|search={search}"
            f"|mode={self.mode.name}|store={_one(self.project_store)}"
        )

    def library_roots(self) -> LibraryRoots:
        """The library folders as the cache classifier takes them."""
        return LibraryRoots.of(
            vilib_root=self.vilib_root,
            userlib_root=self.userlib_root,
            instrlib_root=self.instrlib_root,
        )

    def library_key(self) -> str:
        """The folders and search path as the index records them
        (:func:`~lvkit.library_folders.library_key`)."""
        return library_key(
            {
                "<vilib>": self.vilib_root,
                "<userlib>": self.userlib_root,
                "<instrlib>": self.instrlib_root,
            },
            self.search_path,
        )

    def build_kwargs(self) -> dict[str, Any]:
        """The keyword arguments ``render_body`` / ``diff_vi_files`` take."""
        return {
            "vilib_root": self.vilib_root,
            "userlib_root": self.userlib_root,
            "instrlib_root": self.instrlib_root,
            "search_path": self.search_path,
            "mode": self.mode,
        }
