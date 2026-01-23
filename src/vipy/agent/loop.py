"""Main conversion agent loop.

This module re-exports all conversion-related classes for backward compatibility.
The actual implementations are split across:
- loop_config.py: ConversionConfig, ConversionResult dataclasses
- loop_agent.py: ConversionAgent class
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export config dataclasses
from .loop_config import (
    ConversionConfig,
    ConversionResult,
    GraphType,
)

# Re-export agent class
from .loop_agent import ConversionAgent

if TYPE_CHECKING:
    from ..graph import VIGraph
    from ..memory_graph import InMemoryVIGraph
    from ..structure import LVClass, LVLibrary

__all__ = [
    # Type alias
    "GraphType",
    # Dataclasses
    "ConversionConfig",
    "ConversionResult",
    # Agent
    "ConversionAgent",
]
