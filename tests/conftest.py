"""Shared test fixtures for vipy tests."""

from __future__ import annotations

import pytest

from vipy.agent.codegen.context import CodeGenContext

from .helpers import make_ctx, make_graph_with_terminals


@pytest.fixture
def graph_factory():
    """Fixture providing graph construction helpers."""
    return make_graph_with_terminals


@pytest.fixture
def ctx_with_terminals():
    """Fixture: create a CodeGenContext with a graph that has the given terminals.

    Usage: ctx = ctx_with_terminals("t1", "t2", "t3")
    """

    def _factory(*terminal_ids: str) -> CodeGenContext:
        return make_ctx(*terminal_ids)

    return _factory
