"""``LoadMode`` — how deep a load pulls in a VI's dependencies.

A standalone, dependency-free leaf module (just an ``Enum``) so the CLI and its
argparse setup can name the load modes WITHOUT importing the graph package
(``lvkit.graph`` eagerly pulls networkx/pylabview). ``graph.loading`` re-exports
this, so ``from lvkit.graph.loading import LoadMode`` keeps working unchanged.
"""

from __future__ import annotations

from enum import Enum


class LoadMode(Enum):
    """How deep a load pulls in a VI's dependencies. One knob, three states.

    ``NONE`` — the target VI ONLY. No SubVIs, no classes, no typedefs. SubVI
    calls resolve to nothing (render draws fallback boxes; codegen emits
    unresolved-call placeholders). The old ``expand_subvis=False``.

    ``MINIMAL`` — the target VI, its DIRECT SubVIs LEAF-loaded (their connector
    panes, so the caller's param-name hovers resolve — but not THEIR SubVIs),
    and the classes/typedefs its wires reference field-loaded (no class methods).
    The minimum set to FAITHFULLY render/diff/describe one VI: byte-identical
    render to FULL (verified across the corpus), typically 8-40x cheaper because
    the deep call tree collapses to its shallow direct fan-out.

    ``FULL`` — the entire transitive SubVI + class-method tree. Required by
    codegen, which compiles the whole call tree. The old ``expand_subvis=True``.
    """

    NONE = "none"
    MINIMAL = "minimal"
    FULL = "full"
