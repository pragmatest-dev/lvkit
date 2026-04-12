"""lvkit.graph — In-memory VI graph package.

Re-exports InMemoryVIGraph and connect() for convenient access.
"""

from .core import InMemoryVIGraph, connect

VIGraph = InMemoryVIGraph

__all__ = ["InMemoryVIGraph", "VIGraph", "connect"]
