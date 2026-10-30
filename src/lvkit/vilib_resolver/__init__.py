"""Resolver for LabVIEW vilib VIs (standard SubVIs from vi.lib).

Package layout:
- ``naming`` - Python-name derivation helpers for vilib typedefs
- ``exceptions`` - resolution exceptions + diagnostic context
- ``models`` - VIEntry / VITerminal pydantic catalog models
- ``loader`` - data-loading mixin (vilib/openg/drivers JSON catalog)
- ``lookup`` - terminal-index resolution/lookup mixin
- ``variants`` - polymorphic-variant discovery/matching mixin
- ``resolver`` - VILibResolver, composed from the mixins above

This module is the facade: every name previously importable from the flat
``lvkit.vilib_resolver`` module remains importable from here.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

from .exceptions import ResolutionContext, VILibResolutionNeeded
from .models import VIEntry, VITerminal
from .naming import derive_python_location, derive_python_name
from .resolver import VILibResolver

__all__ = [
    "ResolutionContext",
    "VIEntry",
    "VILibResolutionNeeded",
    "VILibResolver",
    "VITerminal",
    "derive_python_location",
    "derive_python_name",
    "get_resolver",
    "learns_in_own_scope",
    "reset_resolver",
]

# Module-level singleton
_resolver: VILibResolver | None = None


def get_resolver() -> VILibResolver:
    """Get the global VILibResolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = VILibResolver()
    return _resolver


def reset_resolver(project_data_dir: Path | None = None) -> None:
    """Replace the cached global resolver, optionally with a project store.

    Call this at CLI/MCP entry points after discovering a project's .lvkit/
    directory. Subsequent get_resolver() calls return a resolver that loads
    project data first and falls back to shipped data.

    Args:
        project_data_dir: Path to project's .lvkit/ directory, or None to
            reset to shipped-data-only.
    """
    global _resolver
    if project_data_dir is None:
        _resolver = None
    else:
        _resolver = VILibResolver(project_data_dir=project_data_dir)


_P = ParamSpec("_P")
_R = TypeVar("_R")


def learns_in_own_scope(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run ``func`` with a fresh resolver for the same project store.

    Generation learns terminals from the VIs it reads (see
    ``VILibResolver.auto_update_terminals``). That knowledge belongs to one run:
    the decorated call sees a resolver of its own, and the one in place before
    it (with whatever it holds) is back afterwards, so a later load, render or
    MCP call in the same process never sees what this run learned.
    """

    @functools.wraps(func)
    def scoped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        global _resolver
        outer = _resolver
        _resolver = VILibResolver(
            project_data_dir=outer.project_data_dir if outer else None
        )
        try:
            return func(*args, **kwargs)
        finally:
            _resolver = outer

    return scoped
