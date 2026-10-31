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

import copy
import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import ParamSpec, TypeVar

from .exceptions import CatalogPaneMismatch, ResolutionContext, VILibResolutionNeeded
from .models import VIEntry, VITerminal
from .naming import derive_python_location, derive_python_name
from .resolver import VILibResolver

__all__ = [
    "CatalogPaneMismatch",
    "ResolutionContext",
    "VIEntry",
    "VILibResolutionNeeded",
    "VILibResolver",
    "VITerminal",
    "derive_python_location",
    "derive_python_name",
    "get_resolver",
    "own_resolver",
    "reset_resolver",
    "run_in_own_resolver",
]

# Module-level singleton
_resolver: VILibResolver | None = None


# The resolver of the generation run in progress: what a run learns from the
# calls it generates (typedefs, polymorphic variants, terminal numbers) stays in
# it and is gone when the run ends.
_run_resolver: ContextVar[VILibResolver | None] = ContextVar(
    "run_resolver", default=None
)

_Params = ParamSpec("_Params")
_Result = TypeVar("_Result")


@contextmanager
def own_resolver() -> Iterator[VILibResolver]:
    """Run with a copy of the current resolver that ``get_resolver`` returns until
    the block ends."""
    token = _run_resolver.set(copy.deepcopy(get_resolver()))
    try:
        run = _run_resolver.get()
        assert run is not None
        yield run
    finally:
        _run_resolver.reset(token)


def run_in_own_resolver(
    function: Callable[_Params, _Result],
) -> Callable[_Params, _Result]:
    """``function`` run inside ``own_resolver``."""

    @functools.wraps(function)
    def wrapper(*args: _Params.args, **kwargs: _Params.kwargs) -> _Result:
        with own_resolver():
            return function(*args, **kwargs)

    return wrapper


def get_resolver() -> VILibResolver:
    """Get the resolver of the run in progress, else the global VILibResolver
    instance."""
    run = _run_resolver.get()
    if run is not None:
        return run
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
