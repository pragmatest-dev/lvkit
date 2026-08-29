"""Tests for `read_vi(format="lvnet")` -- the MCP mirror of
`lvkit describe --format lvnet` (Element 5).

Default `format="json"` behavior is already covered by
`tests/test_mcp_cache.py`; this file only exercises the new `format`/
`verbose` parameters, checked against `render_lvnet`/`build_netlist_from_graph`
called directly on the same graph.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.mcp import server as srv

pytestmark = pytest.mark.needs_samples

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_GOLDEN_VI = _JKI_SOURCE_ROOT / "Classes" / "TestLoader" / "loadTestsFromTestCase.vi"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _expected(*, verbose: bool) -> str:
    # Reuse read_vi's own loader (`_load_one`) rather than re-deriving the
    # graph load by hand, so the dependency-path root resolution (which
    # includes the VI's own parent dir ahead of `search_paths`) matches
    # EXACTLY what `read_vi` itself does.
    graph, vi_name = srv._load_one(str(_GOLDEN_VI), [str(_JKI_SOURCE_ROOT)])
    module = build_netlist_from_graph(graph, vi_name)
    return render_lvnet(
        module, display_name=graph.vi_display_name(vi_name), verbose=verbose
    )


def test_read_vi_format_lvnet_terse() -> None:
    if not _GOLDEN_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    result = _run(
        srv.read_vi(
            str(_GOLDEN_VI), search_paths=[str(_JKI_SOURCE_ROOT)], format="lvnet"
        )
    )
    assert result == {"lvnet": _expected(verbose=False)}


def test_read_vi_format_lvnet_verbose() -> None:
    if not _GOLDEN_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    result = _run(
        srv.read_vi(
            str(_GOLDEN_VI),
            search_paths=[str(_JKI_SOURCE_ROOT)],
            format="lvnet",
            verbose=True,
        )
    )
    expected = _expected(verbose=True)
    assert result == {"lvnet": expected}
    assert "types :" in expected


def test_read_vi_default_format_unchanged() -> None:
    """format defaults to "json" -- the pre-existing dict IR, byte-identical
    to before this change (covered structurally by test_mcp_cache.py; this
    just pins the default value itself)."""
    if not _GOLDEN_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    from lvkit.graph.netlist import build_netlist, netlist_to_dict

    graph, vi_name = srv._load_one(str(_GOLDEN_VI), [str(_JKI_SOURCE_ROOT)])
    expected = netlist_to_dict(build_netlist(graph, vi_name))

    result = _run(
        srv.read_vi(str(_GOLDEN_VI), search_paths=[str(_JKI_SOURCE_ROOT)])
    )
    assert result == expected
