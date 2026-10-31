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
import shutil
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from lvkit.mcp import server as srv

pytestmark = [pytest.mark.needs_samples, pytest.mark.cold_cache]

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

    # Deep single-VI: read_vi returns the canonical netlist IR dict — the single
    # structured read (operations, wiring, structures, constants) the AI
    # interprets to convert, not via a deterministic-generate MCP tool. There is
    # no `describe` tool: its prose is a lossy projection of read_vi, so it stays
    # CLI-only.
    ctx = _run(srv.read_vi(vi))
    assert isinstance(ctx, dict)
    assert ctx["inputs"] or ctx["outputs"] or ctx["body"]

    # The resolution axis: a library root (a user.lib the VI's recorded paths
    # might start from) is accepted by the reading tool, same as `unresolved`.
    # A root the VI records nothing under leaves the base result intact.
    assert _run(srv.read_vi(vi, userlib=str(tmp_path))) == ctx

    # render: writes the block-diagram HTML viewer and returns its PATH (not the
    # markup, which would flood context). The AI can't reconstruct LV geometry
    # from read_vi, so this is a tool; the viewer embeds the faithful SVG.
    r = _run(srv.render(vi))
    assert isinstance(r, dict) and r["bytes"] > 0
    render_file = Path(r["render_path"])
    assert render_file.exists() and "<svg" in render_file.read_text(encoding="utf-8")

    # diff: writes a visual HTML diff of two VI versions, returns its path.
    after = tmp_path / "after.vi"
    shutil.copy(SAMPLE, after)
    d = _run(srv.diff(vi, str(after)))
    assert isinstance(d, dict) and d["bytes"] > 0
    assert Path(d["diff_path"]).exists()

    # render + diff write into the HERMETIC cache (LVKIT_CACHE_DIR=tmp), never the
    # source tree; the understanding tools stay pure in-process reads.
    assert not (SAMPLE.parent / ".lvkit" / "cache").exists()


def test_render_and_diff_return_a_path_not_the_content(tmp_path: Path) -> None:
    """render/diff hand back a PATH + byte count, NEVER the SVG/HTML markup.

    The contract that keeps these tools usable: a block diagram is hundreds of KB,
    so inlining it into the tool result would flood the model's context (the
    regression where a client dumped a 318K SVG to a file by hand). This pins it:
    the response is tiny and markup-free, and the real content lives on disk.
    """
    if not SAMPLE.exists():
        pytest.skip(f"sample VI not available: {SAMPLE}")
    vi = str(SAMPLE)

    def _assert_path_not_content(
        resp: object, path_key: str, *, extra_keys: set[str]
    ) -> None:
        assert isinstance(resp, dict)
        # The response carries a path + a byte count + input-VI identification
        # (#114) — no markup, no blob key.
        assert set(resp) == {path_key, "bytes", "status", "problems", *extra_keys}
        blob = json.dumps(resp)
        assert "<svg" not in blob and "<html" not in blob.lower()
        # The real content is substantial and lives on disk, not in the response.
        body = Path(resp[path_key]).read_text(encoding="utf-8")
        assert "<svg" in body
        assert resp["bytes"] == len(body) > 10_000
        # And the response is orders of magnitude smaller than the content it
        # points at — proof the markup was NOT inlined.
        assert len(blob) * 20 < resp["bytes"]

    _assert_path_not_content(
        _run(srv.render(vi)), "render_path", extra_keys={"vi_name", "vi_path"}
    )

    after = tmp_path / "after.vi"
    shutil.copy(SAMPLE, after)
    _assert_path_not_content(
        _run(srv.diff(vi, str(after))), "diff_path", extra_keys={"before", "after"}
    )


_RUN_TESTCASE = TESTCASE_DIR / "run.vi"
_RUN_TESTSUITE = TESTCASE_DIR.parent / "TestSuite" / "run.vi"


def test_render_and_diff_identify_same_named_but_different_vis() -> None:
    """Two real, DIFFERENT VIs that share a bare filename (routine under
    LabVIEW dynamic dispatch: every class's override of a method is literally
    ``run.vi``) must still come back correctly and distinctly identified --
    proof for #114's follow-up that the qualified name (not the collision-
    prone bare filename) is what render/diff now report."""
    if not (_RUN_TESTCASE.exists() and _RUN_TESTSUITE.exists()):
        pytest.skip("JKI-VI-Tester run.vi pair not available")

    r1 = _run(srv.render(str(_RUN_TESTCASE)))
    r2 = _run(srv.render(str(_RUN_TESTSUITE)))
    assert r1["vi_name"] == "TestCase.lvclass:run.vi"
    assert r2["vi_name"] == "TestSuite.lvclass:run.vi"
    assert r1["vi_name"] != r2["vi_name"]  # NOT both the bare "run.vi"

    d = _run(srv.diff(str(_RUN_TESTCASE), str(_RUN_TESTSUITE)))
    assert d["before"]["name"] == "TestCase.lvclass:run.vi"
    assert d["after"]["name"] == "TestSuite.lvclass:run.vi"


def test_render_identity_on_a_cache_hit_never_loads_a_graph(monkeypatch) -> None:
    """The whole point of #114's follow-up: a cache HIT must report the real
    qualified name WITHOUT re-parsing the VI. Render once (a miss -- loads the
    graph normally), then break graph loading and render again: if the second
    call still returns the right name, it never touched the loader."""
    if not SAMPLE.exists():
        pytest.skip(f"sample VI not available: {SAMPLE}")
    vi = str(SAMPLE)

    first = _run(srv.render(vi))
    assert first["vi_name"]  # sanity: the miss actually captured a name

    from lvkit.graph import InMemoryVIGraph

    def _boom(self, *a, **kw):
        raise AssertionError("load_vi called on what should be a cache HIT")

    monkeypatch.setattr(InMemoryVIGraph, "load_vi", _boom)
    second = _run(srv.render(vi))
    assert second == first  # identical result, no graph load needed to get it


def test_index_tools() -> None:
    if not TESTCASE_DIR.exists():
        pytest.skip(f"sample class not available: {TESTCASE_DIR}")
    project = str(TESTCASE_DIR)

    # index() builds + persists; the other tools then read the facts.
    built = _run(srv.index(project))
    assert built["vis"] > 0
    assert built["loaded_alone"] == 0  # every VI came out of the whole-project load

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
