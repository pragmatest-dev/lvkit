"""lvkit.graph — In-memory VI graph package.

Re-exports InMemoryVIGraph, connect(), and LoadMode for convenient access.
"""

from .core import InMemoryVIGraph, connect
from .loading import LoadMode, load_vi_by_path
from .queries import AmbiguousVIReferenceError

VIGraph = InMemoryVIGraph

__all__ = [
    "AmbiguousVIReferenceError",
    "InMemoryVIGraph",
    "LoadMode",
    "VIGraph",
    "connect",
    "load_vi_by_path",
]
