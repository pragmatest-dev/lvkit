"""lvkit code-understanding index.

A persistent, incrementally-maintained projection of a VI repo's graph — the
"tree-sitter for VIs" layer. Standalone and CLI-testable; the MCP server is a
thin wrapper over ``query`` added separately.

Design: docs/_internal/design/lvkit-mcp-index.md
Plan:   docs/_internal/design/lvkit-mcp-index-plan.md
"""

from __future__ import annotations

from .model import (
    ClassFact,
    ConstantFact,
    TerminalFact,
    VIFacts,
)

__all__ = ["VIFacts", "TerminalFact", "ConstantFact", "ClassFact"]
