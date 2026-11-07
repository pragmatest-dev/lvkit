"""The VI a SubVI call node runs, as its code is written.

A call of a polymorphic VI runs one of its variants (``call_variant``): the one
its caller records, else the one whose connector pane the call's recorded types
match. Its code is written from a copy of the node carrying that variant, so its
catalog entry, connector pane and parameter names are the variant's; the graph
keeps the call as its caller records it. A call of a polymorphic VI with no
catalog Python that runs no variant has nothing to call: ``RunCall.gap`` says
why."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lvkit.catalogs import vilib_catalog_for
from lvkit.graph.models import VINode
from lvkit.vilib_resolver import VILibResolver

from .context import CodeGenContext
from .import_resolver import ImportResolver


@dataclass(frozen=True)
class RunCall:
    """A call node as its code is written: the node (a copy carrying the variant
    it runs, when the caller records none), and the name and key of the VI whose
    function it calls; ``gap`` is the reason it calls nothing, else None."""

    node: VINode
    name: str
    key: str | None
    gap: str | None = None


def vilib_resolver(ctx: CodeGenContext | None) -> VILibResolver:
    """The vi.lib resolver this run reads: its own copy once it has learned, else
    the catalog of the run."""
    if ctx is None:
        return vilib_catalog_for(None)
    return ctx.learned_vilib[0] if ctx.learned_vilib else ctx.vilib_catalog


def run_call(node: VINode, ctx: CodeGenContext) -> RunCall:
    """``node`` as its code is written (see the module docstring). A variant is
    named by its own key. A call whose VI the caller records only by path (a
    ``<vilib>`` or ``<extravilib>`` VI with no configured root) names the absent
    VI by its qualified name, the identity its stub is keyed by."""
    graph = ctx.graph
    variant = graph.call_variant(node) if graph is not None else node.variant_key
    if variant is not None:
        name = graph.vi_display_name(variant) if graph is not None else variant
        run = node.model_copy(update={"variant_key": variant})
        return RunCall(run, Path(name.rsplit(":", 1)[-1]).name, variant)
    if node.callee_key is None and node.qualified_path and node.qualified_name:
        return RunCall(node, node.qualified_name, None)
    return RunCall(node, node.name or "", node.callee_key, _no_variant(node, ctx))


def _no_variant(node: VINode, ctx: CodeGenContext) -> str | None:
    """Why a call of a polymorphic VI with no catalog Python, that runs no
    variant, calls nothing; None for any other call."""
    graph, key = ctx.graph, node.callee_key
    if graph is None or key is None or not graph.get_poly_variants(key):
        return None
    if vilib_resolver(ctx).has_python(graph.recorded_path(key)):
        return None
    matches = graph.pane_matches(node)
    head = f"The call of the polymorphic VI {_shown(ctx, key)} records no variant"
    if matches:
        listed = ", ".join(_shown(ctx, v) for v in matches)
        return (
            f"{head}, and the connector panes of several of its variants match "
            f"this call: {listed}"
        )
    listed = ", ".join(_shown(ctx, v) for v in graph.get_poly_variants(key))
    return (
        f"{head}, and no variant's connector pane matches this call; its "
        f"variants: {listed}"
    )


def _shown(ctx: CodeGenContext, key: str) -> str:
    resolver = ctx.import_resolver
    return (
        resolver.shown(key) if isinstance(resolver, ImportResolver) else Path(key).name
    )
