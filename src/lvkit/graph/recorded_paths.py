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
smaller joined path (compared without regard to case, then as recorded) is the
file's, so the answer does not depend on the order the references were loaded in.
A file no reference names from an installation folder has no resolved path; a
path a caller records relative to itself (``recorded_by``) is only a place beside
that caller.
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


def _order(tokens: list[str]) -> tuple[str, str]:
    """How two paths for one file compare: the joined path without regard to case
    (a Windows file system names one file whatever the case), then as recorded, so
    two recordings that differ only in case still give one answer."""
    joined = "/".join(tokens)
    return joined.casefold(), joined


class RecordedPaths:
    """The recorded paths of one graph's dependencies, by dependency key."""

    def __init__(self, dep_graph: nx.DiGraph) -> None:
        self._dep_graph = dep_graph
        self._references: dict[str, set[_Reference]] = {}
        # The round a key settled in and its resolved tokens (round 0 and None for
        # a key with no path), valid while the references and the ownership edges
        # are as they were (``_stamp``).
        self._settled: dict[str, tuple[int, list[str] | None]] = {}
        self._stamp: tuple[int, int] = (-1, -1)
        self._recorded = 0
        self._owners = 0

    def clear(self) -> None:
        self._references.clear()
        self._settled.clear()
        self._stamp = (-1, -1)
        self._recorded = 0
        self._owners = 0

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

    def owners_changed(self) -> None:
        """The loader added an ownership edge (a class or library to a member): the
        paths settled so far may change."""
        self._owners += 1

    def resolved(self, key: str) -> list[str] | None:
        """The installation-folder path of ``key``: the smallest (``_order``) one
        any reference, or the container that owns it, gives it; None when no
        reference names it from an installation folder, directly or through the
        caller or container that does.

        A path a caller records relative to itself is resolved against the
        caller's installation path, so what decides a key's path is the keys
        above it (its callers and its containers). They are settled in rounds: in
        each, a key without a path takes the smallest path its references and
        containers give it from the keys settled in the earlier rounds, so the
        result is a function of the references alone, whatever order they were
        recorded in, and every key settles at most once, so the rounds end. A
        file a reference names from an installation folder settles in the first
        round, from those references alone."""
        stamp = (self._recorded, self._owners)
        if stamp != self._stamp:
            self._settled.clear()
            self._stamp = stamp
        if key not in self._settled:
            self._settle(key)
        return self._settled[key][1]

    def path(self, key: str) -> str | None:
        """``resolved`` joined with ``/``."""
        tokens = self.resolved(key)
        return "/".join(tokens) if tokens else None

    def recorded_by(self, key: str, caller: str) -> list[str] | None:
        """The path ``caller`` recorded for ``key``, as recorded (relative to
        ``caller`` unless it starts at an installation folder): the smallest when
        it recorded several. For placing a file relative to that caller on disk."""
        tokens = [
            list(r.tokens) for r in self._references.get(key, ()) if r.caller == caller
        ]
        return min(tokens, key=_order) if tokens else None

    def _sources(self, key: str) -> list[str]:
        """The keys whose paths decide ``key``'s: its callers and its owners."""
        callers = {r.caller for r in self._references.get(key, ()) if r.caller}
        return sorted(callers | set(self._containers_of(key)))

    def _settle(self, key: str) -> None:
        above: set[str] = set()
        pending = [key]
        while pending:
            current = pending.pop()
            if current in above:
                continue
            above.add(current)
            pending.extend(s for s in self._sources(current) if s not in self._settled)
        values: dict[str, tuple[int, list[str]]] = {}
        unsettled = sorted(above)
        # A key settled by an earlier query keeps its round, and is usable by a
        # key below it only from the next round, as if it had settled just now.
        last = max((round_ for round_, _ in self._settled.values()), default=0)
        round_ = 1
        while unsettled:
            update = {k: self._candidate(k, values, round_) for k in unsettled}
            gained = {k: v for k, v in update.items() if v is not None}
            if not gained and round_ > last:
                break
            values.update({k: (round_, v) for k, v in gained.items()})
            unsettled = [k for k in unsettled if k not in gained]
            round_ += 1
        self._settled.update(dict.fromkeys(above, (0, None)))
        self._settled.update(values)

    def _candidate(
        self, key: str, values: dict[str, tuple[int, list[str]]], round_: int
    ) -> list[str] | None:
        """The installation path ``key`` gets in round ``round_`` from the paths the
        keys above it settled in the rounds before."""

        def of(source: str) -> list[str] | None:
            settled = values[source] if source in values else self._settled.get(source)
            return settled[1] if settled is not None and settled[0] < round_ else None

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
        return min(installed, key=_order) if installed else None

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
