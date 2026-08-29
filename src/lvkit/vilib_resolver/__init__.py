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

from pathlib import Path

from .exceptions import ResolutionContext, VILibConflict, VILibResolutionNeeded
from .models import VIEntry, VITerminal
from .naming import derive_python_location, derive_python_name
from .resolver import VILibResolver

__all__ = [
    "ResolutionContext",
    "VIEntry",
    "VILibConflict",
    "VILibResolutionNeeded",
    "VILibResolver",
    "VITerminal",
    "derive_python_location",
    "derive_python_name",
    "get_resolver",
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
