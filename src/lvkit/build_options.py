"""The options that shape what a render/diff build produces besides the VI's
own bytes: where SubVIs are looked for, which library roots are in force, how
deep the load goes, and which project store feeds the resolvers. They are part
of the output cache's key (``output_cache.render_options_tag``) and the single
source of the build keyword arguments, so what is keyed is exactly what is built
with. Stdlib-light like :mod:`lvkit.output_cache`: building a key on a cache hit
imports nothing heavy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .load_mode import LoadMode


@dataclass(frozen=True)
class BuildOptions:
    """Search paths (in order), vi.lib / user.lib roots, load mode and project
    store directory of one build."""

    search_paths: tuple[Path, ...] = ()
    vilib_root: Path | None = None
    userlib_root: Path | None = None
    mode: LoadMode = LoadMode.MINIMAL
    project_store: Path | None = None

    def tag(self) -> str:
        """The key fragment: every field, spelling-independent (resolved paths)."""

        def _one(p: Path | None) -> str:
            return str(p.resolve()) if p is not None else ""

        return (
            f"search={';'.join(_one(p) for p in self.search_paths)}"
            f"|vilib={_one(self.vilib_root)}|userlib={_one(self.userlib_root)}"
            f"|mode={self.mode.name}|store={_one(self.project_store)}"
        )

    def build_kwargs(self) -> dict[str, Any]:
        """The keyword arguments ``render_body`` / ``diff_vi_files`` take."""
        return {
            "search_paths": list(self.search_paths) or None,
            "vilib_root": self.vilib_root,
            "userlib_root": self.userlib_root,
            "mode": self.mode,
        }
