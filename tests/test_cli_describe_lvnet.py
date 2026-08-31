"""Tests for `lvkit describe --format lvnet` (Element 5: wiring `render_lvnet`
into the `describe` CLI).

Uses the same real corpus VI as `tests/test_render_lvnet.py`'s golden fixture
(`loadTestsFromTestCase.vi`, JKI VI-Tester sample) so the CLI's output can be
checked against the underlying `render_lvnet`/`build_netlist_from_graph`
functions directly, in-process -- not just "the subprocess didn't crash".

(The OLD `--format netlist` was struck -- lvnet is the only netlist text
surface now; `--format` no longer accepts `netlist`.)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.netlist import (
    build_netlist_from_graph,
    render_lvnet,
)
from lvkit.load_mode import LoadMode

_JKI_SOURCE_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
_GOLDEN_VI = _JKI_SOURCE_ROOT / "Classes" / "TestLoader" / "loadTestsFromTestCase.vi"


def _require_golden() -> None:
    if not _GOLDEN_VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")


def _load() -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(
        str(_GOLDEN_VI),
        LoadMode.MINIMAL,
        search_paths=[_JKI_SOURCE_ROOT],
        layout=False,
    )
    vi_name = graph.resolve_vi_name(_GOLDEN_VI.name)
    return graph, vi_name


def _run_describe(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BROWSER": "true"}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "lvkit.cli",
            "describe",
            str(_GOLDEN_VI),
            "--search-path",
            str(_JKI_SOURCE_ROOT),
            "--no-auto-vilib",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.needs_samples
def test_format_lvnet_terse_matches_render_lvnet() -> None:
    _require_golden()
    graph, vi_name = _load()
    module = build_netlist_from_graph(graph, vi_name)
    expected = render_lvnet(
        module, display_name=graph.vi_display_name(vi_name), verbose=False
    )

    result = _run_describe("--format", "lvnet")
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected + "\n"
    # Terse never inlines dependency interfaces or the types appendix.
    assert "types :" not in result.stdout


@pytest.mark.needs_samples
def test_format_lvnet_verbose_matches_render_lvnet_and_has_uses_and_types() -> None:
    _require_golden()
    graph, vi_name = _load()
    module = build_netlist_from_graph(graph, vi_name)
    expected = render_lvnet(
        module, display_name=graph.vi_display_name(vi_name), verbose=True
    )

    result = _run_describe("--format", "lvnet", "-v")
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected + "\n"
    assert "  uses :" in result.stdout
    assert "types :" in result.stdout


@pytest.mark.needs_samples
def test_format_lvnet_no_deprecation_notice() -> None:
    _require_golden()
    result = _run_describe("--format", "lvnet")
    assert result.returncode == 0, result.stderr
    assert "deprecated" not in result.stderr
