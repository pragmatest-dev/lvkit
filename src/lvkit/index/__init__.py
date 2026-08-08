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
from .sql import (
    QueryError,
    QueryResult,
    ViewInfo,
    describe_schema,
    error_indicator_histogram,
    run_query,
)

__all__ = [
    "VIFacts",
    "TerminalFact",
    "ConstantFact",
    "ClassFact",
    "run_query",
    "describe_schema",
    "error_indicator_histogram",
    "QueryResult",
    "QueryError",
    "ViewInfo",
]
