"""Context building for LLM prompts.

This module re-exports all context-related functions for backward compatibility.
The actual implementations are split across:
- context_templates.py: LLM prompt templates (FUNCTION_TEMPLATE, METHOD_TEMPLATE, etc.)
- context_builder.py: ContextBuilder class with build methods
"""

from __future__ import annotations

from dataclasses import dataclass

# Re-export templates
from .context_templates import (
    FUNCTION_TEMPLATE,
    METHOD_TEMPLATE,
    UI_WRAPPER_TEMPLATE,
)

# Re-export ContextBuilder
from .context_builder import ContextBuilder


@dataclass
class VISignature:
    """Signature info for a VI (for SubVI imports)."""

    name: str
    module_name: str
    function_name: str
    signature: str  # e.g., "def calculate(a: float) -> float"
    import_statement: str  # e.g., "from .calculate import calculate"


__all__ = [
    # Dataclass
    "VISignature",
    # Templates
    "FUNCTION_TEMPLATE",
    "METHOD_TEMPLATE",
    "UI_WRAPPER_TEMPLATE",
    # Builder
    "ContextBuilder",
]
