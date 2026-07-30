"""The CLI startup / cache-hit path must not import the heavy engine.

A render/diff CACHE HIT is served without building, so it has to reach the cache
lookup WITHOUT importing networkx / pylabview / pydantic / PIL (~230 ms). These
tests pin that invariant: importing each light entry point in a FRESH
interpreter must leave the heavy modules unloaded. If someone re-adds a
module-level ``from .graph import ...`` (or similar) to ``cli.py`` /
``lvkit/__init__.py``, this fails — the fast path would silently regress.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# The modules that pull the ~230 ms engine — forbidden on the light path.
_HEAVY = ("networkx", "pylabview", "pydantic", "PIL")

# Entry points a cache HIT (or a bare CLI dispatch) imports; none may pull heavy.
_LIGHT_ENTRYPOINTS = [
    "lvkit",
    "lvkit.cli",
    "lvkit.load_mode",
    "lvkit.cache_paths",
    "lvkit.output_cache",
]


@pytest.mark.parametrize("entry", _LIGHT_ENTRYPOINTS)
def test_light_entrypoint_does_not_import_engine(entry: str) -> None:
    code = (
        f"import {entry}, sys; "
        f"heavy=[m for m in {_HEAVY!r} if m in sys.modules]; "
        "print(','.join(heavy)); "
        "sys.exit(1 if heavy else 0)"
    )
    res = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
    )
    assert res.returncode == 0, (
        f"importing {entry!r} pulled the heavy engine "
        f"({res.stdout.strip()!r}) — the cache-hit path would import it too.\n"
        f"{res.stderr}"
    )


def test_loadmode_available_both_ways() -> None:
    """``LoadMode`` moved to the light ``load_mode`` leaf, re-exported from
    ``graph.loading``; both spellings must resolve to the SAME enum (the CLI
    imports the light one; existing call sites keep the graph.loading one)."""
    from lvkit.graph.loading import LoadMode as viaGraph
    from lvkit.load_mode import LoadMode as viaLeaf

    assert viaGraph is viaLeaf
    assert [m.value for m in viaLeaf] == ["none", "minimal", "full"]
