"""Backward compat -- import from vipy.graph instead."""

from .graph import InMemoryVIGraph, connect

__all__ = ["InMemoryVIGraph", "connect"]
