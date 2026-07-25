"""MCP tool-handler smoke tests against the relocated extraction cache (#78).

Drives the in-process ``lvkit.mcp.server.call_tool`` dispatcher for all 12 tools
against a real sample VI. The autouse ``_hermetic_cache`` fixture points the
extraction cache at a per-test tmp dir, so this also confirms the MCP path never
writes into the repo. This is a smoke + wiring check: every tool returns without
raising and produces non-empty text.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lvkit.mcp import server as srv

pytestmark = pytest.mark.needs_samples

SAMPLE = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
)


def _call(name: str, **arguments: object) -> str:
    """Invoke an MCP tool handler in-process, returning its first text block."""
    result = asyncio.run(srv.call_tool(name, dict(arguments)))  # type: ignore[arg-type]
    assert result, f"{name} returned no content"
    return result[0].text


def test_all_mcp_tools_against_new_cache(tmp_path: Path) -> None:
    if not SAMPLE.exists():
        pytest.skip(f"sample VI not available: {SAMPLE}")

    # Start from a clean global graph (persists across tests otherwise).
    _call("clear")

    # load
    loaded = json.loads(_call("load", vi_path=str(SAMPLE)))["loaded_vis"]
    assert loaded

    # Canonical internal VI name (file stem may differ from the VI's own name).
    vi_name = srv._get_graph().resolve_vi_name(SAMPLE.name)

    # list_loaded
    listed = json.loads(_call("list_loaded"))["loaded_vis"]
    assert vi_name in listed

    # get_context (JSON) — pick an operation id for get_structure.
    ctx = json.loads(_call("get_context", vi_name=vi_name))
    assert ctx["operations"] or ctx["inputs"] or ctx["outputs"]

    # Read-only description tools.
    assert _call("describe", vi_name=vi_name)
    assert _call("get_operations", vi_name=vi_name)
    assert _call("get_dataflow", vi_name=vi_name)
    assert _call("get_constants", vi_name=vi_name)

    # get_structure needs a real operation id; pull the first from the graph.
    context = srv._get_graph().get_vi_context(vi_name)
    if context.operations:
        op_id = context.operations[0].id
        assert _call("get_structure", vi_name=vi_name, operation_id=op_id)

    # generate_ast_code (stateful; catches its own errors -> always text).
    assert _call("generate_ast_code", vi_name=vi_name)

    # Stateless generate paths — write into tmp, never the repo.
    py_out = tmp_path / "py"
    py_result = _call(
        "generate_python",
        vi_path=str(SAMPLE),
        output_dir=str(py_out),
        soft_unresolved=True,
    )
    assert py_result

    docs_out = tmp_path / "docs"
    docs_result = _call(
        "generate_documents",
        library_path=str(SAMPLE),
        output_dir=str(docs_out),
    )
    assert docs_result

    # clear tears the graph down again.
    assert "cleared" in _call("clear").lower()

    # Nothing leaked into the source tree.
    assert not (SAMPLE.parent / ".lvkit" / "cache").exists()
