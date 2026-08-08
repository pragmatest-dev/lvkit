"""MCP tool smoke tests — project-scoped (index) + deep single-VI + stateless.

Drives the FastMCP tool functions in-process (``@mcp.tool()`` leaves each async
function directly awaitable) across all three groups against real samples. The
autouse ``_hermetic_cache`` fixture points the cache (extraction AND the index
DB) at a per-test tmp dir, so this also confirms the MCP path never writes into
the repo. Wiring/smoke check: every tool returns without raising, non-empty.
"""

from __future__ import annotations

import asyncio
import json
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

    # Deep single-VI tools all take a vi_path and load on demand.
    assert _run(srv.describe(vi))
    assert _run(srv.get_operations(vi))
    assert _run(srv.get_dataflow(vi))
    assert _run(srv.get_constants(vi))
    assert _run(srv.generate_ast_code(vi))

    ctx = json.loads(_run(srv.get_context(vi)))
    assert ctx["operations"] or ctx["inputs"] or ctx["outputs"]
    if ctx["operations"]:
        op_id = ctx["operations"][0]["id"]
        # Returns text even when the op isn't a structure; just must not raise.
        assert isinstance(_run(srv.get_structure(vi, op_id)), str)

    # Stateless generators write into tmp, never the repo.
    py_result = _run(
        srv.generate_python(vi, str(tmp_path / "py"), soft_unresolved=True)
    )
    assert py_result
    docs_result = _run(srv.generate_documents(vi, str(tmp_path / "docs")))
    assert docs_result

    # Nothing leaked into the source tree.
    assert not (SAMPLE.parent / ".lvkit" / "cache").exists()


def test_index_tools() -> None:
    if not TESTCASE_DIR.exists():
        pytest.skip(f"sample class not available: {TESTCASE_DIR}")
    project = str(TESTCASE_DIR)

    # index() builds + persists; the other tools then read the facts.
    built = _run(srv.index(project))
    assert built["vis"] > 0
    assert built["collisions"] == 0  # a single class dir has no name clashes

    syms = _run(srv.find_symbols(project))
    assert len(syms) == built["vis"]
    assert all("impact_score" in s for s in syms)

    # A class method exists; get_callers/blast_radius resolve a bare name.
    # (vi is the first, required arg; project is the optional workspace-root
    # default — call by keyword, exactly as an MCP client does.)
    a_method = syms[0]["path"]
    assert isinstance(_run(srv.get_callers(a_method, project=project)), list)
    assert isinstance(_run(srv.get_callees(a_method, project=project)), list)
    br = _run(srv.blast_radius(a_method, project=project))
    assert "impact_score" in br

    # Bulk reads.
    assert isinstance(_run(srv.find_terminals(project, direction="output")), list)
    assert isinstance(_run(srv.find_constants(project)), list)
    sigs = _run(srv.get_signatures(project))
    assert len(sigs) == built["vis"]

    # Mermaid visualization is self-contained text.
    mm = _run(srv.visualize_project(project, scope="calls"))
    assert mm.splitlines()[0] == "graph LR"
    assert _run(srv.visualize_project(project, scope="classes")).startswith(
        "classDiagram"
    )
