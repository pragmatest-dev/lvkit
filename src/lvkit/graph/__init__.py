"""lvkit.graph — In-memory VI graph package.

Re-exports InMemoryVIGraph, connect(), and LoadMode for convenient access.
"""

from .core import InMemoryVIGraph, connect
from .loading import LoadMode

VIGraph = InMemoryVIGraph

__all__ = ["InMemoryVIGraph", "LoadMode", "VIGraph", "connect"]
