"""Which variants of a polymorphic VI get generated code.

A variant is written in its wrapper's module, so only a variant something uses
needs it: a call of the wrapper that resolves to it (or that leaves the choice to
the wrapper, which then needs all of them), a call of it by name, or a reference to
it from a VI that is itself generated. A variant the catalog gives Python for is
never written: its calls use that Python.
"""

from __future__ import annotations

from collections.abc import Collection

from .graph import InMemoryVIGraph
from .vilib_resolver import VILibResolver


def variants_to_generate(
    graph: InMemoryVIGraph,
    vilib_resolver: VILibResolver,
    groups: dict[str, list[str]],
    written: Collection[str],
) -> dict[str, list[str]]:
    """Each wrapper in ``groups`` (wrapper key to its variant keys) with the
    variants that need generated code, in the order the wrapper records them.

    ``written`` are the VIs that get a module from their own diagram. A variant
    can be used by another variant, so the set of VIs that use variants grows with
    the variants chosen, until no further variant is chosen. A wrapper nothing
    calls (a library's public VI) keeps all its variants."""
    own = {
        wrapper: [
            v for v in variants if not vilib_resolver.has_python(graph.recorded_path(v))
        ]
        for wrapper, variants in groups.items()
    }
    calls = graph.poly_call_choices()
    wrappers = set(groups)
    needed: set[str] = {v for w, vs in own.items() if w not in calls for v in vs}
    users_of = {
        v: [u for u in graph.get_vi_dependents(v) if u not in wrappers]
        for vs in own.values()
        for v in vs
    }
    while True:
        using = {*written, *needed}
        chosen = set(needed)
        for wrapper, callers in calls.items():
            for caller, picks in callers.items():
                if caller not in using or wrapper not in own:
                    continue
                for pick in picks:
                    chosen.update(own[wrapper] if pick is None else [pick])
        chosen.update(v for v, users in users_of.items() if using.intersection(users))
        chosen.intersection_update(v for vs in own.values() for v in vs)
        if chosen == needed:
            break
        needed = chosen
    return {wrapper: [v for v in vs if v in needed] for wrapper, vs in own.items()}
