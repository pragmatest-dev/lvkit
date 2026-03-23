"""vipy.graph — In-memory VI graph package.

Re-exports InMemoryVIGraph and connect() for convenient access.
"""

from .core import InMemoryVIGraph, connect

__all__ = ["InMemoryVIGraph", "connect"]
