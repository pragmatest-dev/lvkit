"""Unified primitive resolver with multi-strategy lookup.

Lookup order:
1. primResID -> exact match from primitives.json
2. Name -> exact name match from primitives.json
3. Exact type signature match
4. Compatible type match (polymorphic/adapt-to-type)

This is a facade package: submodules split the responsibilities out
(exceptions, JSON-entry/result models, the resolver + its JSON catalog), but
every name below stays importable from ``lvkit.primitive_resolver`` exactly
as before the split.
"""

from __future__ import annotations

from pathlib import Path

from .exceptions import PrimitiveResolutionNeeded as PrimitiveResolutionNeeded
from .exceptions import TerminalResolutionNeeded as TerminalResolutionNeeded
from .models import NodeIcon as NodeIcon
from .models import PrimitiveEntry as PrimitiveEntry
from .models import PrimitiveTerminal as PrimitiveTerminal
from .models import ResolvedPrimitive as ResolvedPrimitive
from .models import _collect_icon as _collect_icon
from .models import _collect_imports as _collect_imports
from .resolver import PrimitiveResolver as PrimitiveResolver

# Global instance
_resolver: PrimitiveResolver | None = None


def get_resolver() -> PrimitiveResolver:
    """Get global resolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = PrimitiveResolver()
    return _resolver


def reset_resolver(project_data_dir: Path | None = None) -> None:
    """Replace the cached global resolver, optionally with a project store.

    Call this at CLI/MCP entry points after discovering a project's .lvkit/
    directory. Subsequent get_resolver() calls return a resolver that loads
    project data first and falls back to shipped data.

    Args:
        project_data_dir: Path to project's .lvkit/ directory, or None to
            reset to shipped-data-only (lazy re-init on next get_resolver).
    """
    global _resolver
    if project_data_dir is None:
        _resolver = None
    else:
        _resolver = PrimitiveResolver(project_data_dir=project_data_dir)


def resolve_primitive(
    prim_id: int | str | None = None,
    name: str | None = None,
    input_types: list[str] | None = None,
    output_types: list[str] | None = None,
) -> ResolvedPrimitive | None:
    """Convenience function for resolving primitives."""
    return get_resolver().resolve(prim_id, name, input_types, output_types)
