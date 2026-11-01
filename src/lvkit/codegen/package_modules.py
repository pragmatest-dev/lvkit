"""The modules a generated package defines for state that VIs share: the named
queues and the Shared Variables.

Both are answers to graph queries over the package's VIs, computed once before any
VI is generated; generating a VI only looks its node up.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from lvkit.graph import InMemoryVIGraph

from .ast_utils import unique_names
from .queue_module import QUEUES_MODULE, PackageQueues
from .shared_variable_module import SHARED_VARIABLES_MODULE, PackageSharedVariables


@dataclass(frozen=True)
class PackageModules:
    """The queues and Shared Variables of one package and the file stems of the
    modules that define them at the package root. The stems differ from the
    library folders (``reserved``) and from each other."""

    queues: PackageQueues
    shared_variables: PackageSharedVariables
    queues_stem: str = QUEUES_MODULE
    shared_variables_stem: str = SHARED_VARIABLES_MODULE
    reserved: Collection[str] = field(default=(), repr=False)

    @classmethod
    def from_graph(
        cls,
        graph: InMemoryVIGraph,
        vi_keys: Iterable[str],
        reserved: Collection[str] = (),
    ) -> PackageModules:
        """The shared modules of the package made of the VIs ``vi_keys``."""
        keys = list(vi_keys)
        held = sorted(reserved)
        stems = unique_names([*held, QUEUES_MODULE, SHARED_VARIABLES_MODULE])
        return cls(
            queues=PackageQueues.from_uses(graph.obtain_queue_uses(keys)),
            shared_variables=PackageSharedVariables.from_uses(
                graph.shared_variable_uses(keys)
            ),
            queues_stem=stems[len(held)],
            shared_variables_stem=stems[len(held) + 1],
            reserved=held,
        )

    def write(self, folder: Path) -> list[Path]:
        """Write each module that has a definition into ``folder``; the files
        written."""
        written: list[Path] = []
        for stem, source in (
            (self.queues_stem, self.queues.build_module()),
            (self.shared_variables_stem, self.shared_variables.build_module()),
        ):
            if source is not None:
                path = folder / f"{stem}.py"
                path.write_text(source, encoding="utf-8")
                written.append(path)
        return written
