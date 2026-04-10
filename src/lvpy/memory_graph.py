"""Backward compat -- import from lvpy.graph instead."""

from .graph import InMemoryVIGraph, connect

__all__ = ["InMemoryVIGraph", "connect"]
