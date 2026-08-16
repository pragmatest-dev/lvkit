"""MCP tool smoke tests — project-scoped (index) + deep single-VI + stateless.

Drives the FastMCP tool functions in-process (``@mcp.tool()`` leaves each async
function directly awaitable) across all three groups against real samples. The
autouse ``_hermetic_cache`` fixture points the cache (extraction AND the index
DB) at a per-test tmp dir, so this also confirms the MCP path never writes into
the repo. Wiring/smoke check: every tool returns without raising, non-empty.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from lvkit.mcp import server as srv

pytestmark = pytest.mark.needs_samples

# A standalone VI for the deep single-VI tools.
SAMPLE = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
)
# A small class dir for the project-scoped index tools (no same-named siblings).
TESTCASE_DIR = Path(".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestCase")


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def test_deep_and_stateless_tools(tmp_path: Path) -> None:
    if not SAMPLE.exists():
        pytest.skip(f"sample VI not available: {SAMPLE}")
    vi = str(SAMPLE)

    # Deep single-VI: describe (prose) + read_vi (structured). The AI converts
    # by UNDERSTANDING these, not via a deterministic-generate MCP tool.
    assert _run(srv.describe(vi))

    # read_vi returns the canonical netlist IR dict — the single structured read
    # (operations, wiring, structures, constants) that subsumes the old
    # get_operations/get_dataflow/get_structure/get_constants facet tools.
    ctx = _run(srv.read_vi(vi))
    assert isinstance(ctx, dict)
    assert ctx["inputs"] or ctx["outputs"] or ctx["body"]

    # The resolution axis: an extra `search_paths` root (an out-of-tree library
    # the VI might call into) is accepted by the reading tools, same as
    # `unresolved`. A harmless extra root leaves the base result intact.
    assert _run(srv.describe(vi, search_paths=[str(tmp_path)]))
    assert _run(srv.read_vi(vi, search_paths=[str(tmp_path)])) == ctx

    # Nothing leaked into the source tree (the understanding tools are pure
    # in-process reads — no artifact generation, no scripts/ subprocess).
    assert not (SAMPLE.parent / ".lvkit" / "cache").exists()


def test_index_tools() -> None:
    if not TESTCASE_DIR.exists():
        pytest.skip(f"sample class not available: {TESTCASE_DIR}")
    project = str(TESTCASE_DIR)

    # index() builds + persists; the other tools then read the facts.
    built = _run(srv.index(project))
    assert built["vis"] > 0
    assert built["collisions"] == 0  # a single class dir has no name clashes

    # The SQL query surface subsumes the old find_*/get_signatures read tools:
    # schema introspection lists the views, and reads/aggregates go through it.
    schema = _run(srv.query_schema())
    assert {v["name"] for v in schema} >= {"vi", "terminal", "constant"}

    # Symbol/navigation read via SQL (replaces find_symbols): all VIs indexed.
    vis = _run(srv.query("SELECT path FROM vi ORDER BY path", project=project))
    assert vis["row_count"] == built["vis"]

    # The call graph is the node spine: direct callers of a VI are a query over
    # node.callee_path, and the precomputed vi.callers_count/impact_score columns
    # give the dead-code and change-impact signals (replacing the retired
    # get_callers/get_callees/blast_radius tools).
    a_method = vis["rows"][0][0]
    callers = _run(
        srv.query(
            f"SELECT DISTINCT vi_path FROM node WHERE callee_path='{a_method}'",
            project=project,
        )
    )
    assert "rows" in callers
    impact = _run(
        srv.query(
            "SELECT callers_count, impact_score FROM vi ORDER BY impact_score DESC",
            project=project,
        )
    )
    assert impact["row_count"] > 0

    # The error-indicator histogram: a GROUP BY returns the answer (columnar),
    # not the raw terminal rows the retired find_terminals dumped.
    qres = _run(
        srv.query(
            "SELECT name, COUNT(*) AS n FROM terminal "
            "WHERE type_descriptor = 'Error' AND direction = 'output' "
            "GROUP BY name ORDER BY n DESC",
            project=project,
        )
    )
    assert qres["columns"] == ["name", "n"]
    assert qres["truncated"] is False
    # A bad (non-SELECT) statement is refused loudly.
    with pytest.raises(srv.isql.QueryError):
        _run(srv.query("DELETE FROM vis", project=project))
