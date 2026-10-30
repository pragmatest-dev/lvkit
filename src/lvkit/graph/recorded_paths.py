"""The path each dependency's callers recorded for it, resolved once.

A caller records the path of every VI, class, library or control it references
(``LinkSavePathRef``). A path that starts at an installation folder
(``<vilib>/Utility/sysdir.llb/Get System Directory.vi``) names the file the same
for every caller; a path relative to the caller (``['', 'helper.vi']``) names the
file beside the caller, so it is only a place once the caller's own path is known.

Loading resolves each recorded path against its caller's resolved path as the
dependency is reached, top-down: a project VI has none, the VIs it references by
an installation path have one, and what those reference relative to themselves
resolves against it. The resolved path of a key is the one value placement
(``module_layout``), the docs groups and the vi.lib catalog lookup read.

Several references can resolve one file to different installation paths; the
smaller joined path is the file's, decided from every reference, so the answer
does not depend on the order the references were loaded in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from ..parser.models import is_symbolic_root
from .node_kinds import EdgeRel

_CONTAINER_SUFFIXES = (".lvclass", ".lvlib")


def redirect_member_beside_container(cand: Path, leaf: str) -> Path:
    """Redirect a resolved dependency path from its OWNING container to the
    member's actual on-disk sibling.

    A class/library MEMBER's LinkSavePathRef tokens describe the owning
    container (e.g. ``['', 'TestCase.lvclass']``); the member's own filename
    is carried separately as ``leaf``, NOT in the tokens. So resolving those
    tokens yields the ``.lvclass``/``.lvlib`` file itself, which is not the
    member's file. Member files are stored BESIDE their container, so when
    ``cand`` is a ``.lvclass``/``.lvlib`` that isn't ``leaf`` itself, the member
    is the sibling ``container_dir/leaf``. Returns ``cand`` unchanged otherwise.
    Pure path math: it does not check that the file exists.
    """
    if cand.suffix.lower() in _CONTAINER_SUFFIXES and leaf != cand.name:
        return cand.with_name(leaf)
    return cand


def member_tokens(tokens: list[str], leaf: str) -> list[str]:
    """The tokens of a dependency's own file: a class or library member's recorded
    tokens end at its owning container, and the member sits beside it (see
    ``redirect_member_beside_container``)."""
    if not tokens:
        return []
    return [*tokens[:-1], redirect_member_beside_container(Path(tokens[-1]), leaf).name]


def resolve_against(tokens: list[str], caller: list[str] | None) -> list[str]:
    """``tokens`` (the path a caller recorded) made to start at an installation
    folder when they are relative to a caller that has an installation path
    (``caller``): each leading empty token pops one level of the caller's path,
    ``..`` pops another, so ``['', 'B.vi']`` recorded by
    ``<userlib>/_OpenG.lib/array/array.llb/A.vi`` is
    ``<userlib>/_OpenG.lib/array/array.llb/B.vi``. Tokens that already start at an
    installation folder, or whose caller has none, are returned unchanged."""
    if not tokens or is_symbolic_root(tokens[0]) or not caller:
        return list(tokens)
    empties = 0
    while empties < len(tokens) and tokens[empties] == "":
        empties += 1
    if empties == 0 or empties >= len(caller):
        return list(tokens)
    base = caller[:-empties]
    for token in tokens[empties:]:
        if token == "..":
            if len(base) <= 1:
                return list(tokens)
            base.pop()
        elif token != ".":
            base.append(token)
    return base


@dataclass(frozen=True)
class _Reference:
    """One recorded path: the key of the caller that recorded it (None when the
    reference has no caller in the graph) and its tokens."""

    caller: str | None
    tokens: tuple[str, ...]


class RecordedPaths:
    """The recorded paths of one graph's dependencies, by dependency key."""

    def __init__(self, dep_graph: nx.DiGraph) -> None:
        self._dep_graph = dep_graph
        self._references: dict[str, set[_Reference]] = {}
        # Resolved tokens by key, valid while the references and the graph's
        # edges are as they were (``_stamp``).
        self._settled: dict[str, list[str] | None] = {}
        self._stamp: tuple[int, int] = (-1, -1)
        self._recorded = 0

    def clear(self) -> None:
        self._references.clear()
        self._settled.clear()
        self._stamp = (-1, -1)
        self._recorded = 0

    def record(self, key: str, caller: str | None, tokens: list[str]) -> None:
        """Remember that ``caller`` recorded ``tokens`` for the file ``key`` (a
        class or library member's tokens already end at the member)."""
        if not tokens:
            return
        reference = _Reference(caller if caller != key else None, tuple(tokens))
        references = self._references.setdefault(key, set())
        if reference not in references:
            references.add(reference)
            self._recorded += 1

    def resolved(self, key: str) -> list[str] | None:
        """The resolved tokens of ``key``: the smallest installation-folder path
        any reference (or the container that owns it) gives it; else, for a file a
        caller records only relative to itself, the smallest such path; None when
        nothing was recorded.

        What decides a key's path is only the keys above it (its callers and its
        container), so they are settled together: every key starts without a path
        and is recomputed from its sources' paths of the previous round until no
        path changes. The result is a function of the references alone, whatever
        order they were recorded in."""
        stamp = (self._recorded, self._dep_graph.number_of_edges())
        if stamp != self._stamp:
            self._settled.clear()
            self._stamp = stamp
        if key not in self._settled:
            self._settle(key)
        return self._settled[key]

    def path(self, key: str) -> str | None:
        """``resolved`` joined with ``/``."""
        tokens = self.resolved(key)
        return "/".join(tokens) if tokens else None

    def _sources(self, key: str) -> list[str]:
        """The keys whose paths decide ``key``'s: its callers and its owners."""
        callers = {r.caller for r in self._references.get(key, ()) if r.caller}
        return sorted(callers | set(self._containers_of(key)))

    def _settle(self, key: str) -> None:
        above: list[str] = []
        seen = {key}
        pending = [key]
        while pending:
            current = pending.pop()
            above.append(current)
            for source in self._sources(current):
                if source not in seen and source not in self._settled:
                    seen.add(source)
                    pending.append(source)
        above.sort()
        values: dict[str, list[str] | None] = dict.fromkeys(above)
        for _ in range(len(above) + 1):
            update = {k: self._candidate(k, values) for k in above}
            if update == values:
                break
            values = update
        self._settled.update(values)

    def _candidate(
        self, key: str, values: dict[str, list[str] | None]
    ) -> list[str] | None:
        """The path ``key`` gets from the paths ``values`` of the keys above it."""

        def of(source: str) -> list[str] | None:
            return values[source] if source in values else self._settled[source]

        candidates: list[list[str]] = []
        for reference in self._references.get(key, ()):
            caller = of(reference.caller) if reference.caller else None
            candidates.append(resolve_against(list(reference.tokens), caller))
        for container in self._containers_of(key):
            tokens = of(container)
            if tokens and is_symbolic_root(tokens[0]):
                below = Path(key).relative_to(Path(container).parent).parts
                candidates.append([*tokens[:-1], *below])
        installed = [c for c in candidates if is_symbolic_root(c[0])]
        pool = installed or candidates
        return min(pool) if pool else None

    def _containers_of(self, key: str) -> list[str]:
        """The class or library files that own ``key`` and sit above it."""
        if key not in self._dep_graph:
            return []
        owners = []
        for container in self._dep_graph.predecessors(key):
            if self._dep_graph.edges[container, key].get("rel") != EdgeRel.OWNS:
                continue
            try:
                Path(key).relative_to(Path(container).parent)
            except ValueError:
                continue
            owners.append(container)
        return owners
