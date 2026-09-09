"""Functional-correctness regression suite for deterministic codegen.

These tests GENERATE a corpus VI, EXECUTE the generated Python, and assert its
real output matches what the VI's block-diagram logic should produce — NOT a
structural/string check of the code. They are the guardrail for "don't break a
VI that already works": the generated implementation may change freely, but the
outcomes here must stay correct.

Run/skip as a block:
    uv run pytest -m functional            # only these
    uv run pytest -m 'not functional'      # everything else

Each `_run_leaf` VI must be a LEAF (no SubVI imports) so it execs standalone;
VIs with dependencies get a package-level case (see _run_pkg) once available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.ast_utils import to_function_name
from lvkit.codegen.builder import build_module
from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode

pytestmark = [pytest.mark.functional, pytest.mark.needs_samples]

CORPUS = Path(".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib")


def _run_leaf(vi_rel: str, *args: object) -> Any:
    """Generate a leaf VI, exec it, and call its entry function with args."""
    vi = CORPUS / vi_rel
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi_rel}")
    graph, name = load_vi_by_path(str(vi), LoadMode.FULL)
    ctx = graph.get_vi_context(name)
    code = build_module(ctx, name, graph=graph)
    if "from .." in code:
        pytest.skip(f"{vi_rel} has SubVI imports; not a leaf (needs package case)")
    ns: dict = {}
    exec(compile(code, f"<{vi_rel}>", "exec"), ns)  # noqa: S102
    fn = ns.get(to_function_name(name))
    assert callable(fn), f"entry function {to_function_name(name)} not defined"
    return fn(*args)


# --------------------------------------------------------------------------
# Cases. Add one per VI as it is confirmed working; keep assertions on the
# VALUE, so a codegen change that alters structure but preserves behavior passes.
# --------------------------------------------------------------------------


def test_random_number_within_range_is_in_bounds() -> None:
    """low + rand*(high-low) must land in [low, high]. (Non-deterministic → range.)"""
    for _ in range(25):
        r = _run_leaf(
            "numeric/numeric.llb/Random Number - Within Range__ogtk.vi", 0.0, 10.0
        )
        assert 0.0 <= r.random_number <= 10.0
